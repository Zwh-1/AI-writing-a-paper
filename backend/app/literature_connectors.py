"""External literature connectors feed the existing original/reference stores."""
import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
import time
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qs, unquote
import httpx
from fastapi import HTTPException
from . import db, security, parsers, tasks
from . import research_records as store
from .network import proxy_for
from .config import MAX_FILE

def public_url(url):
    parts=urlsplit(url)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:raise ValueError('请输入完整的 HTTP/HTTPS 文件链接')
    if os.environ.get('PAPER_ALLOW_LOCAL_IMPORT')=='1':return url
    addresses=socket.getaddrinfo(parts.hostname,parts.port or (443 if parts.scheme=='https' else 80),type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):raise ValueError('文献链接不能指向本机或内网地址')
    return url

def http_client(url,timeout=60):
    return httpx.Client(timeout=timeout,follow_redirects=False,proxy=proxy_for(url),trust_env=False,headers={'User-Agent':'Yantai-Research-Workbench/0.7'})

def connector_get(client,url,task=None,**kwargs):
    for attempt in range(4):
        response=client.get(url,**kwargs)
        if getattr(response,'status_code',200)!=429 or attempt==3:return response
        try:delay=min(30,max(1,float(response.headers.get('Retry-After','2'))))
        except ValueError:delay=2
        if task:tasks.event(task['id'],f'服务限流，等待 {delay:g} 秒后继续',20)
        if tasks.STOP.wait(delay):raise InterruptedError('任务进程停止，限流请求未继续')
    return response

def download(url,headers=None,maximum=MAX_FILE):
    current=url
    for _ in range(6):
        public_url(current)
        with http_client(current) as client:
            with client.stream('GET',current,headers=headers or {}) as response:
                if response.is_redirect:
                    target=urljoin(current,response.headers.get('location',''))
                    if urlsplit(target).netloc!=urlsplit(current).netloc:headers={}
                    current=target;continue
                response.raise_for_status()
                if int(response.headers.get('content-length','0') or 0)>maximum:raise ValueError('文件超过导入大小上限')
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>maximum:raise ValueError('文件超过导入大小上限')
                    chunks.append(chunk)
                name=unquote(urlsplit(current).path.rsplit('/',1)[-1]) or 'paper.pdf'
                disposition=response.headers.get('content-disposition','')
                match=re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)",disposition,re.I)
                if match:name=unquote(match[1]).strip()
                if not name.lower().endswith(tuple(parsers.SUPPORTED)) and 'application/pdf' in response.headers.get('content-type',''):name+=' .pdf'.replace(' ','')
                name=name.replace('\\','/').rsplit('/',1)[-1][:240]
                raw=b''.join(chunks)
                if name.lower().endswith('.pdf') and not raw.lstrip().startswith(b'%PDF-'):raise ValueError('链接没有返回有效 PDF，可能是登录页或论文详情页')
                return raw,name,current
    raise ValueError('文件链接跳转次数过多')

def upsert_reference(pid,metadata,fid=None,provider=None,library='',external='',remote_version=''):
    from .reference_service import upsert_external
    return upsert_external(pid,metadata,fid,provider,library,external,remote_version)

def import_url(task,body):
    pid=task['project_id'];tasks.event(task['id'],'下载文献原件',10)
    raw,name,final=download(body['url'])
    original,new=parsers.register_file(pid,body.get('filename') or name,raw,'research',body.get('egress','selected_content'))
    tasks.event(task['id'],'解析已下载原件',55)
    if new or original['status']!='ready':parsers.parse_file(original['id'])
    reference=upsert_reference(pid,{**body.get('metadata',{}),'title':body.get('metadata',{}).get('title') or name,'url':body['url'],'download_url':final,'downloaded_at':db.now()},original['id'])
    return {'file_id':original['id'],'reference':reference,'created':new}

def update_tags(pid,rids,tags,mode='replace'):
    from .reference_service import set_tags
    return set_tags(pid,rids,tags,mode)

def zotero_settings(pid,body=None):
    security.require_project(pid);key='studio:zotero:'+pid
    if body is not None:
        library=str(body.get('library_id','')).strip();kind=body.get('library_type','users')
        if library and not library.isdigit() or kind not in ('users','groups'):raise HTTPException(422,'Zotero 文献库编号或类型无效')
        if 'api_key' in body:store.write_secret('zotero:'+pid,body['api_key'])
        db.set_setting(key,{'library_id':library,'library_type':kind,'attachments':body.get('attachments',True) is True})
    cfg=db.setting(key,{})
    return {**cfg,'key_configured':bool(store.read_secret('zotero:'+pid)),'oauth_configured':bool(os.environ.get('PAPER_ZOTERO_CLIENT_KEY') and os.environ.get('PAPER_ZOTERO_CLIENT_SECRET'))}

