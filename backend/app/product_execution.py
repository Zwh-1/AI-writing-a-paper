"""Materialize selected outputs at the existing isolated execution boundary."""
import hashlib
import re
from pathlib import Path
from fastapi import HTTPException
from . import db,security,resource_refs as refs

MAX_INPUT=80*1024*1024


def prepare(pid,body,remote=False):
    security.require_project(pid)
    file_ids=body.get('file_ids',[])
    sources=body.get('product_refs',[])
    if not isinstance(file_ids,list) or len(file_ids)>10 or any(not isinstance(fid,str) for fid in file_ids):raise HTTPException(422,'执行文件范围无效')
    if not isinstance(sources,list) or len(sources)>20:raise HTTPException(422,'执行产物范围须为0至20项')
    refs.validate(pid,sources,remote)
    inputs=[];origins=[];products=[];total=0;text_size=0;seen=set()
    def append(name,raw,source=None):
        nonlocal total
        total+=len(raw)
        if total>MAX_INPUT:raise HTTPException(422,'执行输入超过80MB')
        inputs.append({'name':name,'sha256':hashlib.sha256(raw).hexdigest(),'raw':raw,**({'source_ref':source} if source else {})})
    from .source_access import require_file
    for identity in dict.fromkeys(file_ids):
        row=require_file(pid,identity,remote);source=refs.make(pid,'file',identity)
        append(identity+row['extension'],security.safe_object(row['sha256']).read_bytes(),source)
        origins.append(source)
    for source in sources:
        key=(source['type'],source['id'],source['version'])
        if key in seen:continue
        seen.add(key);entry={'source_ref':source,'classification':'unverified_reused_content'}
        if source['type'] in ('file','asset'):
            table='files' if source['type']=='file' else 'artifacts'
            row=db.one('SELECT * FROM '+table+' WHERE id=? AND project_id=?',(source['id'],pid))
            title=row['name'];extension=Path(title).suffix.lower()
            metadata=db.loads(row['metadata'],{}) if source['type']=='asset' else {}
            if source['type']=='file':extension=row['extension']
            elif not extension:
                extension='.docx' if metadata.get('media_type')=='application/vnd.openxmlformats-officedocument.wordprocessingml.document' else '.txt' if metadata.get('studio_rich_text') else '.png' if row['kind'] in ('figure','image','screenshot') else '.bin'
            if not re.fullmatch(r'\.[a-z0-9]{1,10}',extension):extension='.bin'
            if row['sha256']:
                name=f'product_{len(products)+1:02d}'+extension
                append(name,security.safe_object(row['sha256']).read_bytes(),source)
                entry['filename']=name
            if source['type']=='asset':
                if metadata.get('document'):entry['document']=metadata['document']
                if metadata.get('spec'):entry['spec']=metadata['spec']
            if not any(key in entry for key in ('filename','document','spec')):raise HTTPException(422,'所选资产没有可执行读取的实际内容')
            entry.update(title=title,kind=source['type'])
        else:
            from .product_service import content,MAX_TEXT
            value=content(pid,source)
            text_size+=len(value['text'])
            if text_size>MAX_TEXT:raise HTTPException(422,'执行产物文本合计超过40000字符，请减少选择')
            entry.update({key:value[key] for key in ('title','text','kind','document')})
        products.append(entry);origins.append(source)
    if body.get('message_id'):
        origins.append(refs.make(pid,'message',body['message_id']))
        refs.validate(pid,[origins[-1]],remote)
    if products:
        append('products.json',db.dumps({'schema_version':'paper-products.execution.v1','products':products}).encode('utf-8'))
    origins=list({(ref['type'],ref['id'],ref['version']):ref for ref in origins}.values())
    refs.validate(pid,origins,remote)
    return inputs,origins
