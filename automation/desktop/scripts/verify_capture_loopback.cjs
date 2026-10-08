// Thin real Electron GUI-process entry. No BrowserWindow, sandbox flags or hardware.
const {app}=require('electron');
const fs=require('node:fs');const path=require('node:path');const {spawn}=require('node:child_process');
const diagnostic=process.argv.includes('--signal-constant-diagnostic');
const output=process.env.IOT_EXP_LOOPBACK_TEST_ROOT ? path.resolve(process.env.IOT_EXP_LOOPBACK_TEST_ROOT) : path.resolve(__dirname,'../../runs/capture-loopback-'+Date.now()+(diagnostic?'-signal-diagnostic':''));
if(fs.existsSync(path.join(output,'result.json')))throw Error('Choose a new test output directory to preserve earlier evidence');
fs.mkdirSync(path.join(output,'electron-state'),{recursive:true});
app.setPath('userData',path.join(output,'electron-state'));
fs.writeFileSync(path.join(output,'entry.json'),JSON.stringify({electron_pid:process.pid,entry_loaded:true}));
app.whenReady().then(async()=>{
 const resources=path.resolve(__dirname,'../build-resources');
 const manifest=JSON.parse(fs.readFileSync(path.join(resources,'runtime-manifest.json'),'utf8'));
 fs.mkdirSync(output,{recursive:true});
 const environment={...process.env,PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'};
 for(const name of ['PYTHONPATH','PYTHONHOME','IOT_EXP_CONTEXT','IOT_EXP_INSTANCE_ID'])delete environment[name];
 const child=spawn(path.resolve(resources,manifest.executables.python),['-I','-B','-X','utf8',path.join(__dirname,'verify_capture_loopback.py'),output,...(diagnostic?['--signal-constant-diagnostic']:[])],{env:environment,windowsHide:true,stdio:['ignore','pipe','pipe']});
 let stdout='',stderr='';child.stdout.on('data',chunk=>stdout+=chunk);child.stderr.on('data',chunk=>stderr+=chunk);
 child.once('error',error=>{fs.writeFileSync(path.join(output,'electron-error.txt'),error.stack);app.exit(2);});
 child.once('exit',code=>{
  fs.writeFileSync(path.join(output,'electron-child.json'),JSON.stringify({electron_pid:process.pid,python_pid:child.pid,python_exit_code:code,bundle_id:manifest.bundle_id,windowsHide:true,stdout,stderr},null,2));
  app.exit(code || 0);
 });
});
