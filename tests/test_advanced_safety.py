import copy,io,json,threading,http.server,time,zipfile
import httpx,pytest
from fastapi.testclient import TestClient
from test_workbench import client,project,section,file,manual_package
from app import db,security,provider,tasks,parsers,collaboration,controlled_execution as controlled,documents,sync_bundle

def test_derived_material_cannot_bypass_parent_egress(client):
 p=project(client);parent=file(client,p,'原件仅限本地');child=file(client,p,'派生材料包括保密观点');db.execute("UPDATE files SET egress='local_only' WHERE id=?",(parent['id'],));security.link_derivation(child['id'],parent['id'],'test')
 assert not security.file_allows_remote(child['id'],p)
 rows=parsers.keyword_search(p,'派生材料 保密观点','all',remote=True);assert not any(s['file_id']==child['id'] for s in rows)
 security.link_derivation(parent['id'],child['id'],'would-cycle');assert db.one('SELECT COUNT(*) n FROM file_derivations')['n']==1


def test_capture_credentials_are_dpapi_protected_and_outside_backups(client):
 p=project(client);db.set_setting('capture_origins:'+p,['http://127.0.0.1:3000'])
 state={'cookies':[{'name':'fixture-session','value':'fixture-secret-cookie','domain':'127.0.0.1','path':'/'}],'origins':[]};r=controlled.store_capture_profile(p,state);assert not r['included_in_backup_or_cloud'];path=security.DATA/'private'/('capture-'+p+'.dpapi');assert b'fixture-secret-cookie' not in path.read_bytes();assert controlled.load_capture_profile(p)==state
 with pytest.raises(ValueError):controlled.store_capture_profile(p,{'cookies':[{'name':'paper_session','value':'reserved','domain':'127.0.0.1','path':'/'}]})
 with pytest.raises(ValueError):controlled.allowed_target(p,'http://127.0.0.1:3000/?access_token=secret')
 with pytest.raises(ValueError):controlled.store_capture_profile(p,{'cookies':[{'name':'a','value':'b','domain':'outside.example','path':'/'}]})


def test_remote_ai_requires_per_account_budget_and_rechecks_before_send(client,monkeypatch):
 p=project(client);manual_package(client,p);s=section(client,p);db.set_setting('sharing:'+p,True);writer=collaboration.create_user('quota-writer','fake-password-long-enough');db.execute('INSERT INTO project_members VALUES(?,?,?)',(p,writer['id'],'editor'))
 from test_advanced import login
 with TestClient(collaboration.app,base_url='https://testserver') as remote:
  login(remote,'quota-writer','fake-password-long-enough');body={'base_revision_id':None,'acknowledge_charges':True}
  assert remote.post(f'/collab/v1/projects/{p}/sections/{s}/writing',json=body).status_code==403
  db.set_setting('remote_ai_global_budget',4);db.set_setting('remote_ai_budget:'+writer['id'],2);cfg=provider.config();db.set_setting('provider',{**cfg,'model':'fake-model'});calls=[]
  def generate(*args,**kwargs):calls.append(1);return {'text':db.dumps({'paragraphs':[{'text':'仅用于自动化测试，实际结果【待补证据】','source_ids':[],'reference_ids':[]}],'missing_materials':['实际结果']}),'usage':{},'cost':None,'remote_id':'fixture','request_hash':'fixture'}
  monkeypatch.setattr(provider,'generate',generate);r=remote.post(f'/collab/v1/projects/{p}/sections/{s}/writing',json=body);assert r.status_code==200,r.text;tasks.run_one();assert len(calls)==1
  r=remote.post(f'/collab/v1/projects/{p}/sections/{s}/writing',json=body);assert r.status_code==200;tasks.run_one();assert len(calls)==1;assert '预算' in db.one('SELECT error FROM tasks WHERE id=?',(r.json()['task_id'],))['error']


def test_chunked_login_body_is_bounded(client):
 with TestClient(collaboration.app,base_url='https://testserver') as remote:
  r=remote.post('/collab/v1/login',content=iter([b'{'*10000,b'a'*10000]),headers={'Content-Type':'application/json'})
  assert r.status_code==413,r.text


def test_actual_http_sse_transport_returns_only_received_deltas(client,monkeypatch):
 from app import provider_stream,network
 class Handler(http.server.BaseHTTPRequestHandler):
  protocol_version='HTTP/1.1'
  def log_message(self,*args):pass
  def do_POST(self):
   self.rfile.read(int(self.headers['Content-Length']));self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
   for item in [{'choices':[{'delta':{'content':'收到甲'}}]},{'choices':[{'delta':{'content':'收到乙'},'finish_reason':'stop'}]}]:
    self.wfile.write(('data: '+json.dumps(item,ensure_ascii=False)+'\n\n').encode());self.wfile.flush();time.sleep(.03)
 server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();cfg={**provider.config(),'base_url':'http://127.0.0.1:'+str(server.server_port),'protocol':'openai','model':'fixture-actual-http'};db.set_setting('provider',cfg);monkeypatch.setattr(provider,'get_key',lambda:'fake-local-only-key');monkeypatch.setattr(network,'proxy_for',lambda url:None)
 try:
  pieces=[];r=provider_stream.generate('fixture','fixture',on_delta=pieces.append);assert pieces==['收到甲','收到乙'];assert r['streamed'] and r['text']=='收到甲收到乙'
 finally:server.shutdown();server.server_close();thread.join(2)


def test_confirming_a_table_does_not_confirm_original_pages(client):
 p=project(client);f=file(client,p,'表格原始材料');page=db.one('SELECT * FROM pages');run=db.uid();candidate={'from_page':1,'to_page':2,'merged_rows':[[{'text':'编号'},{'text':'值'}],[{'text':'1'},{'text':'=NO_EXECUTION()'}]]};db.execute('INSERT INTO advanced_runs VALUES(?,?,?,?,?,?)',(run,p,'advanced_tables',db.dumps({'file_id':f['id']}),db.dumps({'candidates':[candidate]}),db.now()))
 route=f'/api/v1/projects/{p}/continued-tables/{run}/0/confirm';payload={'rows':[['编号','值'],['1','=NO_EXECUTION()']],'note':'自动化测试核对声明'}
 assert client.post(route,json=payload).status_code==422
 response=client.post(route,json={**payload,'acknowledge':True});assert response.status_code==200,response.text;assert db.one('SELECT review_state FROM pages WHERE id=?',(page['id'],))['review_state']=='unreviewed';a=db.one('SELECT * FROM artifacts WHERE id=?',(response.json()['artifact_id'],));assert "'=NO_EXECUTION()" in security.safe_object(a['sha256']).read_text(encoding='utf-8-sig')
