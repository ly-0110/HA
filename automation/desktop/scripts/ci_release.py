from __future__ import annotations
import hashlib,json,os,shutil,subprocess,sys
from pathlib import Path

DESKTOP=Path(__file__).resolve().parents[1]
AUTOMATION=DESKTOP.parent

def run(args,cwd=None,env=None,timeout=None):
    subprocess.run([str(x) for x in args],cwd=cwd or DESKTOP,env=env,check=True,timeout=timeout)

def main():
    sys.path.insert(0,str(DESKTOP/'scripts'))
    from build_runtime import download,extract
    target='windows-x64' if os.name=='nt' else 'linux-x64'
    lock=json.loads((DESKTOP/'runtime-inputs.lock.json').read_text())
    tool=lock['build_tools']['windows_uv' if os.name=='nt' else 'linux_uv']
    tools=AUTOMATION/'runs/ci-build-tools/uv'
    extract(download(tool,AUTOMATION/'runs/runtime-assets'),tools)
    uv=next(tools.rglob('uv.exe' if os.name=='nt' else 'uv'))
    if os.name!='nt':uv.chmod(0o755)
    run([uv,'sync','--extra','dev','--locked'],AUTOMATION)
    run([uv,'run','ruff','check','src','tests'],AUTOMATION)
    run([uv,'run','pytest'],AUTOMATION)
    run([uv,'run','python',DESKTOP/'scripts/build_runtime.py','--uv',uv],AUTOMATION)
    run([uv,'run','python',DESKTOP/'scripts/build_notices.py'],AUTOMATION)
    run([uv,'run','python',DESKTOP/'scripts/collect_python_licenses.py'],AUTOMATION)
    npm_path=Path(shutil.which('npm.cmd' if os.name=='nt' else 'npm'))
    npm=[shutil.which('node'),npm_path.parent/'node_modules/npm/bin/npm-cli.js'] if os.name=='nt' else [npm_path]
    run([*npm,'run','package'])
    packaged=next(p for p in (DESKTOP/'out').iterdir() if p.is_dir() and p.name.endswith('win32-x64' if os.name=='nt' else 'linux-x64'))
    state=AUTOMATION/'runs/ci-smoke-state';workspace=AUTOMATION/'runs/ci-smoke-workspace'
    env={**os.environ,'IOT_EXP_CI_HEADLESS':'1','IOT_EXP_DESKTOP_STATE':str(state),'IOT_EXP_DESKTOP_WORKSPACE':str(workspace),'IOT_EXP_LOCK_ROOT':str(AUTOMATION/'runs/ci-smoke-locks')}
    binary=packaged/('IoTExperimentWorkbench.exe' if os.name=='nt' else 'IoTExperimentWorkbench')
    if os.name=='nt':
        run([binary,'--smoke-test','--disable-gpu'],env=env,timeout=240)
    else:
        helper=packaged/'chrome-sandbox'
        run(['sudo','chown','root:root',helper]);run(['sudo','chmod','4755',helper])
        run(['xvfb-run','-a',binary,'--smoke-test','--disable-gpu'],env=env,timeout=240)
    smoke=json.loads((state/'smoke-result.json').read_text())
    assert smoke['ready'] and smoke['simulation']=='completed' and smoke['events']==2,smoke
    assert smoke['text_preview'] and smoke['image_preview'] and smoke['original_file_unchanged'],smoke
    run([*npm,'run','make','--','--skip-package','--platform','win32' if os.name=='nt' else 'linux','--arch','x64'])
    version=json.loads((DESKTOP/'package.json').read_text())['version']
    output=DESKTOP/'release-assets';output.mkdir(exist_ok=True)
    if os.name=='nt':
        source=next((DESKTOP/'out/make').rglob('IoTExperimentWorkbench-Guarded-Setup.exe'))
        name=f'IoTExperimentWorkbench-{version}-Setup.exe'
    else:
        source=next((DESKTOP/'out/make').rglob('*.deb'))
        name=f'iot-experiment-workbench_{version}_amd64.deb'
    file=output/name;shutil.copy2(source,file)
    digest=hashlib.file_digest(file.open('rb'),'sha256').hexdigest()
    manifest=json.loads((DESKTOP/'build-resources/runtime-manifest.json').read_text())
    data={'product':'IoT 实验工作台','version':version,'platform':target,'installer':name,'bytes':file.stat().st_size,'sha256':digest,'source_commit':os.environ.get('GITHUB_SHA'),'runtime_versions':manifest['actual_versions'],'bundle_id':manifest['bundle_id']}
    (output/f'{target}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'artifact':str(file),'sha256':digest,'smoke_passed':True}))

if __name__=='__main__':main()
