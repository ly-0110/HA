// Owned Electron test entry. All evidence is generated in a simulated session.
const {app,BrowserWindow,shell,dialog}=require('electron');
const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');
const {wait}=require('../src/backend.cjs');
const state=process.env.IOT_EXP_DESKTOP_STATE;
if(!state || !process.env.IOT_EXP_DESKTOP_WORKSPACE)throw Error('Explicit test state/workspace required');
fs.mkdirSync(state,{recursive:true});
for(const file of ['instance-probe-ready.json','instance-probe-stop','file-actions-error.txt'])fs.rmSync(path.join(state,file),{force:true});
const opens=[],reveals=[],dialogs=[];
const nativeOpen=shell.openPath.bind(shell),nativeReveal=shell.showItemInFolder.bind(shell);
let missingAssociation=false,selection=null;
shell.openPath=async file=>{opens.push(file);return missingAssociation?'测试：没有可打开此文件的默认应用':nativeOpen(file);};
shell.showItemInFolder=file=>{reveals.push(file);nativeReveal(file);};
dialog.showOpenDialog=async(_window,options)=>{dialogs.push(options);return selection?{canceled:false,filePaths:[selection]}:{canceled:true,filePaths:[]};};
app.relaunch=()=>{throw Error('File verification must not switch/relaunch the workspace');};
process.argv.push('--instance-probe');
require('../src/main.cjs');
const sha=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function pcap(){
 const header=Buffer.alloc(24);header.writeUInt32LE(0xa1b2c3d4);header.writeUInt16LE(2,4);header.writeUInt16LE(4,6);header.writeUInt32LE(65535,16);header.writeUInt32LE(1,20);
 return header; // Valid empty classic PCAP; no network capture is performed.
}
app.whenReady().then(async()=>{
 const result={platform:process.platform,checks:{}};let main;
 try{
  for(let i=0;i<1200&&!fs.existsSync(path.join(state,'instance-probe-ready.json'));i++)await wait(100);
  if(!fs.existsSync(path.join(state,'instance-probe-ready.json')))throw Error('Actual desktop did not become ready');
  main=BrowserWindow.getAllWindows()[0];
  const invoke=(method,input)=>main.webContents.executeJavaScript(`window.iotDesktop[${JSON.stringify(method)}](${JSON.stringify(input)})`);
  const api=input=>invoke('request',input);
  const [created]=await api({url:'/api/v1/tasks',method:'POST',body:JSON.stringify({client_request_id:'files-'+Date.now(),tasks:[{template_id:'mi_desk_lamp_1s',mode:'simulate',repetitions:1,idle_min_seconds:0,idle_max_seconds:0,cooldown_seconds:0}]})});
  let task;
  for(let i=0;i<200;i++){task=await api({url:'/api/v1/tasks/'+created.id});if(task.status==='completed')break;await wait(100);}
  if(task.status!=='completed')throw Error('Simulation fixture failed');
  const file=path.join(task.session_root,'original.pcap');fs.writeFileSync(file,pcap());
  const beforeHash=sha(file),beforeNames=fs.readdirSync(task.session_root).sort();
  const picked={sessionId:task.session_id,category:'artifacts',relativePath:'original.pcap'};
  const opened=await invoke('openArtifact',picked);
  if(!opened.opened || fs.realpathSync(opens[0])!==fs.realpathSync(file))throw Error('Default app was not handed the original path');
  await invoke('openArtifact',{...picked,reveal:true});
  if(fs.realpathSync(reveals[0])!==fs.realpathSync(file))throw Error('Explorer did not receive original file');
  result.checks.native_default_app_original=true;result.checks.native_reveal_original=true;
  result.checks.no_copy=JSON.stringify(beforeNames)===JSON.stringify(fs.readdirSync(task.session_root).sort());
  result.checks.hash_unchanged=sha(file)===beforeHash;
  if(!result.checks.no_copy || !result.checks.hash_unchanged)throw Error('Open/reveal modified or copied evidence');
  const callCount=opens.length;
  for(const invalid of ['../outside.pcap','original.pcap:stream','C:/outside.pcap','blocked.cmd']){
   let rejected=false;try{await invoke('openArtifact',{...picked,relativePath:invalid});}catch{rejected=true;}
   if(!rejected)throw Error('Unsafe file request accepted: '+invalid);
  }
  if(opens.length!==callCount)throw Error('Rejected paths reached native shell');
  result.checks.traversal_ads_scripts_rejected=true;
  const outside=path.join(path.dirname(task.session_root),'outside.pcap');fs.writeFileSync(outside,pcap());
  const link=path.join(task.session_root,'escape.pcap');
  try{
   fs.symlinkSync(outside,link);let rejected=false;try{await invoke('openArtifact',{...picked,relativePath:'escape.pcap'});}catch{rejected=true;}
   if(!rejected)throw Error('Escaping symlink accepted');result.checks.escaping_link_rejected=true;
  }catch(error){if(error.code==='EPERM')result.checks.escaping_link_rejected='OS symlink privilege unavailable; Python containment test covers this';else throw error;}
  const cancelled=await invoke('registerRoot');
  if(!cancelled.cancelled || !dialogs.at(-1).properties.includes('openDirectory'))throw Error('Native directory chooser cancellation invalid');
  const external=path.join(path.dirname(process.env.IOT_EXP_DESKTOP_WORKSPACE),'登记目录 有空格');fs.mkdirSync(external,{recursive:true});selection=external;
  const registered=await invoke('registerRoot');if(!registered.registered)throw Error('Chosen root not registered');
  result.checks.directory_selection=true;
  await main.webContents.executeJavaScript(`document.querySelector('.nav button:last-child')?.click()`);
  // Find the real History navigation button by its visible title, then use the
  // actual session/file/open controls to check that shell errors reach the UI.
  await main.webContents.executeJavaScript(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent.includes('实验记录'))?.click()`);
  for(let i=0;i<100;i++){
   const clicked=await main.webContents.executeJavaScript(`(()=>{const row=Array.from(document.querySelectorAll('.session-row')).find(button=>button.textContent.includes(${JSON.stringify(task.session_id)}));if(!row)return false;row.click();return true;})()`);
   if(clicked)break;await wait(100);
  }
  for(let i=0;i<100;i++){
   const clicked=await main.webContents.executeJavaScript(`(()=>{const button=Array.from(document.querySelectorAll('.file-list button')).find(button=>button.textContent.includes('original.pcap'));if(!button)return false;button.click();return true;})()`);
   if(clicked)break;await wait(100);
  }
  missingAssociation=true;
  await main.webContents.executeJavaScript(`Array.from(document.querySelectorAll('.preview-toolbar button')).find(button=>button.textContent==='本机打开')?.click()`);
  let errorVisible=false;
  for(let i=0;i<100;i++){errorVisible=await main.webContents.executeJavaScript(`document.body.innerText.includes('没有可打开此文件的默认应用')`);if(errorVisible)break;await wait(100);}
  if(!errorVisible)throw Error('Missing default application error is not visible in actual React UI');
  result.checks.missing_association_error_visible=true;
  result.original_sha256=beforeHash;result.file=file;
  fs.writeFileSync(path.join(state,'file-actions.png'),(await main.webContents.capturePage()).toPNG());
  fs.writeFileSync(path.join(state,'file-actions-result.json'),JSON.stringify(result,null,2));
 }catch(error){fs.writeFileSync(path.join(state,'file-actions-error.txt'),error.stack);process.exitCode=2;}
 finally{fs.writeFileSync(path.join(state,'instance-probe-stop'),'');}
});
