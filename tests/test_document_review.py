import pytest
from test_workbench import client,project,file
from app import db,tasks,material_summary

def document(client,pid):
    f=file(client,pid,'原文核对测试：逐页核对与整份确认。');first=db.one('SELECT * FROM pages WHERE file_id=?',(f['id'],))
    for n in (2,3):
        page_id=db.uid();db.execute('INSERT INTO pages(id,file_id,page_number,text) VALUES(?,?,?,?)',(page_id,f['id'],n,'第'+str(n)+'页的提取原文'))
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),f['id'],page_id,db.dumps({'page':n}),'原文片段','原文片段','evidence','native',0))
    db.execute('UPDATE files SET pages=3 WHERE id=?',(f['id'],))
    return f,db.rows('SELECT * FROM pages WHERE file_id=? ORDER BY page_number',(f['id'],))

def confirm(client,f,pages,**extra):return client.post('/api/v1/files/'+f['id']+'/review',json={'confirm':True,'page_ids':[p['id'] for p in pages],**extra})

def test_document_review_updates_pending_pages_and_source_verification(client):
    pid=project(client);f,pages=document(client,pid);before=[p['text'] for p in pages];fingerprint=material_summary.source(f['id'])[2]
    response=confirm(client,f,pages);assert response.status_code==200,response.text
    result=response.json();assert result['updated_pages']==3 and result['summary_reviewed'] is False
    assert result['parse_report']['reviewed_pages']==3 and result['parse_report']['unreviewed_pages']==0
    assert all(p['review_state']=='reviewed' for p in result['pages'])
    assert [p['text'] for p in result['pages']]==before
    assert material_summary.source(f['id'])[2]==fingerprint
    assert all(s['verified']==1 for s in db.rows('SELECT * FROM sources WHERE file_id=?',(f['id'],)))
    assert not db.rows('SELECT * FROM generation_runs')
    assert db.one("SELECT * FROM audit WHERE action='document_original_reviewed'")
    again=confirm(client,f,pages);assert again.status_code==200 and again.json()['updated_pages']==0
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action='document_original_reviewed'")['n']==1

def test_document_review_preserves_exclusions_and_existing_notes(client):
    pid=project(client);f,pages=document(client,pid)
    client.patch('/api/v1/pages/'+pages[0]['id'],json={'review_state':'reviewed','review_note':'已逐字检查'})
    client.patch('/api/v1/pages/'+pages[1]['id'],json={'review_state':'not_applicable','review_note':'封面排除'})
    response=confirm(client,f,pages);assert response.status_code==200,response.text
    result=response.json();assert result['updated_pages']==1
    assert [p['review_state'] for p in result['pages']]==['reviewed','not_applicable','reviewed']
    assert result['pages'][0]['review_note']=='已逐字检查' and result['pages'][1]['review_note']=='封面排除'
    assert result['parse_report']['excluded_pages']==1 and result['parse_report']['reviewed_pages']==2
    assert db.one('SELECT verified FROM sources WHERE page_id=?',(pages[1]['id'],))['verified']==0

def test_document_review_only_affects_selected_document(client):
    pid=project(client);f,pages=document(client,pid);other=file(client,pid,'另一份文件')
    assert confirm(client,f,pages).status_code==200
    assert db.one('SELECT review_state FROM pages WHERE file_id=?',(other['id'],))['review_state']=='unreviewed'
    assert db.one('SELECT verified FROM sources WHERE file_id=?',(other['id'],))['verified']==0

@pytest.mark.parametrize('confirm_value',[False,None,1,'true'])
def test_document_review_requires_explicit_confirmation(client,confirm_value):
    pid=project(client);f,pages=document(client,pid)
    assert confirm(client,f,pages,confirm=confirm_value).status_code==422
    assert all(p['review_state']=='unreviewed' for p in db.rows('SELECT * FROM pages WHERE file_id=?',(f['id'],)))

def test_document_review_rejects_changed_page_set_atomically(client):
    pid=project(client);f,pages=document(client,pid)
    response=confirm(client,f,pages[:-1]);assert response.status_code==409
    assert not db.one("SELECT id FROM pages WHERE file_id=? AND review_state='reviewed'",(f['id'],))
    assert not db.one('SELECT id FROM sources WHERE file_id=? AND verified=1',(f['id'],))

@pytest.mark.parametrize('state',['queued','parsing','failed'])
def test_document_review_rejects_unfinished_parsing(client,state):
    pid=project(client);f,pages=document(client,pid);db.execute('UPDATE files SET status=? WHERE id=?',(state,f['id']))
    assert confirm(client,f,pages).status_code==409

def test_document_review_rejects_pending_reparse(client):
    pid=project(client);f,pages=document(client,pid);tasks.enqueue(pid,'parse',{'file_id':f['id'],'repair':True})
    assert confirm(client,f,pages).status_code==409
    assert not db.one("SELECT id FROM pages WHERE file_id=? AND review_state='reviewed'",(f['id'],))

def test_document_review_rejects_inconsistent_page_count(client):
    pid=project(client);f,pages=document(client,pid);db.execute('UPDATE files SET pages=4 WHERE id=?',(f['id'],))
    assert confirm(client,f,pages).status_code==409

def test_document_review_rejects_inactive_document(client):
    pid=project(client);f,pages=document(client,pid);db.execute('UPDATE files SET active=0 WHERE id=?',(f['id'],))
    assert confirm(client,f,pages).status_code==409
