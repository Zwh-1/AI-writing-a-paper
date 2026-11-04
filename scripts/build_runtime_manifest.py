"""Write public, content-bound build identity without loading application data.

Run before a frontend build with --frontend, then once after it for the final
package identity. Generated manifests are excluded from their own fingerprints.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def write_manifest(root, output=None, *, frontend=False, require_clean=False, reuse_source=False):
    root=Path(root).resolve()
    if str(root/'backend') not in sys.path:sys.path.insert(0,str(root/'backend'))
    from app.runtime_identity import build_manifest
    if require_clean:
        changed=subprocess.run(['git','-C',str(root),'status','--porcelain','--untracked-files=all'],
            capture_output=True,text=True,encoding='utf-8',errors='replace',check=True,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.splitlines()
        allowed={'build-info.json','frontend/public/build-info.json'}
        changed=[line for line in changed if line[3:].replace('\\','/') not in allowed]
        if changed:raise ValueError('Release identity requires a clean committed source checkout')
    value=build_manifest(root,include_frontend=not frontend)
    previous_path=root/'frontend/public/build-info.json'
    if reuse_source and previous_path.is_file():
        previous=json.loads(previous_path.read_text(encoding='utf-8'))
        if (previous.get('schema')!=value.get('schema') or
                previous.get('version')!=value.get('version') or
                previous.get('source_fingerprint')!=value.get('source_fingerprint')):
            raise ValueError('Provided source identity does not match this build context')
        # Docker does not receive .git. The verified source identity may carry
        # its release commit; never borrow a commit from different file bytes.
        value['source_commit']=previous.get('source_commit')
        value['source_dirty']=previous.get('source_dirty')
    target=Path(output).resolve() if output else root/'build-info.json'
    protected={path.resolve() for path in (root/'backend/app/config.py',root/'frontend/package.json',
        root/'frontend/package-lock.json',root/'runner.py')}
    if target in protected or target.suffix.lower()!='.json':
        raise ValueError('Build identity output must be a dedicated JSON file')
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    targets=[target,root/'frontend/public/build-info.json'] if frontend else [target]
    for path in targets:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name(path.name+'.tmp')
        temporary.write_text(raw,encoding='utf-8')
        os.replace(temporary,path)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--frontend',action='store_true',help='Emit the prebuild frontend identity as well')
    parser.add_argument('--require-clean',action='store_true')
    parser.add_argument('--reuse-source',action='store_true',help='Preserve a supplied commit only after its source fingerprint matches')
    options=parser.parse_args(argv)
    result=write_manifest(options.root,options.output,frontend=options.frontend,require_clean=options.require_clean,reuse_source=options.reuse_source)
    print(json.dumps({key:result.get(key) for key in ('schema','version','source_commit','source_dirty',
        'source_fingerprint','frontend_fingerprint')},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
