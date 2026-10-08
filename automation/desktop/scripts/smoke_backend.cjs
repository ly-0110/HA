const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
const {Backend,wait}=require('../src/backend.cjs');
(async()=>{
 const resources=process.env.IOT_EXP_DESKTOP_RESOURCES || path.resolve(__dirname,'../build-resources');
 const testRoot=process.env.IOT_EXP_SMOKE_ROOT || path.resolve(__dirname,'../../runs/backend-smoke');
 const backend=new Backend(resources,{workspace:path.join(testRoot,'workspace'),app_state:path.join(testRoot,'state'),locks:path.join(testRoot,'locks'),discovery_roots:[]});
 try {
  await backend.start();
  const unauthorized=await fetch(backend.base+'/api/v1/tasks');assert.equal(unauthorized.status,403);
  const boot=await backend.request({url:'/api/v1/bootstrap'});assert.equal(boot.desktop,true);assert.equal('control_token' in boot,false);
  const tasks=await backend.request({url:'/api/v1/tasks',method:'POST',body:JSON.stringify({client_request_id:'backend-smoke-'+backend.instance,tasks:[{template_id:'mi_desk_lamp_1s',mode:'simulate',repetitions:1,idle_min_seconds:0,idle_max_seconds:0,cooldown_seconds:0}]})});
  let task;
  for(let attempt=0;attempt<300;attempt++){task=await backend.request({url:'/api/v1/tasks/'+tasks[0].id});if(['completed','failed','interrupted'].includes(task.status))break;await wait(200);}
  assert.equal(task.status,'completed',task.error);assert.equal(task.completed_events,2);assert.equal(task.quality.validation.ok,true);
  const report=await backend.request({url:`/api/v1/sessions/${task.session_id}/artifacts/quality_report.json?preview=true`});
  assert.equal(JSON.parse(report.text).session_outcome,'completed');
  await backend.stop();
  const result={platform:process.platform,ready:true,authenticated_get:true,credential_exposed:false,simulation:task.status,events:2,validation:true,clean_exit:true};
  fs.mkdirSync(testRoot,{recursive:true});fs.writeFileSync(path.join(testRoot,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(backend.child?.exitCode===null){backend.child.stdin.end();await new Promise(resolve=>backend.child.once('exit',resolve));}}
})().catch(error=>{console.error(error);process.exitCode=1;});
