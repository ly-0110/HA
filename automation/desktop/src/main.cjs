const {app,BrowserWindow,session,protocol,ipcMain,shell,dialog,net,Menu}=require('electron');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const {Backend,wait,hasExited}=require('./backend.cjs');
const {safeRelative,isWithin,trustedEvidence,allowedRequest}=require('./policy.cjs');
const {atomicJSON}=require('./state.cjs');

const installEvent=process.argv.find(value=>value.startsWith('--squirrel-'));
if(installEvent){
 const update=path.resolve(path.dirname(process.execPath),'../Update.exe');
 const action=installEvent==='--squirrel-uninstall'?'--removeShortcut':'--createShortcut';
 if(fs.existsSync(update) && installEvent!=='--squirrel-obsolete'){
  const helper=spawn(update,[action,path.basename(process.execPath)],{windowsHide:true});
  helper.once('exit',()=>app.exit(0));helper.once('error',()=>app.exit(1));
 }else app.exit(0);
 return;
}

const APP_ID='org.iotexp.workbench';
const appState=process.env.IOT_EXP_DESKTOP_STATE || path.join(app.getPath('appData'),APP_ID);
fs.mkdirSync(appState,{recursive:true});
app.setPath('userData',appState);
app.setAppUserModelId(APP_ID);
if(process.platform==='linux')app.setDesktopName('iot-experiment-workbench.desktop');
if(process.argv.includes('--smoke-test') && process.env.IOT_EXP_CI_HEADLESS==='1')app.disableHardwareAcceleration();
if(!app.requestSingleInstanceLock()) { app.quit(); } else {
protocol.registerSchemesAsPrivileged([{scheme:'app',privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true,stream:true,bypassCSP:false}}]);
let window,backend,closing=false,exiting=false,startError='',starting=false;
let roots=[];
const smokeEvidence=[];
const resources=app.isPackaged ? process.resourcesPath : process.env.IOT_EXP_DESKTOP_RESOURCES || path.resolve(__dirname,'../build-resources');
const settingsFile=path.join(appState,'preferences.json');
let preferences={};
let preferencesError='';
if(fs.existsSync(settingsFile)){
  try {preferences=JSON.parse(fs.readFileSync(settingsFile,'utf8'));if(!preferences || typeof preferences!=='object' || Array.isArray(preferences))throw Error('配置应为对象');}
  catch(error){preferences={};preferencesError='偏好配置无法读取：'+settingsFile+'。请从备份恢复或修复JSON后重试。';}
}
const workspace=process.env.IOT_EXP_DESKTOP_WORKSPACE || preferences.workspace || path.join(os.homedir(),'IoTExperiments','default');
const locks=process.env.IOT_EXP_LOCK_ROOT || (process.platform==='win32' ? path.join(process.env.LOCALAPPDATA || app.getPath('appData'),'IoTExperimentWorkbench','locks') : path.join(process.env.XDG_STATE_HOME || path.join(os.homedir(),'.local/state'),'iot-exp','locks'));
roots=preferences.discovery_roots || [];
function trusted(event) { if(!window || event.sender!==window.webContents || event.senderFrame!==window.webContents.mainFrame || !event.senderFrame.url.startsWith('app://workbench/')) throw Error('桌面请求来源无效'); }
function save() { atomicJSON(settingsFile,{...preferences,workspace,discovery_roots:roots}); }
async function startBackend() {
  if(starting)throw Error('后台正在启动');
  starting=true;startError='';
  try {
    if(preferencesError)throw Error(preferencesError);
    if(backend?.child && !hasExited(backend.child)) await backend.stop();
    atomicJSON(path.join(appState,'desktop-process.json'),{pid:process.pid,process_start_token:null,sidecar_pid:0,sidecar_start_token:null,instance_id:'starting'});
    backend=new Backend(resources,{workspace,app_state:appState,locks,discovery_roots:roots});
    await backend.start();save();
    atomicJSON(path.join(appState,'desktop-process.json'),{pid:process.pid,process_start_token:backend.readyMessage.parent_start_token,sidecar_pid:backend.readyMessage.pid,sidecar_start_token:backend.readyMessage.process_start_token,instance_id:backend.instance});
    backend.child.once('exit',()=>{if(!closing && !exiting){startError='实验后台已退出。请等待所属任务完成清理后重试。';window.loadURL('app://workbench/startup.html');}});
    await window.loadURL('app://workbench/index.html');
  } catch(error) {startError=error.message;throw error;} finally {starting=false;}
}
async function resolveArtifact(input) {
  if(!input || !/^[a-zA-Z0-9_-]+$/.test(input.sessionId)) throw Error('会话标识无效');
  const category=input.category || 'artifacts';
  if(!['artifacts','evidence','directory'].includes(category)) throw Error('产物类别无效');
  const relative=category==='directory' ? '' : safeRelative(input.relativePath);
  const response=await backend.raw(`/api/v1/sessions/${input.sessionId}/resolve?category=${category}&relative=${encodeURIComponent(relative)}`);
  if(!response.ok) throw Error('产物不存在或无法访问');
  const value=await response.json();
  if(![workspace,...roots].some(root=>fs.existsSync(root) && isWithin(root,value.path)) || !isWithin(value.root,value.path)) throw Error('产物不在已登记工作区内');
  return value.path;
}

