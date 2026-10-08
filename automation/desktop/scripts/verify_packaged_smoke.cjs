// Run the existing sandboxed main smoke against actual packaged ASAR/resources.
// This verifies package content, not installation or another OS/user account.
const fs=require('node:fs');
const originalFS=require('original-fs');
const path=require('node:path');
const crypto=require('node:crypto');
const packaged=process.env.IOT_EXP_PACKAGED_SMOKE_APP;
const state=process.env.IOT_EXP_DESKTOP_STATE;
const workspace=process.env.IOT_EXP_DESKTOP_WORKSPACE;
for(const [name,value] of Object.entries({packaged,state,workspace})){
  if(!value || !path.isAbsolute(value))throw Error('Set an absolute '+name+' path for this isolated test.');
}
const resources=path.join(packaged,'resources');
const asar=path.join(resources,'app.asar');
if(fs.existsSync(path.join(state,'smoke-result.json')))throw Error('Choose a fresh test state directory.');
const manifest=JSON.parse(fs.readFileSync(path.join(resources,'runtime-manifest.json'),'utf8'));
fs.mkdirSync(state,{recursive:true});
fs.writeFileSync(path.join(state,'packaged-smoke-input.json'),JSON.stringify({
  packaged,resources,bundle_id:manifest.bundle_id,
  asar_sha256:crypto.createHash('sha256').update(originalFS.readFileSync(asar)).digest('hex'),
  launcher:'same-version-development-electron',installed_application:false,
  ordinary_user_install_validated:false,
},null,2));
process.env.IOT_EXP_DESKTOP_RESOURCES=resources;
process.argv.push('--smoke-test');
require(path.join(asar,'src','main.cjs'));
