// Thin GUI acceptance entrypoint: real main/preload/protocol, sandboxed renderer,
// real React DOM handlers, private Python scheduler and simulated workers.
// Only phone-list fixtures and native dialog answers are substituted. No SDK,
// capture tool or real phone is configured, and file-opening IPC is not invoked.
const {app,BrowserWindow,dialog}=require('electron');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const assert=require('node:assert/strict');
const {Backend,wait}=require('../src/backend.cjs');

const base=process.env.IOT_EXP_UI_TEST_ROOT;
if(!base || !path.isAbsolute(base))throw Error('Set an absolute IOT_EXP_UI_TEST_ROOT for this isolated test.');
const testRoot=path.resolve(base);
const source=path.resolve(process.env.IOT_EXP_UI_SOURCE_RESOURCES || path.join(__dirname,'../build-resources'));
const resources=path.join(testRoot,'resources');
const state=path.join(testRoot,'state');
const workspace=path.join(testRoot,'workspace');
const seedRuntime=process.env.IOT_EXP_UI_PREPARED_CACHE==='1';
const result={kind:'sandboxed-electron-ui',phone_fixture:true,real_device_operations:false,capture_operations:false,checks:[],tasks:[],screenshots:[]};
fs.mkdirSync(testRoot,{recursive:true});
const manifest=JSON.parse(fs.readFileSync(path.join(source,'runtime-manifest.json'),'utf8'));
result.bundle_id=manifest.bundle_id;result.actual_versions=manifest.actual_versions;result.warm_appium_cache=seedRuntime;
fs.mkdirSync(resources,{recursive:true});
// Hard-link immutable payloads only. Never change their content/ACLs: the sole
// intentionally corrupt asset is the independent manifest copy below.
for(const relative of Object.keys(manifest.files)){
  if(!relative.startsWith('runtime/'))continue;
  const input=path.resolve(source,relative),output=path.resolve(resources,relative);
  if(!input.startsWith(source+path.sep) || !output.startsWith(resources+path.sep))throw Error('Unsafe fixture payload path');
  fs.mkdirSync(path.dirname(output),{recursive:true});
  if(!fs.existsSync(output))fs.linkSync(input,output);
  // Optional warm-cache verification after a previously covered first-use run.
  // Immutable Appium payload is linked; its writable index/cache stays private.
  const prefix=`runtime/${manifest.target}/appium/`;
  if(seedRuntime && relative.startsWith(prefix) && !relative.includes('/.cache/')){
    const cacheFile=path.resolve(state,'appium',manifest.bundle_id,relative.slice(prefix.length));
    const cacheRoot=path.resolve(state,'appium',manifest.bundle_id);
    if(!cacheFile.startsWith(cacheRoot+path.sep))throw Error('Unsafe Appium fixture path');
    fs.mkdirSync(path.dirname(cacheFile),{recursive:true});
    if(!fs.existsSync(cacheFile))fs.linkSync(input,cacheFile);
  }
}
if(seedRuntime)fs.writeFileSync(path.join(state,'appium',manifest.bundle_id,'bundle.json'),JSON.stringify({bundle_id:manifest.bundle_id,schema:1}));
fs.cpSync(path.join(source,'templates'),path.join(resources,'templates'),{recursive:true,force:true});
const web=path.resolve(__dirname,'../../web/dist');
fs.cpSync(web,path.join(resources,'web'),{recursive:true,force:true});
manifest.files=Object.fromEntries(Object.entries(manifest.files).filter(([name])=>!name.startsWith('web/')));
function recordWeb(directory){for(const item of fs.readdirSync(directory,{withFileTypes:true})){
  const file=path.join(directory,item.name);
  if(item.isDirectory())recordWeb(file);else if(item.isFile())manifest.files['web/'+path.relative(web,file).split(path.sep).join('/')]=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}}
recordWeb(web);
const corrupted=structuredClone(manifest);
corrupted.files[manifest.executables.python]='0'.repeat(64);
fs.writeFileSync(path.join(resources,'runtime-manifest.json'),JSON.stringify(corrupted));
process.env.IOT_EXP_DESKTOP_RESOURCES=resources;
process.env.IOT_EXP_DESKTOP_STATE=state;
process.env.IOT_EXP_DESKTOP_WORKSPACE=workspace;
process.env.IOT_EXP_LOCK_ROOT=path.join(testRoot,'locks');
process.argv.push('--instance-probe');

