"""Versioned advanced layout providers and explicit adoption into the knowledge head."""
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import HTTPException
from . import db, security, tasks
from . import research_records as store
from .literature_connectors import http_client,download,public_url

PROVIDERS={'surya-local','surya-modal','mineru'}

def settings(pid,body=None):
    security.require_project(pid);key='studio:parsing:'+pid;cfg=db.setting(key,{'provider':'surya-local','providers':{'surya-local':{'url':'http://127.0.0.1:8769'},'surya-modal':{'url':''},'mineru':{'url':'https://mineru.net','model':'vlm'}}})
    if body is not None:
        if body.get('provider') not in PROVIDERS:raise HTTPException(422,'解析服务类型无效')
        values={}
        for provider,data in body.get('providers',{}).items():
            if provider not in PROVIDERS or not isinstance(data,dict):raise HTTPException(422,'解析服务配置无效')
            url=str(data.get('url','')).strip().rstrip('/')
            if url and (urlsplit(url).scheme not in ('https','http') or not urlsplit(url).hostname or urlsplit(url).username):raise HTTPException(422,'解析服务地址无效')
            if provider!='surya-local' and url and urlsplit(url).scheme!='https':raise HTTPException(422,'云解析服务需要 HTTPS')
            if 'api_key' in data:store.write_secret('parse:'+pid+':'+provider,data['api_key'])
            values[provider]={'url':url,'model':str(data.get('model','vlm'))[:100]}
        cfg={'provider':body['provider'],'providers':{**cfg.get('providers',{}),**values}};db.set_setting(key,cfg)
    return {**cfg,'providers':{provider:{**data,'key_configured':bool(store.read_secret('parse:'+pid+':'+provider))} for provider,data in cfg.get('providers',{}).items()}}

def health(pid):
    cfg=settings(pid);provider=cfg['provider'];config=cfg['providers'].get(provider,{});url=config.get('url','').rstrip('/')
    if not url:raise ValueError('请先填写解析服务地址')
    key=store.read_secret('parse:'+pid+':'+provider)
    if provider=='mineru':return {'provider':provider,'configured':bool(key),'message':'MinerU 使用 API Key；提交真实文献时验证账号和配额，不发送收费测试。'}
    with http_client(url,timeout=15) as client:
        response=client.get(url+'/health',headers={'Authorization':'Bearer '+key} if key else {});response.raise_for_status();result=response.json()
    return {'provider':provider,**result}

def check_cancel(task):
    row=db.one('SELECT cancel_requested FROM tasks WHERE id=?',(task['id'],))
    if tasks.STOP.is_set() or row and row['cancel_requested']:raise InterruptedError('解析已停止；远端任务可能仍在处理，可用已保存的任务编号继续查询')

def normalize_blocks(payload):
    blocks=payload.get('blocks',[])
    if not blocks and isinstance(payload.get('pages'),list):
        blocks=[]
        for page in payload['pages']:
            for item in page.get('blocks',page.get('layout',[])):
                blocks.append({**item,'page':page.get('page',page.get('page_number',1)),'page_size':page.get('page_size')})
    if not isinstance(blocks,list) or len(blocks)>30000:raise ValueError('解析结果块数量无效')
    result=[]
    for index,raw in enumerate(blocks):
        if not isinstance(raw,dict):raise ValueError('解析结果块无效')
        text=raw.get('text') or raw.get('table_body') or raw.get('latex') or '\n'.join(item for item in raw.get('list_items',[]) if isinstance(item,str))
        if not isinstance(text,str) or len(text)>200000:raise ValueError('解析结果文本过长')
        if not text.strip():continue
        page=int(raw.get('page',raw.get('page_idx',0)+1));bbox=raw.get('bbox')
        if page<1 or page>10000:raise ValueError('解析页码无效')
        if bbox is not None and (not isinstance(bbox,list) or len(bbox)!=4 or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in bbox)):raise ValueError('解析区域坐标无效')
        label=str(raw.get('type',raw.get('label','paragraph'))).lower()
        kind='heading' if label in ('heading','title','sectionheader','section_header') or raw.get('text_level') else 'table' if 'table' in label else 'formula' if label in ('equation','formula','math') else 'paragraph'
        result.append({'page':page,'type':kind,'text':text,'bbox':bbox,'page_size':raw.get('page_size'),'order':int(raw.get('order',raw.get('position',index))),
                       'confidence':raw.get('confidence'),'original_label':label,'coordinate_system':raw.get('coordinate_system','provider_coordinates')})
    if not result:raise ValueError('解析服务未返回可用的结构化文本')
    return sorted(result,key=lambda item:(item['page'],item['order']))

