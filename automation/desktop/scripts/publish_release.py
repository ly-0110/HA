from __future__ import annotations
import hashlib,json,os,subprocess
from pathlib import Path

REPO='ly-0110/HA'
BRANCH='codex/release-desktop-v0.1.4'
VERSION='0.1.4'
TAG='v'+VERSION

def run(args,**kwargs):return subprocess.run(args,check=True,text=True,**kwargs)

def main():
    assets=Path('release-assets').resolve()
    entries=[json.loads((assets/name).read_text()) for name in ('linux-x64.json','windows-x64.json')]
    for e in entries:
        assert e['version']==VERSION
        p=assets/e['installer']
        assert p.stat().st_size==e['bytes']
        assert hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==e['sha256']
    manifest={'product':'IoT 实验工作台','version':VERSION,'source_commit':os.environ['GITHUB_SHA'],'installers':entries}
    sums=''.join(f"{e['sha256']}  {e['installer']}\n" for e in entries)
    run(['git','fetch','origin',BRANCH])
    run(['git','checkout','-B',BRANCH,'origin/'+BRANCH])
    for directory in (Path('.'),assets):
        (directory/'release.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (directory/'SHA256SUMS.txt').write_text(sums,encoding='utf-8')
    run(['git','add','release.json','SHA256SUMS.txt'])
    changed=subprocess.run(['git','diff','--cached','--quiet']).returncode
    if changed:
        run(['git','-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com','commit','-m','记录双端0.1.4安装包与校验值'])
        run(['git','push','origin',BRANCH])
    exists=subprocess.run(['gh','release','view',TAG,'--repo',REPO],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    if not exists:
        run(['gh','release','create',TAG,'--repo',REPO,'--target',BRANCH,'--title','IoT 实验工作台 '+VERSION,'--notes-file','README.md','--draft'])
    run(['gh','release','upload',TAG,'--repo',REPO,'--clobber',*[str(assets/e['installer']) for e in entries],str(assets/'release.json'),str(assets/'SHA256SUMS.txt')])
    run(['gh','release','edit',TAG,'--repo',REPO,'--draft=false','--latest'])
    print('Published https://github.com/'+REPO+'/releases/tag/'+TAG)

if __name__=='__main__':main()
