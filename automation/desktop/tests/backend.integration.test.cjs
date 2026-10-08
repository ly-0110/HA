const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const {spawn}=require('node:child_process');
const {Backend,wait,hasExited}=require('../src/backend.cjs');
const enabled=process.env.IOT_EXP_INTEGRATION==='1';
const resources=process.env.IOT_EXP_DESKTOP_RESOURCES || path.resolve(__dirname,'../build-resources');
const testBase=process.env.IOT_EXP_SMOKE_ROOT || path.resolve(__dirname,'../../runs/lifecycle-integration');
function paths(name){const base=path.join(testBase,name+'-'+Date.now());return {workspace:path.join(base,'workspace'),app_state:path.join(base,'state'),locks:path.join(base,'locks'),discovery_roots:[]};}
async function pythonProbe(backend,script,args=[]){
 return await new Promise((resolve,reject)=>{
  const child=spawn(backend.python,['-I','-B','-X','utf8','-c',script,...args],{env:backend.environment(),windowsHide:true});let output='',error='';
  child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>error+=chunk);
  child.once('error',reject);child.once('exit',code=>code===0?resolve(output.trim()):reject(Error('owned fixture probe failed: '+error)));
 });
}
async function eventually(check,timeout=15000){
 const deadline=Date.now()+timeout;let value;
 while(Date.now()<deadline){value=await check();if(value)return value;await wait(100);}
 throw Error('owned lifecycle condition timed out');
}
async function simulation(backend,name){
 const [task]=await backend.request({url:'/api/v1/tasks',method:'POST',body:JSON.stringify({client_request_id:name+'-'+backend.instance,tasks:[{template_id:'mi_desk_lamp_1s',repetitions:100,mode:'simulate',idle_min_seconds:0.3,idle_max_seconds:0.3,cooldown_seconds:0}]})});
 return await eventually(async()=>{const value=await backend.request({url:'/api/v1/tasks/'+task.id});return value.status==='running'&&value.process_start_token?value:false;});
}
async function ownedFixture(backend,task,role){
 const script=role==='capture'?'import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); print("ready",flush=True); time.sleep(180)':'import time; print("ready",flush=True); time.sleep(180)';
 const child=spawn(backend.python,['-I','-B','-X','utf8','-c',script],{env:backend.environment(),windowsHide:true,stdio:['ignore','pipe','ignore']});
 await new Promise((resolve,reject)=>{child.once('error',reject);child.stdout.once('data',data=>String(data).trim()==='ready'?resolve():reject(Error('owned fixture did not initialize')));});
 const token=await pythonProbe(backend,'from iot_exp.process_identity import process_start_token; import sys; print(process_start_token(int(sys.argv[1])))',[String(child.pid)]);
 assert.notEqual(token,'None');
 const owned={role,pid:child.pid,token,isolated_group:false};
 await pythonProbe(backend,'import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE tasks SET owned_processes_json=? WHERE id=?",(sys.argv[3],sys.argv[2])); c.commit()',[path.join(backend.paths.workspace,'state/console.sqlite3'),task.id,JSON.stringify([owned])]);
 return child;
}
async function stopRecordedWorker(backend,task){
 await pythonProbe(backend,'from iot_exp.process_cleanup import terminate_owned_tree; import sys; terminate_owned_tree(int(sys.argv[1]),sys.argv[2])',[String(task.pid),task.process_start_token]);
}
async function removeFixture(child){
 if(!hasExited(child)){child.kill();await new Promise(resolve=>hasExited(child)?resolve():child.once('exit',resolve));}
}
async function queryDatabase(backend,sql){
 return await new Promise((resolve,reject)=>{
  const db=path.join(backend.paths.workspace,'state/console.sqlite3');
  const script='import sqlite3,json; c=sqlite3.connect('+JSON.stringify(db)+'); c.row_factory=sqlite3.Row; print(json.dumps([dict(x) for x in c.execute('+JSON.stringify(sql)+')]))';
  const child=spawn(backend.python,['-c',script],{env:backend.environment(),windowsHide:true});let output='';
  child.stdout.on('data',chunk=>output+=chunk);child.on('error',reject);child.on('exit',code=>code===0?resolve(JSON.parse(output)):reject(Error('database probe failed')));
 });
}
test('private sidecar authenticates reads and safely drains a running simulation',{skip:!enabled,timeout:90000},async()=>{
 const backend=new Backend(resources,paths('drain'));await backend.start();
 try {
  assert.equal((await fetch(backend.base+'/api/v1/bootstrap')).status,403);
  const boot=await backend.request({url:'/api/v1/bootstrap'});assert.equal('control_token' in boot,false);
  const [task]=await backend.request({url:'/api/v1/tasks',method:'POST',body:JSON.stringify({client_request_id:'drain-'+backend.instance,tasks:[{template_id:'mi_desk_lamp_1s',repetitions:10,mode:'simulate',idle_min_seconds:0.3,idle_max_seconds:0.3,cooldown_seconds:0}]})});
  for(let i=0;i<100;i++){const current=await backend.request({url:'/api/v1/tasks/'+task.id});if(current.status==='running')break;await wait(100);}
  await backend.raw('/api/v1/desktop/drain',{method:'POST'});
  const denied=await backend.raw('/api/v1/tasks',{method:'POST',body:JSON.stringify({client_request_id:'denied-'+backend.instance,tasks:[{template_id:'mi_desk_lamp_1s',mode:'simulate'}]})});
  assert.equal(denied.status,409);
  await Promise.all([backend.stop(),backend.stop()]);const rows=await queryDatabase(backend,'SELECT status,error FROM tasks');
  assert.equal(rows[0].status,'cancelled');assert.equal(backend.child.exitCode,0);
  assert.equal(backend.state,'stopped');
 }finally{if(backend.child.exitCode===null)await backend.stop();}
});

