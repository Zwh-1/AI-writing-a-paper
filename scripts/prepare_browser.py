"""Bundle public Node/Playwright/browser distributions. Never copies user browser profiles."""
from pathlib import Path
import argparse,json,os,re,shutil
ROOT=Path(__file__).resolve().parents[1]
def copy_runtime(node,modules,browsers):
    target=ROOT/'.runtime/browser';target.mkdir(parents=True,exist_ok=True);module_target=target/'node_modules';module_target.mkdir(exist_ok=True)
    def ignored(directory,names):
        return [n for n in names if n in ('User Data','UserData','Crashpad','Crash Reports','.git','__pycache__') or (Path(directory)/n).is_symlink() or (hasattr(Path(directory)/n,'is_junction') and (Path(directory)/n).is_junction())]
    for name in ('playwright','playwright-core'):
        source=modules/name
        if not (source/'package.json').exists():raise ValueError('Missing approved Playwright distribution')
        shutil.copytree(source,module_target/name,dirs_exist_ok=True,ignore=ignored)
    metadata=json.loads((modules/'playwright-core/browsers.json').read_text(encoding='utf-8'));installed=[];dest=ROOT/'.runtime/browsers';dest.mkdir(exist_ok=True)
    for item in metadata['browsers']:
        if item['name'] not in ('chromium','chromium-headless-shell','ffmpeg') or not re.fullmatch(r'\d+',item['revision']):continue
        for name in (item['name'].replace('-','_')+'-'+item['revision'],item['name']+'-'+item['revision']):
            source=browsers/name
            if source.is_dir() and not source.is_symlink():shutil.copytree(source,dest/name,dirs_exist_ok=True,ignore=ignored);installed.append(name);break
    if not any(name.startswith('chromium') for name in installed):raise ValueError('No matching installed Chromium distribution')
    shutil.copyfile(node,target/'node.exe');version=json.loads((modules/'playwright/package.json').read_text(encoding='utf-8'))['version'];result={'playwright_version':version,'browser_directories':installed,'user_profiles_included':False};(target/'ready.json').write_text(json.dumps(result,indent=2),encoding='utf-8');return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',required=True);p.add_argument('--modules',required=True);p.add_argument('--browsers',required=True);a=p.parse_args();print(json.dumps(copy_runtime(Path(a.node),Path(a.modules),Path(a.browsers)),ensure_ascii=False))