def parse(task,body):
    pid=task['project_id'];cfg=settings(pid);provider=body.get('provider') or cfg['provider']
    if provider not in PROVIDERS:raise ValueError('解析 provider 无效')
    config=cfg['providers'].get(provider,{});base=config.get('url','').rstrip('/')
    if not base:raise ValueError('请先填写解析服务地址')
    local=provider=='surya-local' and urlsplit(base).hostname in ('127.0.0.1','localhost','::1')
    original=store.require_file(pid,body['file_id'],not local)
    key=store.read_secret('parse:'+pid+':'+provider);headers={'Authorization':'Bearer '+key} if key else {}
    data=security.safe_object(original['sha256']).read_bytes();job=None
    existing=store.list_records(pid,'parse_result',original['id'])
    fingerprint=store.digest([provider,base,config.get('model'),original['sha256']])
    previous=next((row for row in reversed(existing) if row['data'].get('task_id')==task['id'] and row['data'].get('job_id') and row['data'].get('provider')==provider and row['data'].get('provider_fingerprint') in (None,fingerprint)),None)
    if previous and previous['data'].get('status')=='completed':return previous
    if previous:job=previous['data']['job_id']
    tasks.event(task['id'],'提交结构化文档解析',10)
    with http_client(base,timeout=120) as client:
        if provider=='mineru':
            if not key:raise ValueError('MinerU API Key 未配置')
            public_url(base);api=base if base.endswith('/api/v4') else base+'/api/v4'
            if not job:
                response=client.post(api+'/file-urls/batch',headers=headers,json={'files':[{'name':original['name'],'data_id':original['id']}],'enable_formula':True,'model_version':config.get('model','vlm')});response.raise_for_status();value=response.json()
                if value.get('code') not in (None,0):raise ValueError('MinerU 拒绝任务：'+str(value.get('msg','')))
                job=value['data']['batch_id'];upload=value['data']['file_urls'][0];public_url(upload)
                response=client.put(upload,content=data);response.raise_for_status()
        else:
            if provider=='surya-modal':public_url(base)
            if not job:
                response=client.post(base+'/parse',headers=headers,files={'file':(original['name'],data,'application/pdf')});response.raise_for_status();value=response.json()
                if value.get('blocks') or value.get('pages'):return save_result(task,original,provider,value,None)
                job=value.get('job_id') or value.get('id')
                if not job:raise ValueError('Surya 服务未返回任务编号')
        record=previous or store.save(pid,'parse_result',{'provider':provider,'provider_fingerprint':fingerprint,'job_id':job,'task_id':task['id'],'file_hash':original['sha256'],'status':'processing'},owner=original['id'])
        deadline=time.monotonic()+max(60,min(3600,int(body.get('timeout_seconds',1800))))
        while time.monotonic()<deadline:
            check_cancel(task)
            url=api+'/extract-results/batch/'+job if provider=='mineru' else base+'/jobs/'+job
            response=client.get(url,headers=headers);response.raise_for_status();value=response.json()
            if provider=='mineru':
                if value.get('code') not in (None,0):raise ValueError('MinerU 任务查询失败：'+str(value.get('msg','')))
                items=value.get('data',{}).get('extract_result',[])
                item=next((item for item in items if item.get('data_id')==original['id']),items[0] if items else {});state=item.get('state')
                if state=='done':
                    raw,_,_=download(item['full_zip_url'],maximum=300*1024*1024)
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        if sum(info.file_size for info in archive.infolist())>600*1024*1024:raise ValueError('MinerU 结果解压体积过大')
                        name=next((name for name in archive.namelist() if name.endswith('_content_list.json') or name.endswith('content_list.json')),None)
                        if not name:raise ValueError('MinerU 结果缺少结构化 content_list.json')
                        payload={'blocks':json.loads(archive.read(name))}
                    return save_result(task,original,provider,payload,record)
                if state in ('failed','error'):raise ValueError(str(item.get('err_msg','MinerU 解析失败')))
            else:
                state=value.get('status') or value.get('state')
                if state in ('completed','done','succeeded'):
                    result=value.get('result')
                    if not result:
                        response=client.get(base+'/jobs/'+job+'/result',headers=headers);response.raise_for_status();result=response.json()
                    return save_result(task,original,provider,result,record)
                if state in ('failed','error'):raise ValueError(str(value.get('error','Surya 解析失败')))
            tasks.event(task['id'],'解析服务处理中',45,{'provider':provider,'job_id':job});tasks.STOP.wait(2)
    raise TimeoutError('解析等待超时，远端任务编号已保存，可从任务中心重试查询')