test('sidecar crash safely cancels its live worker and restart does not resubmit',{skip:!enabled,timeout:90000},async()=>{
 const inputs=paths('sidecar-crash');const backend=new Backend(resources,inputs);await backend.start();
 const task=await simulation(backend,'sidecar-crash');
 try{
  backend.child.kill('SIGKILL');await new Promise(resolve=>hasExited(backend.child)?resolve():backend.child.once('exit',resolve));
  const rows=await eventually(async()=>{const values=await queryDatabase(backend,'SELECT id,status FROM tasks');return values.length===1&&values[0].status==='cancelled'?values:false;});
  assert.equal(rows[0].id,task.id);
  const restarted=new Backend(resources,inputs);await restarted.start();
  try{
   const tasks=await restarted.request({url:'/api/v1/tasks'});assert.equal(tasks.length,1);assert.equal(tasks[0].id,task.id);assert.equal(tasks[0].status,'cancelled');
   assert.equal((await restarted.request({url:'/api/v1/health'})).active_owned,0);
  }finally{await restarted.stop();}
 }finally{if(!hasExited(backend.child))await backend.stop();}
});

test('worker crash cleans its ledger child and preserves synthetic partial evidence',{skip:!enabled,timeout:90000},async()=>{
 const backend=new Backend(resources,paths('worker-crash'));await backend.start();let child;
 try{
  const task=await simulation(backend,'worker-crash');child=await ownedFixture(backend,task,'appium');
  const evidence=path.join(backend.paths.workspace,'state/synthetic-partial.pcapng');const bytes=Buffer.from('self-owned fixture; no network capture');fs.writeFileSync(evidence,bytes);
  await stopRecordedWorker(backend,task);
  await eventually(async()=>{const current=await backend.request({url:'/api/v1/tasks/'+task.id});return current.status==='interrupted'&&hasExited(child)?current:false;});
  assert.deepEqual(fs.readFileSync(evidence),bytes);assert.equal((await backend.request({url:'/api/v1/health'})).active_owned,0);
 }finally{if(child)await removeFixture(child);if(!hasExited(backend.child))await backend.stop();}
});

test('dead sidecar and worker recover a verified orphan without automatic rerun',{skip:!enabled,timeout:90000},async()=>{
 const inputs=paths('orphan-recovery');const backend=new Backend(resources,inputs);await backend.start();let child,restarted;
 try{
  const task=await simulation(backend,'orphan-recovery');child=await ownedFixture(backend,task,'appium');
  const evidence=path.join(backend.paths.workspace,'state/recovery-prefix.pcapng');const bytes=Buffer.from('synthetic retained evidence');fs.writeFileSync(evidence,bytes);
  backend.child.kill('SIGKILL');await stopRecordedWorker(backend,task);
  assert.equal(hasExited(child),false);
  restarted=new Backend(resources,inputs);await restarted.start();
  const tasks=await eventually(async()=>{const values=await restarted.request({url:'/api/v1/tasks'});return values.length===1&&values[0].status==='interrupted'&&hasExited(child)?values:false;});
  assert.equal(tasks[0].id,task.id);assert.equal(tasks[0].metadata.recovered_owner_instance,backend.instance);
  assert.deepEqual(fs.readFileSync(evidence),bytes);
  assert.equal((await restarted.request({url:'/api/v1/health'})).state,'ready');
 }finally{if(child)await removeFixture(child);if(restarted&&!hasExited(restarted.child))await restarted.stop();if(!hasExited(backend.child))await backend.stop();}
});

