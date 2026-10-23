from pathlib import Path
import sys,json,sqlite3,hashlib,time
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/parse-repair-release-20261021'
for folder in ['.runtime/python-deps','.runtime/advanced-deps','backend']:sys.path.insert(0,str(ROOT/folder))
import httpx
from app import db,material_quality
with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=20) as client:
    session=json.loads((ROOT/'.runtime/session-8765.json').read_text(encoding='utf-8'))
    auth=client.post('/api/v1/session',json={'token':session['bootstrap_token']});auth.raise_for_status();client.headers['X-CSRF-Token']=auth.json()['csrf']
    f=db.one("SELECT * FROM files WHERE name LIKE '%endnote%'")
    response=client.post('/api/v1/files/'+f['id']+'/reparse');response.raise_for_status();task_id=response.json()['id']
    def wait(tid):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            response=client.get('/api/v1/tasks/'+tid);response.raise_for_status();task=response.json()
            if task['status']=='succeeded':return task
            if task['status'] in ('failed','cancelled','outcome_unknown'):raise ValueError(task.get('error'))
            time.sleep(.3)
        raise TimeoutError('Local repair task did not finish')
    completed=wait(task_id)
    quality=client.get('/api/v1/files/'+f['id']+'/parse-report').json()
    assert quality['state']=='extracted' and quality['ocr_failed_pages']==0
    index=__import__('app.tasks',fromlist=['enqueue']).enqueue(f['project_id'],'advanced_index',{})
    index_result=wait(index['id'])
    health=client.get('/api/v1/health').json();assert health['version']=='0.3.1' and health['status']=='ok'
checkpoint=json.loads((OUT/'checkpoint.json').read_text(encoding='utf-8'))
report=json.loads((OUT/'repair-report.json').read_text(encoding='utf-8'))
backup=sqlite3.connect(checkpoint['backup']);backup.row_factory=sqlite3.Row
old_sources={r['id']:dict(r) for r in backup.execute('SELECT * FROM sources')};old_pages={r['id']:dict(r) for r in backup.execute('SELECT * FROM pages')};backup.close()
current_sources={r['id']:r for r in db.rows('SELECT * FROM sources')};current_pages={r['id']:r for r in db.rows('SELECT * FROM pages')}
report['original_sources_unchanged']=all(current_sources.get(sid)==row for sid,row in old_sources.items());report['original_page_ids_preserved']=set(old_pages).issubset(current_pages)
report['manual_reviews_preserved']=all(current_pages[pid]['review_state']==row['review_state'] and current_pages[pid]['review_note']==row['review_note'] for pid,row in old_pages.items())
report['original_page_text_preserved_as_prefix']=all(current_pages[pid]['text'].startswith(row['text']) for pid,row in old_pages.items())
def digest_rows(table):
 rows=[json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':')) for r in db.rows('SELECT * FROM '+table)]
 return hashlib.sha256(('\n'.join(sorted(rows))).encode()).hexdigest()
report['protected_tables_unchanged']={table:digest_rows(table)==wanted for table,wanted in checkpoint['before_tables'].items()}
report['credential_unchanged']=(hashlib.sha256((ROOT/'data/credential.dpapi').read_bytes()).hexdigest() if (ROOT/'data/credential.dpapi').exists() else None)==checkpoint['credential_sha256'];report['provider_unchanged']=hashlib.sha256(db.one("SELECT value FROM settings WHERE key='provider'")['value'].encode()).hexdigest()==checkpoint['provider_sha256']
for entry in report['files']:
 file=db.one('SELECT * FROM files WHERE id=?',(entry['file_id'],));entry['quality']=material_quality.summary(file);entry['after_chars']=db.one('SELECT SUM(length(text)) chars FROM pages WHERE file_id=?',(file['id'],))['chars'] or 0
report['after_chars']=sum(f['after_chars'] for f in report['files']);report['ocr_pages']=sum(f['quality']['ocr_completed_pages'] for f in report['files']);report['failed_pages']=sum(f['quality']['ocr_failed_pages'] for f in report['files']);report['pending_pages']=sum(f['quality']['ocr_pending_pages'] for f in report['files'])
report['added_source_count']=len(current_sources)-len(old_sources);report['embedded_docx_sources']=db.one("SELECT COUNT(*) n FROM sources WHERE method='embedded_native'")['n'];report['neural_vectors']=db.one('SELECT COUNT(*) n FROM neural_vectors')['n'];report['incremental_index_new_sources']=index_result['result'].get('indexed');report['followup_docx_task']=task_id;report['final_index_task']=index['id'];report['completed_at']=db.now()
assert all(report[k] for k in ['original_sources_unchanged','original_page_ids_preserved','manual_reviews_preserved','original_page_text_preserved_as_prefix','credential_unchanged','provider_unchanged'])
assert all(report['protected_tables_unchanged'].values()) and report['failed_pages']==0 and report['pending_pages']==0
(OUT/'repair-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ['version','added_source_count','before_chars','after_chars','ocr_pages','failed_pages','pending_pages','embedded_docx_sources','neural_vectors','credential_unchanged','provider_unchanged','original_sources_unchanged','original_page_ids_preserved','manual_reviews_preserved']},ensure_ascii=False,indent=2))
