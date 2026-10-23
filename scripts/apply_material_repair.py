"""Apply verified local extraction repair after taking a production checkpoint."""
from pathlib import Path
import sys,os,json,shutil,sqlite3,hashlib,time
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/parse-repair-release-20261021';CLONE=ROOT/'data/parse-repair-20261021'
checkpoint=json.loads((OUT/'checkpoint.json').read_text(encoding='utf-8'))
verified=json.loads((CLONE/'repair-report.json').read_text(encoding='utf-8'))
assert verified['original_sources_unchanged'] and verified['original_page_ids_preserved']
for folder in ['.runtime/python-deps','.runtime/advanced-deps','backend']:sys.path.insert(0,str(ROOT/folder))
from app import db,parsers,security,provider,material_quality,tasks
assert security.DATA==ROOT/'data'
assert not db.rows("SELECT id FROM tasks WHERE status IN ('queued','running')")
provider.generate=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Extraction repair cannot call the model API'))
db.init()
source=sqlite3.connect(checkpoint['backup']);source.row_factory=sqlite3.Row
old_sources={r['id']:dict(r) for r in source.execute('SELECT * FROM sources')}
old_pages={r['id']:dict(r) for r in source.execute('SELECT * FROM pages')};source.close()
files=db.rows('SELECT * FROM files WHERE active=1 ORDER BY name');sha_set={f['sha256'] for f in files}
if (CLONE/'ocr-cache').is_dir():shutil.copytree(CLONE/'ocr-cache',security.DATA/'ocr-cache',dirs_exist_ok=True)
for path in (CLONE/'previews').glob('*'):
    if path.is_file() and any(path.name.startswith(sha+'-office') for sha in sha_set):shutil.copy2(path,security.DATA/'previews'/path.name)
report={'version':'0.3.1','started_at':db.now(),'files':[],'external_model_requests':0}
for i,file in enumerate(files):
    before=db.one('SELECT SUM(length(text)) chars FROM pages WHERE file_id=?',(file['id'],))['chars'] or 0
    queued=tasks.enqueue(file['project_id'],'parse',{'file_id':file['id'],'repair':True})
    assert tasks.run_one()
    task=db.one('SELECT * FROM tasks WHERE id=?',(queued['id'],))
    assert task['status']=='succeeded',task.get('error')
    after=db.one('SELECT SUM(length(text)) chars FROM pages WHERE file_id=?',(file['id'],))['chars'] or 0
    quality=material_quality.summary(db.one('SELECT * FROM files WHERE id=?',(file['id'],)))
    entry={'file_id':file['id'],'name':file['name'],'task_id':queued['id'],'before_chars':before,'after_chars':after,'quality':quality}
    report['files'].append(entry);(OUT/'repair-progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'completed':i+1,'total':len(files),'name':file['name'],'quality':quality['state']},ensure_ascii=False),flush=True)
def digest_rows(table):
    rows=[json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':')) for r in db.rows('SELECT * FROM '+table)]
    return hashlib.sha256(('\n'.join(sorted(rows))).encode()).hexdigest()
current_sources={r['id']:r for r in db.rows('SELECT * FROM sources')}
current_pages={r['id']:r for r in db.rows('SELECT * FROM pages')}
report['original_sources_unchanged']=all(current_sources.get(sid)==row for sid,row in old_sources.items())
report['original_page_ids_preserved']=set(old_pages).issubset(current_pages)
report['manual_reviews_preserved']=all(current_pages[pid]['review_state']==row['review_state'] and current_pages[pid]['review_note']==row['review_note'] for pid,row in old_pages.items())
report['original_page_text_preserved_as_prefix']=all(current_pages[pid]['text'].startswith(row['text']) for pid,row in old_pages.items())
report['protected_tables_unchanged']={table:digest_rows(table)==wanted for table,wanted in checkpoint['before_tables'].items()}
report['credential_unchanged']=(hashlib.sha256((security.DATA/'credential.dpapi').read_bytes()).hexdigest() if (security.DATA/'credential.dpapi').exists() else None)==checkpoint['credential_sha256']
report['provider_unchanged']=hashlib.sha256(db.one("SELECT value FROM settings WHERE key='provider'")['value'].encode()).hexdigest()==checkpoint['provider_sha256']
report['old_source_count']=len(old_sources);report['added_source_count']=len(current_sources)-len(old_sources)
report['before_chars']=sum(f['before_chars'] for f in report['files']);report['after_chars']=sum(f['after_chars'] for f in report['files'])
report['ocr_pages']=sum(f['quality']['ocr_completed_pages'] for f in report['files']);report['failed_pages']=sum(f['quality']['ocr_failed_pages'] for f in report['files']);report['pending_pages']=sum(f['quality']['ocr_pending_pages'] for f in report['files'])
report['completed_at']=db.now()
assert all(report[k] for k in ['original_sources_unchanged','original_page_ids_preserved','manual_reviews_preserved','original_page_text_preserved_as_prefix','credential_unchanged','provider_unchanged'])
assert all(report['protected_tables_unchanged'].values())
report['index_tasks']=[tasks.enqueue(pid,'advanced_index',{})['id'] for pid in sorted({f['project_id'] for f in files})]
(OUT/'repair-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ['version','added_source_count','before_chars','after_chars','ocr_pages','failed_pages','pending_pages','credential_unchanged','provider_unchanged','original_sources_unchanged','original_page_ids_preserved','manual_reviews_preserved']},ensure_ascii=False,indent=2),flush=True)
