import base64,contextlib,copy,datetime,hashlib,http.server,io,json,ssl,threading
from pathlib import Path
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from docx import Document
from test_workbench import client,project,section,save,file,manual_package
from app import db,security,config,exports,sync_bundle,synchronization as sync,documents,provider,tasks,collaboration,controlled_execution as controlled,network

def packet_fixture(c):
 p=project(c);manual_package(c,p);f=file(c,p,'本项目独有的研究原件');sid=section(c,p);save(c,sid,'第一段\n第二段');db.set_setting('sharing:'+p,True)
 data=io.BytesIO();doc=Document();doc.add_paragraph('复杂封面');doc.add_paragraph('{{PAPER_BODY}}');doc.save(data);sha=security.put_blob(data.getvalue());db.set_setting('template:'+p,sha)
 fact=db.uid();db.execute('INSERT INTO facts(id,project_id,label,statement,source_ids,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(fact,p,'研究事实','尚待核对的实际内容','[]',db.now(),db.now()))
 term=db.uid();db.execute('INSERT INTO terms VALUES(?,?,?,?)',(term,p,'术语','term'))
 return p,f,sid,fact,term

@contextlib.contextmanager
def replica(monkeypatch,path):
 path.mkdir()
 for name in ('objects','previews','exports','backups','tmp','logs'):(path/name).mkdir()
 with monkeypatch.context() as m:
  m.setattr(db,'DB_PATH',path/'workbench.sqlite3')
  for module in (security,config,exports):m.setattr(module,'DATA',path)
  db.init();yield

def test_complete_bundle_restores_resources_and_merges_metadata_without_overwriting_conflicts(client,monkeypatch,tmp_path):
 p,f,s,fact,term=packet_fixture(client);first=sync_bundle.make(p);db.set_setting('sync_records:'+p,first['record_heads']);db.set_setting('sync_base:'+p,first['heads']);db.execute('UPDATE facts SET statement=?,revision=2 WHERE id=?',('来自设备A的新内容',fact));second=sync_bundle.make(p,first['heads']);db.execute('DELETE FROM terms WHERE id=?',(term,));third=sync_bundle.make(p,first['heads'])
 with replica(monkeypatch,tmp_path/'replica'):
  receipt=sync_bundle.apply(first,allow_new=True);assert receipt['new_project'];assert security.safe_object(f['sha256']).read_bytes()=='本项目独有的研究原件'.encode();assert security.safe_object(db.setting('template:'+p)).read_bytes().startswith(b'PK')
  db.execute('UPDATE facts SET statement=?,revision=2 WHERE id=?',('来自设备B的并发内容',fact));result=sync_bundle.apply(second);assert len(result['record_conflicts'])==1
  assert db.one('SELECT statement FROM facts WHERE id=?',(fact,))['statement']=='来自设备B的并发内容'
  conflict=db.one('SELECT * FROM sync_record_conflicts');old=db.one('SELECT * FROM facts WHERE id=?',(fact,));sync_bundle.resolve_record(p,conflict['id'],{'choice':'take_remote','expected_local_hash':sync_bundle.record_hash('facts',old)})
  assert db.one('SELECT revision FROM facts WHERE id=?',(fact,))['revision']==3
  sync_bundle.apply(third);assert db.one('SELECT id FROM terms WHERE id=?',(term,)) is None

def test_bundle_cannot_replace_an_existing_file_or_use_paths_as_ids(client):
 p,f,s,fact,term=packet_fixture(client);packet=sync_bundle.make(p);bad=copy.deepcopy(packet);bad['tables']['files'][0]['id']='../private'
 with pytest.raises(ValueError):sync_bundle.validate(bad)
 bad=copy.deepcopy(packet);bad['tables']['files'][0]['sha256']='0'*64
 with pytest.raises(ValueError):sync_bundle.validate(bad)

@pytest.fixture
def webdav(tmp_path,monkeypatch):
 key=rsa.generate_private_key(public_exponent=65537,key_size=2048);name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]);now=datetime.datetime.now(datetime.timezone.utc)
 cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(__import__('ipaddress').ip_address('127.0.0.1'))]),False).sign(key,hashes.SHA256())
 certfile=tmp_path/'local-ca.pem';keyfile=tmp_path/'local-server-key.pem';certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM));keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));state={'body':None,'etag':None,'auth':[]}
 class Handler(http.server.BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def auth(self):state['auth'].append(self.headers.get('Authorization'));return self.headers.get('Authorization')=='Basic '+base64.b64encode(b'fixture-user:fixture-password').decode()
  def reply(self,status,body=b''):
   self.send_response(status)
   if state['etag']:self.send_header('ETag',state['etag'])
   self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  def do_PUT(self):
   if not self.auth():return self.reply(401)
   raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
   if self.headers.get('If-None-Match')=='*' and state['body'] is not None:return self.reply(412)
   if self.headers.get('If-Match') and self.headers['If-Match']!=state['etag']:return self.reply(412)
   state['body']=raw;state['etag']='"'+hashlib.sha256(raw).hexdigest()+'"';return self.reply(201)
  def do_GET(self):
   if not self.auth():return self.reply(401)
   return self.reply(200,state['body']) if state['body'] else self.reply(404)
 server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(certfile,keyfile);server.socket=ctx.wrap_socket(server.socket,server_side=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();monkeypatch.setenv('PAPER_CLOUD_CA_CERT',str(certfile));monkeypatch.setattr(network,'proxy_for',lambda url:None)
 try:yield 'https://127.0.0.1:'+str(server.server_port)+'/project.papersync',state
 finally:server.shutdown();server.server_close();thread.join(2)

def test_actual_https_webdav_etag_conflict_encryption_and_pull_preview(client,webdav):
 p,f,s,_,_=packet_fixture(client);url,state=webdav;body={'url':url,'username':'fixture-user','cloud_password':'fixture-password','sync_password':'fixture-independent-sync-password'};result=sync.cloud(p,body);assert result['etag']==state['etag'];assert '本项目独有'.encode() not in state['body'];saved=state['body']
 with pytest.raises(Exception) as error:sync.cloud(p,body)
 assert getattr(error.value,'status_code',None)==409 and state['body']==saved
 result=sync.cloud(p,{**body,'etag':state['etag']});pull=sync.cloud(p,body,pull=True);assert pull['automatically_applied'] is False;assert pull['resource_count']>=2;packet=sync.decrypt(security.safe_object(pull['encrypted_blob']).read_bytes(),body['sync_password']);assert packet['project_id']==p
 assert all(a=='Basic '+base64.b64encode(b'fixture-user:fixture-password').decode() for a in state['auth'])
 assert 'fixture-password' not in db.dumps(db.rows('SELECT * FROM settings'))

def test_http_read_test_and_capture_use_real_local_system(client,monkeypatch):
 p=project(client)
 class Handler(http.server.BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   body='<html><meta charset="utf-8"><title>隔离测试站点</title><style>body{font-family:Arial;background:#edf7ef;padding:70px}</style><h1>Controlled research system fixture</h1><p>真实浏览器截图测试</p></html>'.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_port);db.set_setting('capture_origins:'+p,[url])
 try:
  result=controlled.http_test(p,{'url':url,'expected_status':200,'contains':'fixture'});assert result['passed'];shot=controlled.capture(p,{'url':url,'is_test':True});assert shot['metadata']['is_test'] and shot['metadata']['egress']=='local_only';assert security.safe_object(shot['sha256']).read_bytes().startswith(b'\x89PNG')
  with pytest.raises(ValueError):controlled.allowed_target(p,'https://outside.example')
 finally:server.shutdown();server.server_close();thread.join(2)

def test_sandbox_never_falls_back_to_host_when_docker_unavailable(client,monkeypatch):
 p=project(client);monkeypatch.setattr(controlled,'capabilities',lambda:{'docker_cli':True,'docker_engine':False})
 with pytest.raises(ValueError):controlled.execute(p,{'code':'raise Exception("must not run on host")'})

def test_offline_keys_are_per_account_and_not_model_keys(client):
 a=collaboration.create_user('offline-a','fixture-password-long-enough');b=collaboration.create_user('offline-b','fixture-password-long-enough');ka=collaboration.offline_key(a['id']);kb=collaboration.offline_key(b['id']);assert ka!=kb and ka==collaboration.offline_key(a['id']);assert len(base64.b64decode(ka))==32

def test_model_request_uses_one_credential_and_provider_snapshot(client,monkeypatch):
 client.put('/api/v1/settings',json={'api_key':'fake-old-key-1234','provider':{'base_url':'https://before.example','model':'old-model'}})
 original_config=provider.config();seen=[]
 def handle(request):
  db.set_setting('provider',{**original_config,'base_url':'https://after.example','model':'new-model'});security.save_key('fake-new-key-5678');seen.append(provider.headers('openai')['Authorization']);return __import__('httpx').Response(200,json={'data':[{'id':'old-model'}]})
 monkeypatch.setattr(provider,'client',lambda cfg:__import__('httpx').Client(transport=__import__('httpx').MockTransport(handle)));provider.models();assert seen==['Bearer fake-old-key-1234'];assert provider.config()['base_url']=='https://after.example';assert client.get('/api/v1/settings').json()['capabilities']=={}
