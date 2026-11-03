"""Read real asset/document content and apply reviewed assistant text operations."""
import base64
import copy
import io
from fastapi import HTTPException
from . import db,security,documents
from . import research_records as store

def artifact(pid,identity,include_content=True,seen=None):
    seen=set() if seen is None else set(seen)
    if identity in seen:raise HTTPException(409,'资产来源出现循环')
    seen.add(identity)
    row=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(identity,pid))
    if not row:raise HTTPException(404,'资产不属于当前项目')
    metadata=db.loads(row['metadata'],{})
    if metadata.get('egress','local_only')=='local_only':raise HTTPException(403,'选中资产不允许外发')
    file_ids=set();stamps=[]
    from .resource_refs import validate
    validate(pid,metadata.get('source_refs',[]),remote=True)
    for fid in metadata.get('source_file_ids',[]):
        source=store.require_file(pid,fid,True);file_ids.add(source['id']);stamps.append([source['id'],source['sha256'],source['egress'],source['active']])
    if metadata.get('source_file_id'):
        source=store.require_file(pid,metadata['source_file_id'],True);file_ids.add(source['id']);stamps.append([source['sha256'],source['egress'],source['active']])
    for sid in metadata.get('source_ids',[]):
        source=db.one('SELECT file_id FROM sources WHERE id=?',(sid,))
        if not source:raise HTTPException(403,'资产原始来源已不存在')
        original=store.require_file(pid,source['file_id'],True);file_ids.add(original['id']);stamps.append([sid,original['sha256'],original['egress']])
    from .writing_agent.provenance import revision_permissions
    for rid in metadata.get('source_revision_ids',[]):stamps.append(revision_permissions(pid,rid))
    from .writing_agent.evidence import get as evidence
    for eid in metadata.get('evidence_ids',[]):
        value=evidence(pid,eid,True);stamps.append([eid,value.get('origin_version')])
    if metadata.get('parent_artifact_id'):
        _,_,parent_files,parent_stamp=artifact(pid,metadata['parent_artifact_id'],False,seen);file_ids.update(parent_files);stamps.append(parent_stamp)
    value={'id':identity,'name':row['name'],'kind':row['kind'],'metadata':metadata};images=[]
    if include_content and row['sha256']:
        raw=security.safe_object(row['sha256']).read_bytes()
        if len(raw)>24*1024*1024:raise HTTPException(422,'选中资产内容过大，请选择较小范围')
        try:
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                image.thumbnail((1800,1800));output=io.BytesIO();image.convert('RGB').save(output,format='PNG')
                images=[{'mime':'image/png','data':base64.b64encode(output.getvalue()).decode()}];value['content_type']='image'
        except (OSError,ValueError):
            text=None
            from pathlib import Path
            extension=Path(row['name']).suffix.lower()
            if raw.lstrip().startswith(b'%PDF-') or raw.startswith(b'PK') and (extension=='.docx' or metadata.get('media_type')=='application/vnd.openxmlformats-officedocument.wordprocessingml.document'):
                from .parsers import parse_pages
                text='\n\n'.join(page['text'] for page in parse_pages(raw,'.pdf' if raw.lstrip().startswith(b'%PDF-') else '.docx'))
            else:
                for encoding in ('utf-8-sig','utf-16','gb18030'):
                    try:text=raw.decode(encoding);break
                    except UnicodeError:pass
            if text is None:raise HTTPException(422,'此资产暂不能直接送入助手，请先转换为图片或可读取文本')
            if len(text)>40000:raise HTTPException(422,'文本资产超过40000字符，请先整理为较小资产')
            value.update(content_type='text',text=text)
    return value,images,sorted(file_ids),store.digest([row['sha256'],metadata,stamps])

def section(pid,identity,revision_id=None):
    row=security.require_section(identity)
    if row['project_id']!=pid:raise HTTPException(404,'正文不属于当前项目')
    current=documents.current(row)
    if not current:raise HTTPException(409,'请先保存当前正文')
    if revision_id is not None and current['id']!=revision_id:raise HTTPException(409,'正文已经更新，请重新读取')
    from .writing_agent.provenance import revision_permissions
    permission=revision_permissions(pid,current['id']);document=db.loads(current['document'],{})
    if len(db.dumps(document))>150000:raise HTTPException(422,'当前章节超过助手正文预算，请缩小章节')
    paragraphs=[{'node_id':node['attrs']['nodeId'],'type':node['type'],'text':documents.plain(node),
                 'text_spans':[child.get('text','') for child in node.get('content',[]) if child['type']=='text'],
                 'locked':bool(db.one('SELECT node_id FROM locks WHERE section_id=? AND node_id=?',(identity,node['attrs']['nodeId'])))}
                for node in documents.nodes(document) if node['type'] in ('paragraph','heading')]
    return row,current,document,paragraphs,permission

def proposed_document(document,operations):
    if not isinstance(operations,list) or not 1<=len(operations)<=80:raise HTTPException(422,'助手改写操作数量无效')
    candidate=copy.deepcopy(document)
    for operation in operations:
        if not isinstance(operation,dict) or set(operation)-{'node_id','before','after'}:raise HTTPException(422,'助手改写操作格式无效')
        before,after=operation.get('before'),operation.get('after')
        if not isinstance(before,str) or not before or not isinstance(after,str) or len(after)>20000:raise HTTPException(422,'助手改写范围或内容无效')
        node=next((node for node in documents.nodes(candidate) if node.get('attrs',{}).get('nodeId')==operation.get('node_id') and node['type'] in ('paragraph','heading')),None)
        if not node:raise HTTPException(422,'助手改写引用了不存在的正文段落')
        matches=[child for child in node.get('content',[]) if child['type']=='text' and before in child.get('text','')]
        if len(matches)!=1 or matches[0]['text'].count(before)!=1:raise HTTPException(422,'助手改写没有唯一匹配原文字段；引用、公式与图表未被重写')
        matches[0]['text']=matches[0]['text'].replace(before,after,1)
    return candidate

@db.atomic
def adopt(pid,proposal_id,expected_revision):
    proposal=store.get(pid,proposal_id,'proposal');data=proposal['data']
    if proposal['revision']!=expected_revision or data.get('adopted_revision_id'):raise HTTPException(409,'改写候选已更新或已采用')
    if data.get('origin')=='product_reuse':
        from .product_service import local_context
        row,current,document,permission=local_context(pid,proposal['owner_id'],data['base_revision_id'])
    else:row,current,document,_,permission=section(pid,proposal['owner_id'],data['base_revision_id'])
    if permission!=data['permission_stamp']:raise HTTPException(409,'候选来源权限已变化，不能采用')
    candidate=documents.normalize(data['document']) if data.get('document') else proposed_document(document,data['operations'])
    from .document_commands import commit
    saved=commit(pid,row['id'],candidate,current['id'] if current else None,proposal.get('source_refs',[]),origin='assistant_draft',task_id=data.get('task_id'))
    store.save(pid,'proposal',{**data,'adopted_revision_id':saved['id']},proposal['id'],proposal['owner_id'],proposal['revision'])
    return saved