let ownedBackend,fixturePhone=false,forceFixtureTask='',drainStarted=false;
const originalStart=Backend.prototype.start;
Backend.prototype.start=async function(){ownedBackend=this;return originalStart.call(this);};
const originalRequest=Backend.prototype.request;
Backend.prototype.request=async function(input){
  if(input.url.startsWith('/api/v1/devices'))return {devices:fixturePhone?[{
    udid:'UI_PREFLIGHT_FIXTURE',state:'device',manufacturer:'UI Fixture',model:'No physical phone',busy_status:'idle',android_version:'8',sdk_level:'26',
  }]:[],error:null};
  const value=await originalRequest.call(this,input);
  if(input.url==='/api/v1/tasks' && input.method==='POST')result.tasks.push(...value.map(task=>({id:task.id,request:task.request})));
  if(forceFixtureTask && input.url==='/api/v1/tasks' && !input.method)return value.map(task=>task.id===forceFixtureTask?{
    ...task,status:'stopping',stage:'stopping',stop_requested_at_ns:(Date.now()-61000)*1e6,updated_at_ns:Date.now()*1e6,
  }:task);
  return value;
};
const originalStop=Backend.prototype.stop;
Backend.prototype.stop=async function(){drainStarted=true;return originalStop.call(this);};
const choices=[];
const cleanupControl=setInterval(()=>{
  if(fs.existsSync(path.join(testRoot,'stop-this-ui-test')))app.quit();
},500);
app.once('will-quit',()=>clearInterval(cleanupControl));
dialog.showMessageBox=async(_window,options)=>{
  if(options.buttons?.includes('取消退出')){
    const response=choices.shift();assert.ok(response===0 || response===1,'Unexpected quit dialog');
    result.quit_dialogs=(result.quit_dialogs || []).concat({message:options.message,response});
    return {response,checkboxChecked:false};
  }
  throw Error('Unexpected native dialog: '+options.message);
};
dialog.showErrorBox=(title,message)=>{result.native_error={title,message};};
require('../src/main.cjs');

