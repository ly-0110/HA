const path=require('node:path');
const {execFile}=require('node:child_process');
const {promisify}=require('node:util');
const run=promisify(execFile);
const signed=process.platform==='win32' && !!process.env.WINDOWS_CERTIFICATE_FILE;
module.exports={
  packagerConfig:{
    asar:true,name:'IoTExperimentWorkbench',executableName:'IoTExperimentWorkbench',
    icon:path.join(__dirname,'assets/workbench'),
    appBundleId:'org.iotexp.workbench',
    download:{cacheRoot:path.join(__dirname,'../runs/electron-package-cache')},
    extraResource:[path.join(__dirname,'build-resources/web'),path.join(__dirname,'build-resources/templates'),path.join(__dirname,'build-resources/runtime'),path.join(__dirname,'build-resources/runtime-manifest.json'),path.join(__dirname,'build-resources/licenses'),path.join(__dirname,'build-resources/sbom.json'),path.join(__dirname,'build-resources',process.platform==='win32'?'iot-exp.cmd':'iot-exp.sh')],
    ignore:[/^\/build-resources/,/^\/runtime-appium/,/^\/scripts/,/^\/tests/,/^\/out/],
  },
  makers:[
    {name:'@electron-forge/maker-squirrel',config:{name:'IoTExperimentWorkbench',setupIcon:path.join(__dirname,'assets/workbench.ico'),...(signed?{windowsSign:{}}:{})}},
    {name:'@electron-forge/maker-deb',config:{options:{bin:'IoTExperimentWorkbench',icon:path.join(__dirname,'assets/workbench.png'),maintainer:'IoT Experiment Workbench maintainers',homepage:'https://github.com/ly-0110/HA',scripts:{preinst:path.join(__dirname,'installer/preinst'),prerm:path.join(__dirname,'installer/preinst')}}}},
  ],
  hooks:{postPackage:async(_configuration,result)=>{
    const {flipFuses,FuseVersion,FuseV1Options}=require('@electron/fuses');
    for(const directory of result.outputPaths){
      const binary=path.join(directory,process.platform==='win32'?'IoTExperimentWorkbench.exe':'IoTExperimentWorkbench');
      await flipFuses(binary,{version:FuseVersion.V1,[FuseV1Options.RunAsNode]:false,[FuseV1Options.EnableNodeOptionsEnvironmentVariable]:false,[FuseV1Options.EnableNodeCliInspectArguments]:false,[FuseV1Options.OnlyLoadAppFromAsar]:true});
      if(signed){const {sign}=await import('@electron/windows-sign');await sign({files:[binary]});}
    }
  },postMake:async(_configuration,results)=>{
    for(const result of results){
      if(result.platform!=='win32')continue;
      const setup=result.artifacts.find(file=>file.endsWith('Setup.exe'));
      if(!setup)throw Error('Squirrel setup artifact missing');
      const compiler=path.join(process.env.WINDIR || 'C:/Windows','Microsoft.NET/Framework64/v4.0.30319/csc.exe');
      const output=path.join(path.dirname(setup),'IoTExperimentWorkbench-Guarded-Setup.exe');
      await run(compiler,['/nologo','/target:winexe','/platform:x64','/r:System.Windows.Forms.dll','/r:System.Web.Extensions.dll','/win32icon:'+path.join(__dirname,'assets/workbench.ico'),'/out:'+output,'/resource:'+setup+',SetupPayload',path.join(__dirname,'installer/InstallGate.cs')]);
      if(signed){const {sign}=await import('@electron/windows-sign');await sign({files:[output]});}
      result.artifacts=result.artifacts.filter(file=>file!==setup).concat(output);
    }
    return results;
  }},
};
