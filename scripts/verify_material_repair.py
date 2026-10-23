"""Run real-document extraction against a key-free local clone."""
from pathlib import Path
import os,sys,json,sqlite3,hashlib,time
ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'data'/'parse-repair-20261021'
DEST.mkdir(exist_ok=True)
source=sqlite3.connect(str(ROOT/'data/workbench.sqlite3'))
target=sqlite3.connect(str(DEST/'workbench.sqlite3'))
source.backup(target);source.close();target.row_factory=sqlite3.Row
original_sources={r['id']:dict(r) for r in target.execute('SELECT * FROM sources')}
original_page_ids={r['id'] for r in target.execute('SELECT id FROM pages')}
blobs={r['sha256'] for r in target.execute('SELECT sha256 FROM files')}
for row in target.execute('SELECT images FROM pages'):
    blobs.update(x['sha256'] for x in json.loads(row['images']))
objects=DEST/'objects';objects.mkdir(exist_ok=True)
for sha in blobs:
    original=ROOT/'data/objects'/sha;out=objects/sha
    if original.is_file() and not out.exists():os.link(original,out)
target.execute("DELETE FROM settings WHERE key IN ('provider','account_usage','account_usage_history','capabilities')")
target.commit();target.close()
os.environ['PAPER_DATA_DIR']=str(DEST)
os.environ.pop('PAPER_API_KEY',None);os.environ.pop('PAPER_API_KEY_FILE',None)
for path in ['.runtime/python-deps','.runtime/advanced-deps','backend']:sys.path.insert(0,str(ROOT/path))
from app import db,parsers,provider,material_quality
provider.generate=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No paid generation allowed during local extraction'))
db.init();files=db.rows('SELECT * FROM files WHERE active=1 ORDER BY name')
report={'started_at':db.now(),'data_dir':str(DEST),'files':[],'external_model_requests':0}
for i,file in enumerate(files):
    started=time.time();before=db.one('SELECT SUM(length(text)) chars FROM pages WHERE file_id=?',(file['id'],))['chars'] or 0
    print(json.dumps({'file':i+1,'total':len(files),'name':file['name'],'phase':'start'},ensure_ascii=False),flush=True)
    try:
        result=parsers.parse_file(file['id'],repair=True)
        after=db.one('SELECT SUM(length(text)) chars FROM pages WHERE file_id=?',(file['id'],))['chars'] or 0
        record={'file_id':file['id'],'name':file['name'],'before_chars':before,'after_chars':after,'seconds':round(time.time()-started,2),'quality':result['quality']}
    except Exception as error:record={'file_id':file['id'],'name':file['name'],'error':str(error)}
    report['files'].append(record)
    (DEST/'repair-progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False),flush=True)
now_sources={r['id']:r for r in db.rows('SELECT * FROM sources')}
report['original_sources_unchanged']=all(now_sources.get(sid)==row for sid,row in original_sources.items())
report['original_page_ids_preserved']=original_page_ids.issubset({r['id'] for r in db.rows('SELECT id FROM pages')})
report['old_source_count']=len(original_sources);report['new_source_count']=len(now_sources)-len(original_sources)
report['completed_at']=db.now()
(DEST/'repair-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
assert report['original_sources_unchanged'] and report['original_page_ids_preserved']
assert not any('error' in record for record in report['files'])
print(json.dumps({'report':str(DEST/'repair-report.json'),'original_sources_unchanged':True,'original_page_ids_preserved':True,'new_sources':report['new_source_count']},ensure_ascii=False),flush=True)