def save_result(task,original,provider,payload,record):
    current=store.require_file(task['project_id'],original['id'])
    if current['sha256']!=original['sha256']:raise ValueError('原件已变化')
    blocks=normalize_blocks(payload);raw=db.dumps({'blocks':blocks,'provider':provider,'file_hash':original['sha256']}).encode()
    value={'provider':provider,'file_hash':original['sha256'],'blob_sha256':security.put_blob(raw),'status':'completed','blocks':len(blocks),'pages':max(item['page'] for item in blocks),'task_id':task['id'],'human_reviewed':False}
    if record:value.update(job_id=record['data'].get('job_id'),provider_fingerprint=record['data'].get('provider_fingerprint'))
    return store.save(task['project_id'],'parse_result',value,record['id'] if record else None,original['id'],record['revision'] if record else None)

@db.atomic
def adopt(pid,identity,expected_revision):
    from . import knowledge_units as units
    from .parsers import tokens
    record=store.get(pid,identity,'parse_result')
    if record['revision']!=expected_revision or record['data'].get('status')!='completed':raise HTTPException(409,'解析结果尚未就绪或已更新')
    original=store.require_file(pid,record['owner_id'])
    if original['sha256']!=record['data']['file_hash']:raise HTTPException(409,'原文版本已变化')
    data=json.loads(security.safe_object(record['data']['blob_sha256']).read_bytes());blocks=normalize_blocks(data)
    engine='studio:'+record['data']['provider']+':'+record['data']['blob_sha256'][:16];stamp=units.input_stamp(original)
    old=db.one('SELECT id FROM knowledge_parses WHERE file_id=? AND input_hash=? AND engine=?',(original['id'],stamp,engine))
    if old:return units.activate_parse(pid,old['id'])
    parse_id=db.uid();db.execute('INSERT INTO knowledge_parses(id,project_id,file_id,file_hash,input_hash,engine,strategy,created_at) VALUES(?,?,?,?,?,?,?,?)',(parse_id,pid,original['id'],original['sha256'],stamp,engine,'layout',db.now()))
    headings=[]
    for index,block in enumerate(blocks,1):
        if block['type']=='heading':headings=[block['text'][:200]]
        uid=db.uid();locator={'page':block['page'],'bbox':block['bbox'],'physical_page':original['extension']=='.pdf','coordinate_system':block['coordinate_system'],'page_size':block['page_size'],'extraction_origin':record['data']['provider'],'layout_order':block['order'],'confidence':block.get('confidence')}
        db.execute('INSERT INTO knowledge_units(id,parse_id,ordinal,kind,text,content_hash,locator,heading_path,origin,issues) VALUES(?,?,?,?,?,?,?,?,?,?)',(uid,parse_id,index,block['type'],block['text'],units.digest(block['text']),db.dumps(locator),db.dumps(headings),record['data']['provider'],db.dumps(['layout_and_recognition_need_original_review'])))
        db.execute('INSERT INTO knowledge_unit_fts VALUES(?,?)',(uid,tokens(block['text'])));units.index_chunks(uid,block['text'])
    units.activate_parse(pid,parse_id)
    from .knowledge_lineage import changed
    changed(pid,'file',original['id'],'当前解析版本已切换，译文、随记和派生内容需复查')
    from .knowledge_service import index_units
    index_units(pid)
    return {'parse_id':parse_id,'units':len(blocks),'human_reviewed':False}

def convert_doc(data):
    from .word_conversion import convert_doc as convert
    return convert(data)
