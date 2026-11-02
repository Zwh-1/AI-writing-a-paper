"""Capture a coherent public source bundle while preserving the active workspace."""
import argparse,hashlib,importlib.util,json,os,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();stage=args.output.resolve()
    if not stage.is_relative_to(ROOT/'.runtime'):raise ValueError('Release staging must stay within this workspace runtime')
    if stage.exists():raise ValueError('Use a new staging directory; existing snapshots are never overwritten')
    spec=importlib.util.spec_from_file_location('release_package_source',ROOT/'scripts/package.py');package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
    for attempt in range(12):
        try:
            paths=list(package.selected_files('full',ROOT));sources=[p for p in paths if p.relative_to(ROOT).parts[0]!='.runtime']
            extras=[ROOT/'frontend/package-lock.json',ROOT/'scripts/package.py']
            sources=list(dict.fromkeys(sources+extras));contents={p.relative_to(ROOT).as_posix():p.read_bytes() for p in sources}
            fresh={p.relative_to(ROOT).as_posix() for p in package.selected_files('full',ROOT) if p.relative_to(ROOT).parts[0]!='.runtime'}|{p.relative_to(ROOT).as_posix() for p in extras}
            if fresh!=set(contents) or any((ROOT/name).read_bytes()!=data for name,data in contents.items()):raise RuntimeError('Source changed while capturing')
            if 'frontend/dist/index.html' not in contents or not any(name.startswith('frontend/dist/assets/') for name in contents):raise RuntimeError('Frontend build is not ready')
            break
        except (FileNotFoundError,RuntimeError) as error:
            if attempt==11:raise
            print(json.dumps({'phase':'waiting_for_coherent_source','attempt':attempt+1,'reason':str(error)},ensure_ascii=False),flush=True);time.sleep(2)
    stage.mkdir(parents=True);hashes={}
    for name,data in contents.items():
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);hashes[name]=hashlib.sha256(data).hexdigest()
    runtime=[p for p in paths if p.relative_to(ROOT).parts[0]=='.runtime']
    for source in runtime:
        target=stage/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
        try:os.link(source,target)
        except OSError:__import__('shutil').copy2(source,target)
    result={'source_root':str(ROOT),'stage':str(stage),'source_files':len(contents),'runtime_files':len(runtime),'source_hashes':hashes,'capture_consistent':True,'user_data_included':False}
    (stage/'release-source-snapshot.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='source_hashes'},ensure_ascii=False))
if __name__=='__main__':main()