app.on('second-instance',()=>{ if(window){if(window.isMinimized())window.restore();window.focus();} });
app.whenReady().then(async()=>{
  const partition=session.fromPartition('workbench');
  partition.setPermissionRequestHandler((_webContents,_permission,callback)=>callback(false));
  partition.setPermissionCheckHandler(()=>false);
  partition.protocol.handle('app',async request=>{
    const url=new URL(request.url);
    if(url.host==='evidence') {
      if(process.argv.includes('--smoke-test'))smokeEvidence.push({origin:request.initiatorOrigin,method:request.method});
      if(!trustedEvidence(request)) return new Response('禁止证据访问',{status:403});
      const route=allowedRequest(url.pathname+url.search,'GET');
      if(!/\/sessions\/[^/]+\/(artifacts|evidence)\//.test(route)) return new Response('证据路径无效',{status:400});
      const response=await backend.raw(route);
      const headers=new Headers(response.headers);
      headers.set('access-control-allow-origin','app://workbench');
      headers.set('content-security-policy',"default-src 'none'; style-src 'unsafe-inline'");
      headers.set('x-content-type-options','nosniff');
      return new Response(response.body,{status:response.status,headers});
    }
    if(url.host!=='workbench' || request.method!=='GET') return new Response('禁止访问',{status:403});
    const relative=decodeURIComponent(url.pathname.slice(1) || 'index.html');
    const web=path.join(resources,'web');
    const file=path.resolve(web,safeRelative(relative));
    if(!fs.existsSync(file) || !isWithin(web,file)) return new Response('文件不存在',{status:404});
    const response=await net.fetch(pathToFileURL(file).toString());
    const headers=new Headers(response.headers);
    headers.set('content-security-policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' app://evidence data:; frame-src app://evidence; connect-src 'self' app://evidence; object-src 'none'; base-uri 'none'");
    headers.set('x-content-type-options','nosniff');
    return new Response(response.body,{status:response.status,headers});
  });
  ipcMain.handle('workbench:request',(event,input)=>{trusted(event);return backend.request(input);});
  ipcMain.handle('workbench:status',async event=>{
    trusted(event);let active_owned=0;
    if(backend?.child && !hasExited(backend.child) && backend.base){try{const health=await (await backend.raw('/api/v1/health')).json();active_owned=health.active_owned;if(backend.state!=='draining')backend.state=health.state;}catch{}}
    return {state:starting?'starting':startError?'failed':backend?.state || 'starting',active_owned,error:startError,workspace,resources,bundle:backend?.manifest.bundle_id};
  });
  ipcMain.handle('workbench:retry-backend',async event=>{trusted(event);await startBackend();return {ready:true};});
  ipcMain.handle('workbench:open-preparation',async(event,kind)=>{
    trusted(event);
    const sources={sdk:'https://developer.android.com/studio',usb:'https://developer.android.com/studio/run/device',capture:'https://www.wireshark.org/download.html'};
    if(!Object.hasOwn(sources,kind))throw Error('准备指南类型无效');
    await shell.openExternal(sources[kind]);return {opened:true};
  });
  ipcMain.handle('workbench:open-artifact',async(event,input)=>{trusted(event);const file=await resolveArtifact(input);if(input.reveal){shell.showItemInFolder(file);return {opened:true};}const error=await shell.openPath(file);if(error)throw Error(error);return {opened:true};});
  ipcMain.handle('workbench:preview-artifact',async(event,input)=>{
    trusted(event);await resolveArtifact(input);
    const viewer=new BrowserWindow({width:1000,height:760,webPreferences:{session:partition,contextIsolation:true,sandbox:true,nodeIntegration:false}});
    viewer.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    viewer.webContents.on('will-navigate',(event,url)=>{if(!url.startsWith('app://workbench/preview.html'))event.preventDefault();});
    const query=new URLSearchParams({session:input.sessionId,category:input.category,file:input.relativePath});
    await viewer.loadURL('app://workbench/preview.html?'+query);return {opened:true};
  });
  ipcMain.handle('workbench:configure-tool',async(event,kind,candidate)=>{
    trusted(event);
    if(!['sdk','dumpcap'].includes(kind)) throw Error('工具类型无效');
    if(candidate!=null && (kind!=='sdk' || typeof candidate!=='string' || !path.isAbsolute(candidate)))throw Error('SDK候选路径无效');
    const choice=candidate==null?await dialog.showOpenDialog(window,{title:kind==='sdk'?'选择 Android SDK 目录':'选择 Dumpcap 程序',properties:[kind==='sdk'?'openDirectory':'openFile']}):null;
    if(choice?.canceled)return {cancelled:true};
    const response=await backend.raw('/api/v1/desktop/tools',{method:'POST',body:JSON.stringify({kind,path:candidate ?? choice.filePaths[0],detected:candidate!=null})});
    if(!response.ok) throw Error(await response.text());
    return response.json();
  });
  ipcMain.handle('workbench:register-root',async event=>{
    trusted(event);
    const choice=await dialog.showOpenDialog(window,{title:'登记既有实验目录',properties:['openDirectory']});
    if(choice.canceled)return {cancelled:true};
    const root=fs.realpathSync(choice.filePaths[0]);
    if(root.split(path.sep).some(part=>part.toLowerCase()==='legacy'))throw Error('旧研究数据目录不属于实验工作区');
    const response=await backend.raw('/api/v1/desktop/roots',{method:'POST',body:JSON.stringify({path:root})});
    if(!response.ok)throw Error(await response.text());
    roots=[...new Set([...roots,root])];save();return {registered:true};
  });
  ipcMain.handle('workbench:choose-workspace',async event=>{
    trusted(event);
    const health=await (await backend.raw('/api/v1/health')).json();
    if(health.active_owned || health.state==='recovery')throw Error('请先完成当前任务清理');
    const choice=await dialog.showOpenDialog(window,{title:'选择实验工作区',properties:['openDirectory','createDirectory']});
    if(choice.canceled)return {cancelled:true};
    const selected=fs.realpathSync(choice.filePaths[0]);
    if(selected.split(path.sep).some(part=>part.toLowerCase()==='legacy'))throw Error('旧研究数据目录不属于实验工作区');
    if(isWithin(resources,selected))throw Error('工作区不能位于安装资源目录内');
    atomicJSON(settingsFile,{...preferences,workspace:selected,discovery_roots:roots});
    app.relaunch();await quitSafely();return {restarting:true};
  });
  ipcMain.handle('workbench:import-workspace',async event=>{
    trusted(event);
    const choice=await dialog.showOpenDialog(window,{title:'导入旧 automation 工作区',properties:['openDirectory']});
    if(choice.canceled)return {cancelled:true};
    const root=fs.realpathSync(choice.filePaths[0]);
    const response=await backend.raw('/api/v1/desktop/import',{method:'POST',body:JSON.stringify({path:root})});
    if(!response.ok)throw Error(await response.text());
    roots=[...new Set([...roots,root])];save();return response.json();
  });
  window=new BrowserWindow({width:1440,height:960,minWidth:740,minHeight:540,show:!process.argv.includes('--smoke-test') && !process.argv.includes('--instance-probe'),title:'IoT 实验工作台',icon:path.join(__dirname,'../assets/workbench.png'),webPreferences:{session:partition,preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,sandbox:true,nodeIntegration:false,offscreen:process.argv.includes('--smoke-test') && process.env.IOT_EXP_CI_HEADLESS==='1'}});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',(event,url)=>{if(!url.startsWith('app://workbench/'))event.preventDefault();});
  window.webContents.on('render-process-gone',()=>{if(!closing)window.loadURL('app://workbench/index.html');});
  window.on('close',event=>{if(!exiting){event.preventDefault();quitSafely().catch(error=>dialog.showErrorBox('退出未完成',error.message));}});
  Menu.setApplicationMenu(null);
  await window.loadURL('app://workbench/startup.html');
  try {
    await startBackend();
    if(process.argv.includes('--instance-probe')){
      atomicJSON(path.join(appState,'instance-probe-ready.json'),{pid:process.pid,instance:backend.instance});
      const timer=setInterval(()=>{if(fs.existsSync(path.join(appState,'instance-probe-stop'))){clearInterval(timer);quitSafely();}},200);
    }
    if(process.argv.includes('--smoke-test')) {
      // Wayland can stop producing frames for a hidden window after its preview
      // closes. Exercise the ordinary visible window before capturing it again.
      window.show();
      window.webContents.setBackgroundThrottling(false);
      const response=await backend.request({url:'/api/v1/bootstrap'});
      const toolHealth=await backend.request({url:'/api/v1/environment'});
      const privateChecks=toolHealth.checks.filter(check=>['node','java','appium'].includes(check.name));
      if(privateChecks.length!==3 || privateChecks.some(check=>!check.ok))throw Error('内置真机工具初始化失败：'+JSON.stringify(privateChecks));
      let rendered='';
      for(let attempt=0;attempt<100;attempt++){
        rendered=await window.webContents.executeJavaScript('document.body.innerText');
        if(rendered.includes('本机服务已连接'))break;
        await wait(100);
      }
      const title=window.webContents.getTitle();
      if(!rendered.includes('本机服务已连接'))throw Error('工作台界面未完成实际后台连接：'+rendered.slice(0,1500));
      fs.writeFileSync(path.join(appState,'workbench.png'),(await window.webContents.capturePage()).toPNG());
      const created=await backend.request({url:'/api/v1/tasks',method:'POST',body:JSON.stringify({client_request_id:'desktop-smoke-'+backend.instance,tasks:[{template_id:'mi_desk_lamp_1s',mode:'simulate',idle_min_seconds:0,idle_max_seconds:0,cooldown_seconds:0,repetitions:1}]})});
      let task;
      for(let attempt=0;attempt<100;attempt++){
        task=await backend.request({url:'/api/v1/tasks/'+created[0].id});
        if(['completed','failed','interrupted','cancelled'].includes(task.status))break;
        await wait(100);
      }
      if(task.status!=='completed' || task.completed_events!==2)throw Error('桌面私有worker模拟失败：'+task.error);
      const fixture=path.join(task.session_root,'preview-smoke.xml');
      const literal='<script>window.experimentScriptRan=true</script>';
      fs.writeFileSync(fixture,literal);
      const before=fs.readFileSync(fixture);
      await window.webContents.executeJavaScript(`window.iotDesktop.previewArtifact(${JSON.stringify({sessionId:task.session_id,category:'artifacts',relativePath:'preview-smoke.xml'})})`);
      let viewer=BrowserWindow.getAllWindows().find(item=>item!==window);
      let previewed=false;
      for(let attempt=0;attempt<100;attempt++){
        previewed=await viewer.webContents.executeJavaScript(`document.querySelector('pre')?.textContent===${JSON.stringify(literal)} && !window.iotDesktop && !window.experimentScriptRan`);
        if(previewed)break;await wait(100);
      }
      if(!previewed)throw Error('实际XML证据预览或权限隔离失败：'+await viewer.webContents.executeJavaScript('JSON.stringify({text:document.body.innerText,bridge:!!window.iotDesktop,script:!!window.experimentScriptRan,url:location.href})')+' 请求来源：'+JSON.stringify(smokeEvidence));
      if(!before.equals(fs.readFileSync(fixture)))throw Error('预览改变了原文件');
      viewer.destroy();
      fs.writeFileSync(path.join(task.session_root,'preview-smoke.png'),(await window.webContents.capturePage()).toPNG());
      await window.webContents.executeJavaScript(`window.iotDesktop.previewArtifact(${JSON.stringify({sessionId:task.session_id,category:'artifacts',relativePath:'preview-smoke.png'})})`);
      viewer=BrowserWindow.getAllWindows().find(item=>item!==window);let imageLoaded=false;
      for(let attempt=0;attempt<100;attempt++){
        imageLoaded=await viewer.webContents.executeJavaScript(`!!document.querySelector('img')?.naturalWidth`);
        if(imageLoaded)break;await wait(100);
      }
      if(!imageLoaded)throw Error('实际图片证据流未完成解码');
      viewer.destroy();
      fs.writeFileSync(path.join(appState,'smoke-result.json'),JSON.stringify({ready:true,title,desktop:response.desktop,credential_exposed:'control_token' in response,rendered:true,simulation:task.status,events:task.completed_events,session:task.session_root,text_preview:true,image_preview:true,preview_isolated:true,original_file_unchanged:true,private_appium_prepared:true,private_tool_checks:privateChecks,sdk_candidates:toolHealth.sdk_candidates}));
      exiting=true;await backend.stop();app.quit();
    }
  } catch(error) {
    if(process.argv.includes('--smoke-test')){fs.writeFileSync(path.join(appState,'smoke-result.json'),JSON.stringify({ready:false,error:error.message}));exiting=true;app.exit(2);}
    else if(!closing && !exiting)await window.loadURL('app://workbench/startup.html');
  }
});
async function quitSafely() {
  if(closing)return;
  closing=true;
  try {
    if(backend?.child && !hasExited(backend.child) && backend.base) {
      const health=await (await backend.raw('/api/v1/health')).json();
      if(health.active_owned){
        const answer=await dialog.showMessageBox(window,{type:'question',buttons:['安全停止并退出','取消退出'],defaultId:0,cancelId:1,message:'当前实验需要完成抓包与日志清理后退出。'});
        if(answer.response!==0)return;
      }
      window.setTitle('IoT 实验工作台 · 正在安全清理');
      await window.loadURL('app://workbench/startup.html');
      let elapsed=0;
      const stop=backend.stop();let done=false;stop.then(()=>done=true,()=>done=true);
      while(!done){
        await wait(1000);elapsed++;
        if(elapsed>=60 && elapsed%60===0){
          const answer=await dialog.showMessageBox(window,{type:'warning',buttons:['继续等待','强制终止本应用任务','返回工作台'],defaultId:0,cancelId:2,message:'安全停止已持续60秒，强制终止可能留下不完整产物。'});
          if(answer.response===2){
            stop.then(async()=>{
              await startBackend();
            }).catch(error=>dialog.showErrorBox('后台重启失败',error.message));
            return;
          }
          if(answer.response===1){
            const tasks=await backend.request({url:'/api/v1/tasks'});
            for(const task of tasks.filter(item=>item.controllable && item.status==='stopping')) await backend.request({url:`/api/v1/tasks/${task.id}/force-stop`,method:'POST'});
          }
        }
      }
      await stop;
    }
    else if((backend?.child && !hasExited(backend.child)) || (backend?.preparingChild && !hasExited(backend.preparingChild))){
      window.setTitle('IoT 实验工作台 · 正在完成初始化清理');
      await backend.stop();
    }
    exiting=true;app.quit();
  } finally {closing=false;}
}
app.on('before-quit',event=>{if(!exiting){event.preventDefault();quitSafely().catch(error=>dialog.showErrorBox('退出未完成',error.message));}});
app.on('will-quit',()=>{
 const marker=path.join(appState,'desktop-process.json');
 if(fs.existsSync(marker)){try{const value=JSON.parse(fs.readFileSync(marker,'utf8'));if(value.pid===process.pid)fs.unlinkSync(marker);}catch{}}
});
}
