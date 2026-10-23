"""Read-only release verification. Human semantic acceptance is never inferred."""
import argparse
import hashlib
import json
import sqlite3
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DOCS=ROOT/'docs/writing-agent'

def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def source_files():
    result={}
    for folder in ('backend','frontend/src','prompts','scripts/writing-agent','tests'):
        for path in (ROOT/folder).rglob('*'):
            if path.is_file() and not set(path.parts)&{'__pycache__','.pytest_cache'} and path.suffix not in ('.pyc','.log'):
                result[path.relative_to(ROOT).as_posix()]=sha(path)
    for name in ('runner.py','scripts/package.py','启动论文工作台.ps1','停止论文工作台.ps1','打开真实验收审阅.ps1','frontend/package.json','frontend/package-lock.json','frontend/tsconfig.json','frontend/vite.config.ts','frontend/dist/index.html'):
        if (ROOT/name).exists():result[name]=sha(ROOT/name)
    return result

def database(folder):
    path=folder/'workbench.sqlite3';c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        version=c.execute('PRAGMA user_version').fetchone()[0];checks=[r[0] for r in c.execute('PRAGMA quick_check')]
        return {'version':version,'quick_check':checks,'foreign_key_violations':len(c.execute('PRAGMA foreign_key_check').fetchall()),'human_diagnostic_reviews':c.execute("SELECT COUNT(*) FROM diagnostic_reviews WHERE method='human_reviewed'").fetchone()[0],'model_diagnostic_reviews':c.execute("SELECT COUNT(*) FROM diagnostic_reviews WHERE method='model_assisted'").fetchone()[0]}
    finally:c.close()

def junit():
    tree=ET.parse(DOCS/'completion-final-tests.xml');suites=tree.findall('.//testsuite')
    return {key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}

def supplemental_tests():
    path=DOCS/'completion-billing-final-tests.xml'
    if not path.exists():return {'present':False}
    suites=ET.parse(path).findall('.//testsuite')
    return {'present':True,**{key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}}

def package(version):
    path=ROOT.parent/f'砚台论文工作台_{version}_便携版.zip'
    if not path.exists():return {'present':False}
    with zipfile.ZipFile(path) as archive:
        manifest=json.loads(archive.read('便携包清单.json').decode('utf-8-sig'));differences=[];bad_hashes=[]
        for relative,expected in manifest['files'].items():
            actual=hashlib.sha256(archive.read(relative)).hexdigest()
            if actual!=expected:bad_hashes.append(relative)
            current=ROOT/relative
            if current.exists() and (relative.startswith(('backend/','frontend/src/','prompts/','scripts/writing-agent/')) or relative in ('runner.py','frontend/dist/index.html')) and sha(current)!=actual:differences.append(relative)
        private=[n for n in archive.namelist() if n.startswith(('data/','.runtime/writing-v4-live-data/','.runtime/writing-v41-browser/')) or n.endswith('.dpapi') or Path(n).name=='workbench.sqlite3']
        return {'present':True,'path':str(path),'bytes':path.stat().st_size,'files':len(manifest['files']),'all_manifest_hashes_checked':True,'hash_failures':bad_hashes,'source_differences':differences,'private_data_paths':private,'credentials_included':manifest.get('api_key_included'),'crc_passed':archive.testzip() is None}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',action='store_true');parser.add_argument('--freeze',action='store_true');args=parser.parse_args()
    version=load(ROOT/'frontend/package.json')['version'];files=source_files();snapshot=DOCS/'completion-source-snapshot.json'
    if args.freeze:snapshot.write_text(json.dumps({'version':version,'files':files,'captured_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    changes=[]
    if snapshot.exists():
        frozen=load(snapshot)['files'];changes=[name for name in set(files)|set(frozen) if files.get(name)!=frozen.get(name)]
    matrix=load(DOCS/'completion-validation-matrix.json');browser=load(DOCS/'completion-browser-report.json');tests=junit()
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health',timeout=10) as r:health=json.load(r)
    except Exception as error:health={'status':'unavailable','error':type(error).__name__}
    real=load(DOCS/'completion-real-acceptance.json');experiment=database(ROOT/'.runtime/writing-v4-live-data')
    quality='human_review_pending' if not experiment['human_diagnostic_reviews'] else 'requires_explicit_policy_and_thesis_review'
    supplement=supplemental_tests();post_changes=DOCS/'completion-post-regression-changes.json'
    result={'version':version,'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'software release invariants and actual execution records; no inferred human quality approval','tests':tests,'supplemental_billing_tests':supplement,'post_full_regression_changes':load(post_changes) if post_changes.exists() else None,'browser':browser,'validation_matrix':{'requirements':matrix['count'],'all_programmatic_gates_passed':matrix['all_programmatic_gates_passed']},'current_service':health,'main_database':database(ROOT/'data'),'experiment_database':experiment,'source_files_checked':len(files),'changes_since_source_snapshot':sorted(changes),'real_trial_budget':real.get('all_isolated_budget'),'quality_acceptance':quality,'human_acceptance_fabricated':False,'paid_requests_made_by_this_script':0}
    if args.package:result['package']=package(version)
    result['software_checks_passed']=not any(tests[k] for k in ('failures','errors','skipped')) and browser.get('passed') and matrix['all_programmatic_gates_passed'] and health.get('version')==version and not changes and result['main_database']['quick_check']==['ok'] and result['main_database']['foreign_key_violations']==0
    if supplement.get('present'):result['software_checks_passed']=result['software_checks_passed'] and not any(supplement[k] for k in ('failures','errors','skipped'))
    if args.package:result['software_checks_passed']=result['software_checks_passed'] and result['package'].get('present') and result['package'].get('crc_passed') and not result['package'].get('hash_failures') and not result['package'].get('source_differences') and not result['package'].get('private_data_paths')
    (DOCS/'completion-release-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'version':version,'software_checks_passed':result['software_checks_passed'],'tests':tests,'source_changes':changes,'quality_acceptance':quality,'report':str(DOCS/'completion-release-validation.json')},ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['software_checks_passed'] else 1)

if __name__=='__main__':main()
