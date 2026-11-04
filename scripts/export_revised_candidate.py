"""Replay an independently revised frozen manuscript without adopting or billing.

Uses the application's native export pipeline and a read-only database. Historical
generation inputs, costs and review verdicts are never rewritten.
"""
import argparse,hashlib,json,shutil,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import runner
from app import db,documents,exports,security
from scripts.verify_whole_paper_candidate import read_only_database

def manuscript_hash(frozen):
    payload=[{'section':{k:s.get(k) for k in ('id','title','kind','position')},
              'document':db.loads(frozen['revisions'][s['id']]['document'],{})}
             for s in frozen['sections']]
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def validate(prepared):
    frozen=prepared['snapshot'];revision=prepared['revision']
    if revision.get('paper_hash')!=manuscript_hash(frozen):raise ValueError('Revised manuscript digest mismatch')
    refs={};seen=set()
    for sec in frozen['sections']:
        doc=db.loads(frozen['revisions'][sec['id']]['document'],{})
        for n in documents.nodes(doc):
            if n['type']=='citation':
                rid=n.get('attrs',{}).get('citationId')
                if rid not in frozen['references']:raise ValueError('Dangling bibliography identity: '+str(rid))
                refs.setdefault(rid,len(refs)+1)
            if n['type']!='text':
                identity=n.get('attrs',{}).get('nodeId')
                if identity:
                    if identity in seen:raise ValueError('Duplicate revised node identity: '+identity)
                    seen.add(identity)
    if len(refs)!=revision['expected_reference_count']:raise ValueError('Citation coverage differs from declared bibliography')
    return refs

def export(prepared,destination):
    validate(prepared)
    destination=Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):raise FileExistsError('Refusing to overwrite a populated delivery checkpoint')
    destination.mkdir(parents=True,exist_ok=True)
    old_data=exports.DATA
    try:
        with read_only_database(),tempfile.TemporaryDirectory(prefix='revised-export-',dir=destination.parent) as tmp:
            scratch=Path(tmp);(scratch/'exports').mkdir();(scratch/'tmp').mkdir()
            exports.DATA=scratch
            result=exports.export_project(prepared['snapshot']['project']['id'],{'mode':'draft','pdf':True,'include_toc':True,'cover_note':'论文修订稿　2026年11月4日'},prepared=prepared)
            for path in (scratch/'exports'/result['export_id']).iterdir():
                if path.is_file() and path.name!='论文交付包.zip':shutil.copyfile(path,destination/path.name)
    finally:exports.DATA=old_data
    return {'directory':str(destination),'revision':prepared['revision'],'native_export':result}

def main():
    p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();prepared=json.loads(args.snapshot.read_text('utf-8'))
    print(json.dumps(export(prepared,args.out),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