let main;
const js=code=>new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Renderer DOM operation timed out')),5000);
  main.webContents.executeJavaScript(code).then(value=>{clearTimeout(timer);resolve(value);},error=>{clearTimeout(timer);reject(error);});
});
async function until(predicate,message,timeout=30000){
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline){try{if(await predicate())return;}catch{}await wait(100);}
  let text='';try{text=await js('document.body.innerText');}catch{}
  throw Error(message+'\n'+text.slice(0,3500));
}
const body=()=>js('document.body.innerText');
async function clickText(text,selector='button'){
  await js(`(()=>{const element=[...document.querySelectorAll(${JSON.stringify(selector)})].find(item=>item.textContent.trim()===${JSON.stringify(text)});if(!element)throw Error('Missing clickable text: '+${JSON.stringify(text)});element.click();})()`);
  await wait(100);
}
async function setLabel(label,value){
  await js(`(()=>{const label=[...document.querySelectorAll('label')].find(item=>item.firstChild?.textContent.trim()===${JSON.stringify(label)});const element=label?.querySelector('input,select');if(!element)throw Error('Missing input label: '+${JSON.stringify(label)});Object.getOwnPropertyDescriptor(element.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(element,${JSON.stringify(String(value))});element.dispatchEvent(new Event(element.tagName==='SELECT'?'change':'input',{bubbles:true}));})()`);
  await wait(100);
}
async function setAria(label,value){
  await js(`(()=>{const element=document.querySelector('[aria-label='+${JSON.stringify(JSON.stringify(label))}+']');if(!element)throw Error('Missing aria input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(element,${JSON.stringify(String(value))});element.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await wait(100);
}
async function screenshot(name){
  await wait(250);
  const file=path.join(testRoot,name+'.png');fs.writeFileSync(file,(await main.webContents.capturePage()).toPNG());result.screenshots.push(file);
  fs.writeFileSync(path.join(testRoot,name+'.txt'),await body());
}
function passed(name,detail){result.checks.push({name,passed:true,detail});fs.writeFileSync(path.join(testRoot,'ui-progress.json'),JSON.stringify(result,null,2));console.log('UI check: '+name);}
async function openCreate(templateId='mi_desk_lamp_1s'){
  await clickText('新建实验');
  await until(async()=>await js('!!document.querySelector(".create-panel")'),'Create form did not load');
  await setLabel('实验模板',templateId);
}
async function zeroTiming(){
  await js(`document.querySelector('details.advanced').open=true`);
  for(const label of ['最短等待（秒）','最长等待（秒）','操作后等待（秒）'])await setLabel(label,0);
  await js(`document.querySelector('details.advanced').open=false`);
}
async function launchUI(){
  const before=result.tasks.length;await clickText('启动实验');
  await until(()=>result.tasks.length>before,'UI did not create a task');
  const created=result.tasks.at(-1);
  await until(async()=>{const task=await originalRequest.call(ownedBackend,{url:'/api/v1/tasks/'+created.id});return await js(`document.querySelector('.run-detail code')?.textContent===${JSON.stringify(created.id)} || (${JSON.stringify(task.session_id)}!==null && document.querySelector('.run-detail code')?.textContent===${JSON.stringify(task.session_id)})`);},'Created task not selected');
  return created;
}
async function complete(taskId){
  let task;
  await until(async()=>{task=await originalRequest.call(ownedBackend,{url:'/api/v1/tasks/'+taskId});return ['completed','failed','cancelled','interrupted'].includes(task.status);},'Task did not reach a terminal state');
  return task;
}
function assertEventTargets(events,type,values){assert.deepEqual(events.filter(event=>event.event_type===type).map(event=>event.target),values);}

app.whenReady().then(async()=>{
  try{
    await until(()=>{main=BrowserWindow.getAllWindows()[0];return !!main;},'No main window');
    main.show();
    main.webContents.setBackgroundThrottling(false);
    assert.equal(process.argv.some(argument=>argument==='--no-sandbox' || argument==='--disable-sandbox'),false);
    assert.equal(await js(`typeof window.require==='undefined' && typeof window.process==='undefined' && !!window.iotDesktop`),true);
    result.renderer_security={sandbox_disabling_arguments:false,node_globals_exposed:false,context_bridge_available:true};
    main.webContents.on('render-process-gone',(_event,details)=>{result.renderer_gone=details;console.log('Owned renderer gone: '+details.reason);});
    await until(async()=>await js(`!document.querySelector('#retry')?.hidden && document.querySelector('#error')?.textContent.includes('校验失败')`),'Integrity failure/retry page not shown');
    assert.equal(await js('!!window.iotDesktop'),true);
    await screenshot('01-startup-integrity-failure');
    passed('startup_failure','An independent manifest has a bad Python checksum; startup shows the actual verification error and retry button.');
    fs.writeFileSync(path.join(resources,'runtime-manifest.json'),JSON.stringify(manifest));
    await clickText('重试连接');
    await until(async()=>(await body()).includes('本机服务已连接'),'GUI retry did not restore the real backend',90000);
    passed('startup_retry','Restoring only the independent manifest and clicking Retry establishes the real authenticated backend.');

    await openCreate();await setLabel('每个事件目标的次数',1);await zeroTiming();
    await clickText('正式采集');
    assert.equal(await js(`document.querySelector('.capture-config label:nth-child(1) input').value`),'10.42.0.250');
    assert.equal(await js(`document.querySelector('.capture-config label:nth-child(2) input').value`),'wlp2s0');
    assert.equal(await js(`document.querySelector('.capture-config label:nth-child(3) input').value`),'host 10.42.0.250');
    await setLabel('目标设备 IP','10.42.0.123');await setLabel('抓包接口','draft-interface');
    await setLabel('抓包过滤器','host 10.42.0.123');await setLabel('随机种子',12345);
    await clickText('模拟运行');await clickText('加入批次');
    const savedDraft=await js(`JSON.stringify([...document.querySelectorAll('.create-panel input,.create-panel select')].map(item=>[item.value,item.checked]))`);
    const savedPlan=await js(`document.querySelector('.plan-preview').innerText`);
    for(const tab of ['设备概览','运行中心','实验记录','环境设置']){
      await clickText(tab);
      assert.equal(await js(`document.querySelector('.create-panel').getBoundingClientRect().width`),0);
      await clickText('新建实验');
      assert.equal(await js(`JSON.stringify([...document.querySelectorAll('.create-panel input,.create-panel select')].map(item=>[item.value,item.checked]))`),savedDraft);
      assert.equal(await js(`document.querySelector('.plan-preview').innerText`),savedPlan);
      assert.equal(await js(`document.querySelectorAll('.queue-item').length`),1);
    }
    await clickText('正式采集');
    assert.equal(await js(`document.querySelector('.capture-config label:nth-child(1) input').value`),'10.42.0.123');
    assert.equal(await js(`document.querySelector('.capture-config label:nth-child(2) input').value`),'draft-interface');
    await clickText('模拟运行');
    await js(`document.querySelector('[aria-label="移除批次第 1 项"]').click()`);await wait(100);
    passed('create_draft_survives_navigation','All four other tabs preserve edited fields, event plan and queued batch; formal defaults load and edited capture values survive navigation.');
    await clickText('检查配置');
    await until(async()=>(await body()).includes('预检通过'),'Simulated preflight did not pass');
    const power=await launchUI();const powerTask=await complete(power.id);
    assert.equal(powerTask.status,'completed',powerTask.error);assert.equal(powerTask.completed_events,2);assert.equal(powerTask.quality.validation.ok,true);
    await until(async()=>await js('document.querySelector(".console")?.textContent.includes("实验执行完成")'),'Task log not rendered');
    await until(async()=>await js(`!!document.querySelector('.run-detail .badge.completed') && document.querySelector('.run-detail .metrics strong')?.innerText.replace(/\\s/g,'')==='2/2'`),'Completed task progress was not rendered');
    await screenshot('02-power-simulation-log');
    passed('ui_simulation_selection_and_logs',{task:power.id,events:powerTask.completed_events,validation:true});

    await openCreate('mi_desk_lamp_1s_advanced');await setLabel('每个事件目标的次数',1);await zeroTiming();
    await setAria('亮度目标 1',25);await setAria('亮度目标 2',75);
    await setAria('色温目标 1',3100);await setAria('色温目标 2',4700);
    await clickText('设备概览');await clickText('新建实验');
    assert.equal(await js(`document.querySelector('[aria-label="亮度目标 1"]').value`),'25');
    assert.equal(await js(`document.querySelector('[aria-label="色温目标 2"]').value`),'4700');
    const beforeInvalid=result.tasks.length;
    await setAria('亮度目标 1',101);await clickText('启动实验');
    await until(async()=>(await body()).includes('亮度目标必须是范围内的整数'),'Invalid target did not produce a field error');
    assert.equal(result.tasks.length,beforeInvalid);await setAria('亮度目标 1',25);
    passed('invalid_target_blocks_submission','A brightness target above the template range shows a form error and sends no task request.');
    // Keep two scene targets and both focus booleans, independently of sliders.
    await js(`(()=>{const group=[...document.querySelectorAll('.event-group')].find(item=>item.querySelector('b')?.textContent.includes('情景'));for(const label of group.querySelectorAll('.target-choice')){const wanted=['电脑模式','阅读模式'].includes(label.innerText.trim());if(label.querySelector('input').checked!==wanted)label.querySelector('input').click();}})()`);
    await wait(200);
    await js(`(()=>{const group=[...document.querySelectorAll('.event-group')].find(item=>item.querySelector('b')?.textContent.includes('专注'));for(const input of group.querySelectorAll('.target-choice input'))if(!input.checked)input.click();})()`);
    await wait(200);
    const previewBefore=await js(`JSON.stringify([...document.querySelector('.plan-preview').children].map(item=>item.innerText).sort())`);
    await js(`(()=>{const group=[...document.querySelectorAll('.event-group')].find(item=>item.querySelector('b')?.textContent.includes('亮度'));group.querySelector('input[type=checkbox]').click();})()`);
    await wait(100);
    assert.equal(await js(`document.querySelectorAll('[aria-label^="亮度目标"]').length`),0);
    assert.equal(await js(`document.querySelector('[aria-label="色温目标 1"]').value`),'3100');
    await js(`(()=>{const group=[...document.querySelectorAll('.event-group')].find(item=>item.querySelector('b')?.textContent.includes('亮度'));group.querySelector('input[type=checkbox]').click();})()`);
    await wait(100);await setAria('亮度目标 1',25);await setAria('亮度目标 2',75);
    assert.equal(await js(`JSON.stringify([...document.querySelector('.plan-preview').children].map(item=>item.innerText).sort())`),previewBefore);
    await screenshot('03-independent-event-targets');
    const advanced=await launchUI();
    assertEventTargets(advanced.request.events,'set_brightness',[25,75]);assertEventTargets(advanced.request.events,'set_color_temperature',[3100,4700]);
    assert.deepEqual(new Set(advanced.request.events.filter(event=>event.event_type==='select_scene').map(event=>event.target)),new Set(['电脑模式','阅读模式']));
    assert.deepEqual(new Set(advanced.request.events.filter(event=>event.event_type==='set_focus_mode').map(event=>event.target)),new Set([true,false]));
    const advancedTask=await complete(advanced.id);assert.equal(advancedTask.status,'completed',advancedTask.error);assert.equal(advancedTask.completed_events,8);assert.equal(advancedTask.quality.validation.ok,true);
    passed('independent_event_editing',{task:advanced.id,events:8,targets:advanced.request.events,validation:true});

    await openCreate('xiaomi_touchscreen_speaker_music');await setLabel('每个事件目标的次数',1);await zeroTiming();
    assert.equal(await js(`document.querySelectorAll('.numeric-targets').length`),0);
    const music=await launchUI();const musicTask=await complete(music.id);
    assert.equal(musicTask.status,'completed',musicTask.error);assert.equal(musicTask.completed_events,2);assert.equal(musicTask.quality.validation.ok,true);
    assert.deepEqual(music.request.events.map(event=>[event.event_type,event.required_state,event.expected_state]),[['play_music','paused','playing'],['pause_music','playing','paused']]);
    passed('template_switch_preserves_music_transitions',{task:music.id,events:2,validation:true});

    fixturePhone=true;
    const reloaded=new Promise(resolve=>main.webContents.once('did-finish-load',resolve));
    main.webContents.reload();await reloaded;
    await until(async()=>(await body()).includes('本机服务已连接'),'Reload did not reconnect');
    await openCreate();await clickText('正式采集');
    assert.equal(await js(`document.querySelector('details.advanced').open`),false);
    await setLabel('运行手机','UI_PREFLIGHT_FIXTURE');await setLabel('每个事件目标的次数',1);
    await setLabel('目标设备 IP','192.0.2.20');await setLabel('抓包接口','UI_NO_CAPTURE');await setLabel('抓包过滤器','host 192.0.2.20');
    const formal=await launchUI();const formalTask=await complete(formal.id);
    assert.equal(formalTask.status,'failed');assert.equal(formalTask.completed_events,0);assert.equal(formalTask.session_root,null);
    assert.match(formalTask.error,/ADB|SDK|未配置|not found/i);
    await until(async()=>await js('document.querySelector(".run-detail .alert.error")?.textContent.length>0'),'Formal failure was not visible');
    await screenshot('04-formal-preflight-blocked');
    passed('formal_basic_start_and_preflight_failure',{task:formal.id,advanced_closed:true,events:0,no_session:true,error:formalTask.error,phone:'UI fixture only; no physical phone or SDK'});
    fixturePhone=false;

    forceFixtureTask=power.id;await clickText('运行中心');
    await until(async()=>await js(`!![...document.querySelectorAll('.task-list button')].find(item=>item.querySelector('small')?.textContent===${JSON.stringify(powerTask.session_id)})`),'Original task unavailable');
    await js(`([...document.querySelectorAll('.task-list button')].find(item=>item.querySelector('small')?.textContent===${JSON.stringify(powerTask.session_id)})).click()`);
    await clickText('暂停更新');
    await until(async()=>await js(`!![...document.querySelectorAll('.run-detail button')].find(item=>item.textContent==='强制终止')`),'Fresh cleanup update incorrectly reset the 60-second force-stop UI');
    await screenshot('05-force-button-after-cleanup-update');
    passed('force_button_stop_timestamp','Actual React render with paused logs receives a stopping row with stop_requested_at_ns 61 seconds old and updated_at_ns current; force option is visible. No force action is sent.');
    forceFixtureTask='';

    const backendInstance=ownedBackend.instance;const taskCount=(await originalRequest.call(ownedBackend,{url:'/api/v1/tasks'})).length;
    const rendererReloaded=new Promise((resolve,reject)=>{
      const deadline=setTimeout(()=>reject(Error('Renderer recovery did not finish loading')),30000);
      main.webContents.once('did-finish-load',()=>{clearTimeout(deadline);resolve();});
    });
    main.webContents.forcefullyCrashRenderer();
    await rendererReloaded;assert.equal(main.webContents.isDestroyed(),false);
    await until(async()=>(await body()).includes('本机服务已连接'),'Renderer did not restore',30000);
    assert.equal(ownedBackend.instance,backendInstance);assert.equal((await originalRequest.call(ownedBackend,{url:'/api/v1/tasks'})).length,taskCount);
    passed('renderer_crash_restore','The real renderer crashed and reloaded with the same backend instance and unchanged task count.');

    ownedBackend.child.kill();
    await until(async()=>await js(`!document.querySelector('#retry')?.hidden && document.querySelector('#error')?.textContent.includes('后台已退出')`),'Owned sidecar failure did not show a retry page');
    await screenshot('07-owned-backend-failure');
    await clickText('重试连接');
    await until(async()=>(await body()).includes('本机服务已连接'),'Backend fault retry did not reconnect',90000);
    assert.notEqual(ownedBackend.instance,backendInstance);
    assert.equal((await originalRequest.call(ownedBackend,{url:'/api/v1/tasks'})).length,taskCount);
    passed('backend_fault_retry','Only the isolated owned sidecar was terminated; the fault page and user Retry restore a new authenticated instance without re-running existing tasks.');

    await openCreate();await setLabel('每个事件目标的次数',100);
    await js(`document.querySelector('details.advanced').open=true`);
    await setLabel('最短等待（秒）',1);await setLabel('最长等待（秒）',1);await setLabel('操作后等待（秒）',1);
    const active=await launchUI();
    await until(async()=>{const task=await originalRequest.call(ownedBackend,{url:'/api/v1/tasks/'+active.id});return task.status==='running';},'Long simulated task did not begin');
    choices.push(1);main.close();await until(()=>result.quit_dialogs?.length===1,'Cancel exit choice was not presented');await wait(500);
    assert.equal(main.isDestroyed(),false);assert.equal(ownedBackend.child.exitCode,null);assert.equal(drainStarted,false);
    passed('cancel_safe_exit','Cancel keeps the main window/backend alive and does not begin draining.');
    await screenshot('05-cancelled-exit-task-running');
    const progressTimer=setInterval(async()=>{
      try{const text=await body();if(text.includes('正在安全清理')){clearInterval(progressTimer);result.cleanup_progress=text;await screenshot('06-safe-cleanup-progress');}}catch{}
    },40);
    choices.push(0);main.close();
    app.once('will-quit',()=>{
      clearInterval(progressTimer);
      result.safe_exit=ownedBackend.child.exitCode!==null && ownedBackend.state==='stopped';
      if(result.safe_exit)passed('safe_exit_cleanup','The real owned backend completed draining and exited before Electron quit.');
      result.passed=result.safe_exit && result.checks.length>=9 && result.checks.every(check=>check.passed) && !result.native_error;
      result.completed_at=new Date().toISOString();
      fs.writeFileSync(path.join(testRoot,'ui-result.json'),JSON.stringify(result,null,2));
      console.log(JSON.stringify({passed:result.passed,checks:result.checks.length,safe_exit:result.safe_exit,progress_observed:!!result.cleanup_progress}));
      if(!result.passed)process.exitCode=1;
    });
  }catch(error){
    result.error=error.stack;result.passed=false;fs.writeFileSync(path.join(testRoot,'ui-result.json'),JSON.stringify(result,null,2));console.error(error);
    if(main && !main.isDestroyed())await screenshot('failure').catch(()=>{});
    if(ownedBackend?.child?.exitCode===null)await ownedBackend.stop().catch(()=>{});
    app.exit(2);
  }
});