def zotero_sync(task,body):
    pid=task['project_id'];cfg=zotero_settings(pid);key=store.read_secret('zotero:'+pid)
    if not cfg.get('library_id') or not key:raise ValueError('请先配置 Zotero 文献库与访问凭证')
    library=cfg['library_type']+'/'+cfg['library_id'];base='https://api.zotero.org/'+library
    headers={'Zotero-API-Key':key,'Zotero-API-Version':'3'};start=0;seen=0;changed=0;failures=[]
    with http_client(base) as client:
        while True:
            tasks.event(task['id'],f'读取 Zotero 条目 {start+1} 起',min(85,10+start//10))
            state=db.one('SELECT cancel_requested FROM tasks WHERE id=?',(task['id'],))
            if state and state['cancel_requested']:raise InterruptedError('Zotero 同步已停止，已完成条目保留')
            response=connector_get(client,base+'/items/top',task,params={'format':'json','include':'data,bib','limit':100,'start':start,'sort':'dateModified','direction':'asc'},headers=headers)
            response.raise_for_status();rows=response.json()
            if not isinstance(rows,list):raise ValueError('Zotero 返回了无效条目列表')
            for item in rows:
                data=item.get('data',{});external=item['key'];version=str(item.get('version',data.get('version','')))
                prior=db.one('SELECT * FROM studio_external_links WHERE project_id=? AND provider=? AND library_id=? AND external_id=?',(pid,'zotero',library,external))
                seen+=1;prior_info=db.loads(prior['data'],{}) if prior else {}
                prior_reference=store.require_reference(pid,prior['reference_id']) if prior else None
                unchanged=bool(prior and prior['remote_version']==version and not body.get('force'))
                if unchanged and not cfg.get('attachments',True):continue
                metadata={'title':data.get('title','未命名文献'),'authors':[creator.get('name') or ' '.join(filter(None,[creator.get('firstName'),creator.get('lastName')])) for creator in data.get('creators',[])],
                          'abstract':data.get('abstractNote',''),'year':data.get('date',''),'journal':data.get('publicationTitle',''),'doi':data.get('DOI',''),'url':data.get('url',''),'tags':[tag['tag'] for tag in data.get('tags',[]) if tag.get('tag')],
                          'bib':item.get('bib',''),'zotero_key':external,'zotero_library':library,'metadata_source':'zotero'}
                fid=prior_reference['file_id'] if prior_reference else None;attachment_info={'attachment_pending':False,'attachment_state':'disabled'}
                if cfg.get('attachments',True):
                    try:
                        children=connector_get(client,base+'/items/'+external+'/children',task,headers=headers);children.raise_for_status()
                        attachment=next((child for child in children.json() if child.get('data',{}).get('contentType')=='application/pdf'),None)
                        if attachment:
                            fingerprint=attachment['key']+':'+str(attachment.get('version',attachment.get('data',{}).get('version','')))
                            attachment_info={'attachment_pending':False,'attachment_state':'ready','attachment_fingerprint':fingerprint}
                            if body.get('force') or not fid or prior_info.get('attachment_pending') or prior_info.get('attachment_fingerprint')!=fingerprint:
                                raw,name,_=download(base+'/items/'+attachment['key']+'/file',headers)
                                original,new=parsers.register_file(pid,attachment['data'].get('filename') or name,raw,'research','selected_content');fid=original['id']
                                if new or original['status']!='ready':parsers.parse_file(fid)
                        else:attachment_info={'attachment_pending':False,'attachment_state':'none'}
                    except (httpx.HTTPError,ValueError) as error:
                        detail={'key':external,'attachment_error':security.redact(error)};failures.append(detail);attachment_info={**prior_info,'attachment_pending':True,'attachment_state':'failed','last_error':detail['attachment_error']}
                upsert_reference(pid,metadata,fid,'zotero',library,external,version)
                db.execute('UPDATE studio_external_links SET data=? WHERE project_id=? AND provider=? AND library_id=? AND external_id=?',(db.dumps({'source':'zotero',**attachment_info}),pid,'zotero',library,external))
                if not unchanged or fid!=(prior_reference['file_id'] if prior_reference else None):changed+=1
            start+=len(rows)
            total=response.headers.get('Total-Results')
            if not rows or total and start>=int(total) or not total and len(rows)<100:break
            if start>100000:raise ValueError('单次同步超过十万条，请按库分批整理')
    db.set_setting('studio:zotero:last:'+pid,{'synced_at':db.now(),'seen':seen,'updated':changed,'attachment_failures':failures})
    return {'seen':seen,'updated':changed,'attachment_failures':failures,'complete':not failures}

def oauth_header(method,url,parameters,token_secret=''):
    consumer=os.environ.get('PAPER_ZOTERO_CLIENT_KEY','');secret=os.environ.get('PAPER_ZOTERO_CLIENT_SECRET','')
    if not consumer or not secret:raise ValueError('服务端尚未配置 Zotero OAuth 应用；可先使用 API Key 连接')
    values={'oauth_consumer_key':consumer,'oauth_nonce':secrets.token_hex(16),'oauth_timestamp':str(int(time.time())),'oauth_signature_method':'HMAC-SHA1','oauth_version':'1.0',**parameters}
    enc=lambda value:quote(str(value),safe='~')
    normalized='&'.join(enc(k)+'='+enc(v) for k,v in sorted(values.items()))
    base='&'.join([method.upper(),enc(url),enc(normalized)])
    values['oauth_signature']=base64.b64encode(hmac.new((enc(secret)+'&'+enc(token_secret)).encode(),base.encode(),hashlib.sha1).digest()).decode()
    return 'OAuth '+', '.join(enc(k)+'="'+enc(v)+'"' for k,v in values.items())

def begin_oauth(pid,callback):
    security.require_project(pid);url='https://www.zotero.org/oauth/request'
    with http_client(url) as client:
        response=client.post(url,headers={'Authorization':oauth_header('POST',url,{'oauth_callback':callback})});response.raise_for_status()
    values=parse_qs(response.text);token=values.get('oauth_token',[''])[0];secret=values.get('oauth_token_secret',[''])[0]
    if not token or not secret:raise ValueError('Zotero 没有返回有效临时令牌')
    store.write_secret('zotero-oauth:'+token,secret)
    db.execute('INSERT INTO studio_oauth VALUES(?,?,?,?,0)',(token,pid,callback,time.time()+600))
    return {'authorize_url':'https://www.zotero.org/oauth/authorize?'+urlencode({'oauth_token':token}),'expires_in':600}

def finish_oauth(token,verifier):
    with db.transaction():
        state=db.one('SELECT * FROM studio_oauth WHERE token=?',(token,))
        if not state or state['consumed'] or state['expires_at']<time.time():raise ValueError('Zotero 授权已过期或已经处理')
        db.execute('UPDATE studio_oauth SET consumed=1 WHERE token=?',(token,))
    secret=store.read_secret('zotero-oauth:'+token);url='https://www.zotero.org/oauth/access'
    try:
        with http_client(url) as client:
            response=client.post(url,headers={'Authorization':oauth_header('POST',url,{'oauth_token':token,'oauth_verifier':verifier},secret)});response.raise_for_status()
        data=parse_qs(response.text);key=data.get('oauth_token',[''])[0];user=data.get('userID',[''])[0]
        if not key or not user:raise ValueError('Zotero 授权结果缺少访问凭证')
        zotero_settings(state['project_id'],{'library_id':user,'library_type':'users','api_key':key,'attachments':True})
        return {'project_id':state['project_id'],'connected':True}
    finally:store.write_secret('zotero-oauth:'+token,'')

def openalex(pid,body):
    security.require_project(pid);query=str(body.get('query','')).strip()
    if not query or len(query)>2000:raise HTTPException(422,'请输入研究问题或检索关键词')
    params={'search':query,'per-page':min(50,max(1,int(body.get('limit',20)))),'page':max(1,int(body.get('page',1)))}
    filters=[]
    for key,field in [('from_year','from_publication_date'),('to_year','to_publication_date')]:
        if body.get(key):
            year=int(body[key])
            if not 1500<=year<=2200:raise HTTPException(422,'年份范围无效')
            filters.append(field+':'+str(year)+('-01-01' if key=='from_year' else '-12-31'))
    if body.get('open_access'):filters.append('is_oa:true')
    if filters:params['filter']=','.join(filters)
    key=store.read_secret('openalex');headers={}
    if key:params['api_key']=key
    with http_client('https://api.openalex.org') as client:
        response=connector_get(client,'https://api.openalex.org/works',params=params,headers=headers);response.raise_for_status();data=response.json()
    items=[]
    for work in data.get('results',[]):
        inverted=work.get('abstract_inverted_index') or {};words={position:word for word,positions in inverted.items() for position in positions}
        location=work.get('best_oa_location') or work.get('primary_location') or {}
        items.append({'id':work['id'],'title':work.get('display_name',''),'authors':[a['author']['display_name'] for a in work.get('authorships',[])],
                      'year':str(work.get('publication_year','')),'doi':work.get('doi',''),'abstract':' '.join(words[index] for index in sorted(words)),
                      'url':work.get('doi') or work['id'],'pdf_url':location.get('pdf_url'),'journal':(location.get('source') or {}).get('display_name',''),
                      'cited_by_count':work.get('cited_by_count',0),'tags':[term['display_name'] for term in work.get('keywords',[])],'metadata_source':'openalex'})
    result={'query':query,'page':params['page'],'total':data.get('meta',{}).get('count',0),'items':items}
    store.save(pid,'search',result)
    return result