test('owned residual capture fixture requires an actual sixty second gate before force',{skip:!enabled,timeout:150000},async()=>{
 const inputs=paths('force-sixty-seconds');const backend=new Backend(resources,inputs);await backend.start();let child,restarted;
 try{
  const task=await simulation(backend,'force-sixty');child=await ownedFixture(backend,task,'capture');
  const evidence=path.join(backend.paths.workspace,'state/force-prefix.pcapng');const bytes=Buffer.from('synthetic buffered capture prefix');fs.writeFileSync(evidence,bytes);
  await stopRecordedWorker(backend,task);
  const stopping=await eventually(async()=>{const value=await backend.request({url:'/api/v1/tasks/'+task.id});return value.status==='stopping'&&value.stop_requested_at_ns?value:false;});
  await assert.rejects(backend.request({url:'/api/v1/tasks/'+task.id+'/force-stop',method:'POST'}),/60/);
  assert.equal(hasExited(child),false);
  while(Date.now()-stopping.stop_requested_at_ns/1e6<61000)await wait(500);
  assert.equal(hasExited(child),false,'safe stop must not implicitly force the fixture');
  await backend.request({url:'/api/v1/tasks/'+task.id+'/force-stop',method:'POST'});
  await eventually(async()=>hasExited(child));
  const stopped=await backend.request({url:'/api/v1/tasks/'+task.id});assert.equal(stopped.status,'interrupted');assert.deepEqual(fs.readFileSync(evidence),bytes);
  await backend.stop();restarted=new Backend(resources,inputs);await restarted.start();
  const tasks=await restarted.request({url:'/api/v1/tasks'});assert.equal(tasks.length,1);assert.equal(tasks[0].id,task.id);assert.equal(tasks[0].status,'interrupted');
  assert.deepEqual(fs.readFileSync(evidence),bytes);
 }finally{if(child)await removeFixture(child);if(restarted&&!hasExited(restarted.child))await restarted.stop();if(!hasExited(backend.child))await backend.stop();}
});
test('parent crash causes sidecar-owned cleanup without resubmission',{skip:!enabled,timeout:90000},async()=>{
 const inputs=paths('parent-crash');
 const modulePath=path.resolve(__dirname,'../src/backend.cjs');
 const script=`const {Backend,wait}=require(${JSON.stringify(modulePath)});(async()=>{const b=new Backend(${JSON.stringify(resources)},${JSON.stringify(inputs)});await b.start();const [t]=await b.request({url:'/api/v1/tasks',method:'POST',body:JSON.stringify({client_request_id:'crash-'+b.instance,tasks:[{template_id:'mi_desk_lamp_1s',repetitions:20,mode:'simulate',idle_min_seconds:0.3,idle_max_seconds:0.3,cooldown_seconds:0}]})});for(let i=0;i<100;i++){let r=await b.request({url:'/api/v1/tasks/'+t.id});if(r.status==='running')break;await wait(100);}process.stdout.write(JSON.stringify({python:b.python,task:t.id})+'\\n');process.exit(0);})().catch(e=>{console.error(e);process.exit(2)});`;
 const parent=spawn(process.execPath,['-e',script],{windowsHide:true});let output='';parent.stdout.on('data',data=>output+=data);
 await new Promise((resolve,reject)=>{parent.once('error',reject);parent.once('exit',code=>code===0?resolve():reject(Error('parent probe failed')));});
 const evidence=JSON.parse(output.trim());const stub={python:evidence.python,paths:inputs,environment:()=>process.env};let rows=[];
 for(let i=0;i<150;i++){rows=await queryDatabase(stub,'SELECT status FROM tasks');if(rows[0]?.status==='cancelled')break;await wait(200);}
 assert.equal(rows.length,1);assert.equal(rows[0].status,'cancelled');
});

test('desktop drain leaves an independently started packaged CLI running',{skip:!enabled,timeout:90000},async()=>{
 const inputs=paths('external-cli');const externalRoot=path.join(path.dirname(inputs.workspace),'external-cli');
 const backend=new Backend(resources,inputs);await backend.start();
 const child=spawn(backend.python,['-I','-B','-X','utf8','-m','iot_exp.cli','--resources',resources,'--workspace',externalRoot,'--app-state',path.join(externalRoot,'app-state'),'run','--dry-run','--repetitions','100','--seed','42','--session-id','external-cli'],{env:backend.environment(),windowsHide:true,stdio:['ignore','ignore','pipe']});
 let diagnostic='';child.stderr.on('data',data=>diagnostic+=data);
 try{
  const actions=path.join(externalRoot,'runs/sessions/external-cli/actions.jsonl');
  for(let i=0;i<100 && !fs.existsSync(actions);i++){assert.equal(child.exitCode,null,diagnostic);await wait(100);}
  assert.equal(fs.existsSync(actions),true,diagnostic);
  await backend.stop();assert.equal(child.exitCode,null,'desktop must not terminate independent CLI');
 }finally{
  if(backend.child.exitCode===null)await backend.stop();
  if(child.exitCode===null){child.kill();await new Promise(resolve=>child.once('exit',resolve));}
 }
});
