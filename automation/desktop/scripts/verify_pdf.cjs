// Run with the pinned Electron executable. The real main/preload/protocol are
// reused; all generated files belong to an isolated simulated session.
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs');const path=require('node:path');
const {wait}=require('../src/backend.cjs');
process.argv.push('--instance-probe');
require('../src/main.cjs');
function pdf(){
 const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
  '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 160] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
  '<< /Length 48 >>\nstream\nBT /F1 24 Tf 30 90 Td (Preview test) Tj ET\nendstream',
  '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
 let value='%PDF-1.4\n',offsets=[0];
 for(let i=0;i<objects.length;i++){offsets.push(Buffer.byteLength(value));value+=`${i+1} 0 obj\n${objects[i]}\nendobj\n`;}
 const start=Buffer.byteLength(value);value+='xref\n0 6\n0000000000 65535 f \n';
 for(const offset of offsets.slice(1))value+=String(offset).padStart(10,'0')+' 00000 n \n';
 return value+`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
}
app.whenReady().then(async()=>{
 const state=process.env.IOT_EXP_DESKTOP_STATE;
 try{
  for(let i=0;i<1200 && !fs.existsSync(path.join(state,'instance-probe-ready.json'));i++)await wait(100);
  const main=BrowserWindow.getAllWindows()[0];
  const request=input=>main.webContents.executeJavaScript(`window.iotDesktop.request(${JSON.stringify(input)})`);
  const [created]=await request({url:'/api/v1/tasks',method:'POST',body:JSON.stringify({client_request_id:'pdf-'+Date.now(),tasks:[{template_id:'mi_desk_lamp_1s',mode:'simulate',repetitions:1,idle_min_seconds:0,idle_max_seconds:0,cooldown_seconds:0}]})});
  let task;
  for(let i=0;i<200;i++){task=await request({url:'/api/v1/tasks/'+created.id});if(task.status==='completed')break;await wait(100);}
  if(task.status!=='completed')throw Error('Simulation did not complete');
  const file=path.join(task.session_root,'preview.pdf');fs.writeFileSync(file,pdf());
  const original=fs.readFileSync(file);
  await main.webContents.executeJavaScript(`window.iotDesktop.previewArtifact(${JSON.stringify({sessionId:task.session_id,category:'artifacts',relativePath:'preview.pdf'})})`);
  const viewer=BrowserWindow.getAllWindows().find(window=>window!==main);
  await wait(3000);
  const frames=viewer.webContents.mainFrame.framesInSubtree.map(frame=>frame.url);
  fs.writeFileSync(path.join(state,'pdf-preview.png'),(await viewer.webContents.capturePage()).toPNG());
  const result={frames,unchanged:original.equals(fs.readFileSync(file)),bridge:await viewer.webContents.executeJavaScript('!!window.iotDesktop')};
  fs.writeFileSync(path.join(state,'pdf-result.json'),JSON.stringify(result,null,2));
  viewer.destroy();fs.writeFileSync(path.join(state,'instance-probe-stop'),'');
 }catch(error){fs.writeFileSync(path.join(state,'pdf-error.txt'),error.stack);fs.writeFileSync(path.join(state,'instance-probe-stop'),'');}
});
