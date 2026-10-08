// Authenticated private backend probe in fresh, deliberately long paths.
// No phone actions, APK installation, capture, or default profile changes.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {execFileSync,spawn}=require('node:child_process');
const net=require('node:net');
const {Backend,wait,hasExited}=require('../src/backend.cjs');
const root=process.env.IOT_EXP_DEPENDENCY_TEST_ROOT;
if(!root || !path.isAbsolute(root))throw Error('Choose an absolute isolated test root.');
if(fs.existsSync(path.join(root,'dependency-result.json')))throw Error('Choose a fresh test root; keep previous results.');
const source=path.resolve(process.env.IOT_EXP_DEPENDENCY_RESOURCES || path.join(__dirname,'../build-resources'));
const useInstalled=process.env.IOT_EXP_DEPENDENCY_USE_SOURCE==='1';
const resources=useInstalled?source:path.join(root,'模拟应用缓存目录','LocalCache','Local','IoTExperimentWorkbench','app-0.1.2','resources');
const state=process.env.IOT_EXP_DEPENDENCY_APP_STATE || path.join(root,'独立用户配置目录','state');
fs.mkdirSync(root,{recursive:true});
const manifest=JSON.parse(fs.readFileSync(path.join(source,'runtime-manifest.json'),'utf8'));
if(!useInstalled)for(const relative of Object.keys(manifest.files)){
  const input=path.resolve(source,relative),output=path.resolve(resources,relative);
  assert.ok(input.startsWith(source+path.sep) && output.startsWith(resources+path.sep));
  fs.mkdirSync(path.dirname(output),{recursive:true});
  if(!fs.existsSync(output))fs.linkSync(input,output);
}
if(!useInstalled){
  fs.cpSync(path.join(source,'templates'),path.join(resources,'templates'),{recursive:true});
  fs.cpSync(path.join(source,'web'),path.join(resources,'web'),{recursive:true});
  fs.writeFileSync(path.join(resources,'runtime-manifest.json'),JSON.stringify(manifest));
}
const backend=new Backend(resources,{app_state:state,workspace:path.join(root,'workspace'),locks:path.join(root,'locks')});
const result={bundle_id:manifest.bundle_id,resources,state,phone_actions:false,uses_installed_resources:useInstalled,default_profile_modified:!!process.env.IOT_EXP_DEPENDENCY_APP_STATE};
(async()=>{
  try{
    await backend.start();
    const before=await backend.request({url:'/api/v1/environment'});
    result.private_tools=before.checks.filter(check=>['node','java','appium'].includes(check.name));
    assert.equal(result.private_tools.length,3);
    assert.ok(result.private_tools.every(check=>check.ok),JSON.stringify(result.private_tools));
    result.appium_prepared=JSON.parse(fs.readFileSync(path.join(state,'runtime-status.json'),'utf8')).prepared;
    assert.equal(result.appium_prepared,true);
    const existing=fs.existsSync(path.join(state,'tools.json'))?JSON.parse(fs.readFileSync(path.join(state,'tools.json'),'utf8')):{};
    if(!useInstalled)assert.ok(!existing.sdk,'Detection must not select tools silently.');
    assert.ok(before.sdk_candidates.length>0,'This workstation has an existing SDK.');
    const sdk=existing.sdk?{path:existing.sdk,source:'existing selection',adb:path.join(existing.sdk,'platform-tools',process.platform==='win32'?'adb.exe':'adb')}:before.sdk_candidates[0];
    if(!existing.sdk){
      assert.equal(before.sdk_candidates.length,1,'A real profile requires an unambiguous existing SDK.');
      const selected=await backend.raw('/api/v1/desktop/tools',{method:'POST',body:JSON.stringify({kind:'sdk',path:sdk.path,detected:true})});
      assert.equal(selected.status,200,await selected.text());
    }
    const after=await backend.request({url:'/api/v1/environment'});
    result.device_checks=after.checks.filter(check=>['adb','node','java','appium','android_sdk_root'].includes(check.name));
    assert.ok(result.device_checks.every(check=>check.ok),JSON.stringify(result.device_checks));
    result.sdk=sdk;
    result.adb_version=execFileSync(sdk.adb,['version'],{encoding:'utf8',windowsHide:true});
    const env={...backend.environment(),APPIUM_HOME:path.join(state,'appium',manifest.bundle_id),JAVA_HOME:path.dirname(path.dirname(path.join(resources,manifest.executables.java))),ANDROID_HOME:sdk.path,ANDROID_SDK_ROOT:sdk.path};
    const node=path.join(resources,manifest.executables.node);
    const appium=path.join(env.APPIUM_HOME,'node_modules/appium/index.js');
    result.driver_list=execFileSync(node,[appium,'driver','list','--installed','--json'],{env,encoding:'utf8',windowsHide:true,timeout:40000});
    assert.ok(result.driver_list.includes('6.9.3'));
    const listener=net.createServer();
    await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
    const port=listener.address().port;
    await new Promise(resolve=>listener.close(resolve));
    const server=spawn(node,[appium,'--address','127.0.0.1','--port',String(port)],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let serverLog='';
    server.stdout.on('data',data=>serverLog=(serverLog+data).slice(-12000));
    server.stderr.on('data',data=>serverLog=(serverLog+data).slice(-12000));
    try{
      for(let attempt=0;attempt<200;attempt++){
        assert.ok(!hasExited(server),serverLog);
        try{const response=await fetch(`http://127.0.0.1:${port}/status`,{signal:AbortSignal.timeout(500)});const health=await response.json();if(health.value?.ready){result.appium_server_ready=true;break;}}catch{}
        await wait(100);
      }
      assert.equal(result.appium_server_ready,true,serverLog);
      assert.ok(serverLog.includes('uiautomator2'),serverLog);
      result.appium_driver_loaded=true;
    }finally{
      if(!hasExited(server)){server.kill();await new Promise(resolve=>server.once('exit',resolve));}
      fs.writeFileSync(path.join(root,'private-appium-server.log'),serverLog);
    }
    result.passed=true;
  }catch(error){result.passed=false;result.error=error.stack;process.exitCode=1;}
  finally{
    await backend.stop();
    fs.writeFileSync(path.join(root,'dependency-result.json'),JSON.stringify(result,null,2));
    process.stdout.write(JSON.stringify({passed:result.passed,bundle:result.bundle_id,private_tools:result.private_tools,sdk:result.sdk,error:result.error})+'\n');
  }
})();
