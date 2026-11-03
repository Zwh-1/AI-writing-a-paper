"""Research studio endpoints reuse the existing authenticated API and task runner."""
import base64
import io
import mimetypes
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from . import db,security,tasks,documents,workspace_maintenance
from .product_contracts import ReuseCommand,RelationQuery,ExecutionCommand
from . import research_records as store,literature_connectors as imports,assistant_service as ai,reading_service as reading,research_delivery as exports,parsing_service as parsing

router=APIRouter()
BASE='/projects/{pid}/studio'

@router.get('/projects/{pid}/products')
def project_products(pid:str,query:str='',kind:str='',section_id:str='',limit:int=40,offset:int=0):
    from .product_service import catalog
    return catalog(pid,query,kind,section_id,limit,offset)

@router.get('/projects/{pid}/products/{kind}/{identity}')
def project_product(pid:str,kind:str,identity:str):
    from .product_service import item
    return item(pid,kind,identity)

@router.post('/projects/{pid}/products/preview')
def preview_product(pid:str,body:dict):
    from .product_service import content
    value=content(pid,body)
    return {key:value[key] for key in ('title','text','source_ref','kind','classification','visual')}

@router.post('/projects/{pid}/products/reuse')
def reuse_product(pid:str,body:ReuseCommand):
    from .product_service import reuse
    return reuse(pid,body.model_dump(exclude_unset=True))

@router.post('/projects/{pid}/products/relations')
def product_relations(pid:str,body:RelationQuery):
    from .product_relations import lookup
    return lookup(pid,body.source.model_dump(exclude_unset=True),body.limit)

@router.post('/projects/{pid}/research-sync/snapshot')
def research_snapshot(pid:str,body:dict):
    from .research_sync import snapshot
    return snapshot(pid,body.get('base'))

@router.post('/projects/{pid}/research-sync/apply')
def research_merge(pid:str,body:dict):
    from .research_sync import apply
    return apply(pid,body)

@router.get('/projects/{pid}/research-sync/conflicts')
def research_conflicts(pid:str):
    from .research_sync import conflicts
    return conflicts(pid)

@router.post('/projects/{pid}/research-sync/conflicts/{identity}')
def resolve_research_conflict(pid:str,identity:str,body:dict):
    from .research_sync import resolve
    return resolve(pid,identity,body)

@router.post('/projects/{pid}/workspace/query')
def workspace_query(pid:str,body:dict):
    from .workspace_query import query
    return query(pid,body)

@router.get('/projects/{pid}/capabilities')
def capabilities(pid:str):
    from .capability_service import status
    return status(pid)

@router.post('/projects/{pid}/resource-refs/resolve')
def resolve_ref(pid:str,body:dict):
    from .resource_refs import resolve
    return resolve(pid,body)

@router.get('/projects/{pid}/research-content/search')
def derived_search(pid:str,query:str,limit:int=20):
    from .research_workflows import search_derived
    return search_derived(pid,query,limit)

@router.post('/projects/{pid}/annotations/{identity}/notes')
def annotation_note(pid:str,identity:str,body:dict):
    from .research_workflows import annotation_note
    return annotation_note(pid,identity,body.get('section_id'))

@router.post('/projects/{pid}/reading-files/{fid}/evidence')
def selection_evidence(pid:str,fid:str,body:dict):
    from .research_workflows import selection_evidence
    return selection_evidence(pid,fid,body)

@router.put('/projects/{pid}/notes/{identity}/section')
def attach_note(pid:str,identity:str,body:dict):
    from .research_workflows import note_section
    return note_section(pid,identity,body.get('section_id'),body.get('revision'))

@router.post('/projects/{pid}/notes/{identity}/proposals')
def note_proposal(pid:str,identity:str,body:dict):
    from .research_workflows import note_proposal
    return note_proposal(pid,identity,body.get('section_id'),body.get('revision_id'))

@router.post('/projects/{pid}/workspace-intents')
def create_intent(pid:str,body:dict):
    from .workspace_intents import create
    return create(pid,body)

@router.get('/projects/{pid}/workspace-intents')
def pending_intents(pid:str,section_id:str):
    from .workspace_intents import pending
    return pending(pid,section_id)

@router.post('/projects/{pid}/workspace-intents/{identity}/finish')
def finish_intent(pid:str,identity:str,body:dict):
    from .workspace_intents import finish
    return finish(pid,identity,body)

@router.get('/projects/{pid}/asset-library/{identity}/versions')
def asset_versions(pid:str,identity:str):
    from .asset_service import get
    get(pid,identity)
    return db.rows('SELECT * FROM asset_versions WHERE project_id=? AND asset_id=? ORDER BY revision DESC',(pid,identity))

@router.get('/asset-versions/{identity}/file')
def version_file(identity:str):
    row=db.one('SELECT * FROM asset_versions WHERE id=?',(identity,))
    if not row:raise HTTPException(404,'资产版本不存在')
    security.require_project(row['project_id'])
    return FileResponse(security.safe_object(row['sha256']))

@router.get(BASE+'/status')
def status(pid:str):
    security.require_project(pid)
    return {'version':1,'records':db.rows('SELECT kind,count(*) AS count FROM studio_records WHERE project_id=? AND deleted_at IS NULL GROUP BY kind',(pid,)),
            'agents':ai.agents(pid),'zotero':imports.zotero_settings(pid),'parsing':parsing.settings(pid),'features':['url_import','zotero','tags','reading','translation','annotations','webpage','chat','notes','completion','comments','canvas','latex','openalex','layout','cache']}

@router.get(BASE+'/library')
def library(pid:str,query:str='',tag:str=''):
    security.require_project(pid);items=[]
    for row in db.rows("SELECT * FROM bibliography WHERE project_id=? AND COALESCE(json_extract(metadata,'$._studio_deleted'),0)=0 ORDER BY created_at DESC",(pid,)):
        metadata=db.loads(row['metadata'],{})
        if query and query.casefold() not in db.dumps(metadata).casefold() or tag and tag not in metadata.get('tags',[]):continue
        items.append({**row,'metadata':metadata})
    return {'items':items,'tags':sorted({tag for row in items for tag in row['metadata'].get('tags',[]) if isinstance(tag,str)}),
            'files':db.rows("SELECT id,name,extension,status,pages,role FROM files WHERE project_id=? AND active=1 AND role IN ('research','example','draft') ORDER BY created_at DESC",(pid,))}

@router.post(BASE+'/imports/url')
def import_url(pid:str,body:dict):
    security.require_project(pid)
    if not isinstance(body.get('url'),str) or not 1<=len(body['url'])<=12000:raise HTTPException(422,'文件链接无效')
    return tasks.enqueue(pid,'studio',{**body,'action':'import_url'})

@router.put(BASE+'/tags')
def tags(pid:str,body:dict):return imports.update_tags(pid,body.get('reference_ids'),body.get('tags'),body.get('mode','replace'))

@router.get(BASE+'/zotero')
def zotero(pid:str):return {**imports.zotero_settings(pid),'last_sync':db.setting('studio:zotero:last:'+pid)}

@router.put(BASE+'/zotero')
def configure_zotero(pid:str,body:dict):return imports.zotero_settings(pid,body)

@router.post(BASE+'/zotero/sync')
def sync_zotero(pid:str,body:dict):
    security.require_project(pid);return tasks.enqueue(pid,'studio',{'action':'zotero','force':body.get('force',False)})

@router.post(BASE+'/zotero/oauth')
def authorize_zotero(pid:str,request:Request):
    callback=str(request.base_url).rstrip('/')+'/api/v1/studio/zotero/callback'
    return imports.begin_oauth(pid,callback)

@router.get('/studio/zotero/callback')
def oauth_callback(oauth_token:str='',oauth_verifier:str=''):
    imports.finish_oauth(oauth_token,oauth_verifier)
    return HTMLResponse('<!doctype html><html lang="zh"><meta charset="utf-8"><title>Zotero 已连接</title><body><p>Zotero 已连接。请回到砚台并刷新连接状态。</p><button onclick="window.close()">关闭此页</button><script>if(window.opener)window.opener.postMessage({type:"yantai-zotero-connected"},window.location.origin)</script></body></html>')

@router.post(BASE+'/openalex/search')
def search_openalex(pid:str,body:dict):
    if body.get('ai_plan'):
        security.require_project(pid);return tasks.enqueue(pid,'studio',{**body,'action':'literature'})
    return imports.openalex(pid,body)

@router.post(BASE+'/openalex/import')
def import_openalex(pid:str,body:dict):
    metadata=body.get('metadata',{})
    if not isinstance(metadata,dict) or not str(metadata.get('title','')).strip():raise HTTPException(422,'书目信息无效')
    if body.get('download') and metadata.get('pdf_url'):return tasks.enqueue(pid,'studio',{'action':'import_url','url':metadata['pdf_url'],'metadata':metadata})
    return imports.upsert_reference(pid,metadata,provider='openalex',external=str(metadata.get('id') or metadata.get('doi') or store.digest(metadata)))

@router.post(BASE+'/abstract/translate')
def translate_abstract(pid:str,body:dict):
    store.require_reference(pid,body.get('reference_id',''));return tasks.enqueue(pid,'studio',{**body,'action':'abstract'})

@router.get(BASE+'/abstract/{rid}')
def current_abstract(pid:str,rid:str,language:str='zh-CN'):
    row=store.require_reference(pid,rid);stamp=reading.text_hash(row['metadata'].get('abstract',''))
    return next((item for item in reversed(store.list_records(pid,'translation',rid,500)) if item['data'].get('language')==language and item['data'].get('source_hash')==stamp),None)

@router.get(BASE+'/reader/{fid}')
def reader(pid:str,fid:str,language:str='zh-CN'):return reading.document(pid,fid,language)

@router.post(BASE+'/reader/{fid}/translate')
def translate(pid:str,fid:str,body:dict):
    store.require_file(pid,fid,True);return tasks.enqueue(pid,'studio',{**body,'file_id':fid,'action':'translate'})

@router.put(BASE+'/reader/{fid}/progress')
def reading_progress(pid:str,fid:str,body:dict):return reading.progress(pid,fid,body)

@router.post(BASE+'/reader/{fid}/annotations')
def annotate(pid:str,fid:str,body:dict):return reading.annotate(pid,fid,body)

@router.post(BASE+'/reader/{fid}/guide')
def generate_guide(pid:str,fid:str):
    store.require_file(pid,fid,True);return tasks.enqueue(pid,'studio',{'action':'guide','file_id':fid})

@router.post(BASE+'/reader/{fid}/webpage')
def generate_webpage(pid:str,fid:str,body:dict):
    store.require_file(pid,fid,True);return tasks.enqueue(pid,'studio',{**body,'action':'webpage','file_id':fid})

@router.get(BASE+'/webpages/{identity}/download')
def webpage_download(pid:str,identity:str):
    record=store.get(pid,identity,'webpage');html=record['data']['html']
    policy='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data: blob:; connect-src \'none\'">'
    return StreamingResponse(io.BytesIO((policy+html).encode()),media_type='text/html',headers={'Content-Disposition':'attachment; filename="paper-interactive.html"'})

@router.get(BASE+'/records')
def records(pid:str,kind:str,owner:str|None=None,query:str='',limit:int=200,offset:int=0):return store.list_records(pid,kind,owner,limit,query,offset)

@router.post(BASE+'/records')
def create_record(pid:str,body:dict):
    from .research_commands import create_record as execute
    return execute(pid,body)

@router.get(BASE+'/records/{identity}')
def record(pid:str,identity:str):return store.get(pid,identity)

@router.get(BASE+'/records/{identity}/versions')
def record_versions(pid:str,identity:str):return store.versions(pid,identity)

@router.get(BASE+'/records/{identity}/sync-history')
def record_sync_history(pid:str,identity:str):return store.sync_history(pid,identity)

@router.post(BASE+'/records/{identity}/restore-sync')
def restore_sync_record(pid:str,identity:str,body:dict):
    return store.restore_sync(pid,identity,body.get('history_id'),body.get('target_revision'),body.get('revision'))

@router.post(BASE+'/records/{identity}/restore')
def restore_record(pid:str,identity:str,body:dict):return store.restore(pid,identity,body.get('target_revision'),body.get('revision'))

@router.get(BASE+'/assets/{identity}/metadata')
def asset_metadata(pid:str,identity:str):
    from .asset_service import get
    return get(pid,identity)

@router.get(BASE+'/assets')
def assets(pid:str,query:str='',tag:str=''):
    from .asset_service import catalog
    return catalog(pid,query,tag)

@router.post(BASE+'/assets')
def create_asset(pid:str,body:dict):
    from .asset_service import create
    return create(pid,body)

@router.put(BASE+'/assets/{identity}/content')
@db.atomic
def update_asset(pid:str,identity:str,body:dict):
    from .asset_service import update
    return update(pid,identity,body)

@router.put(BASE+'/records/{identity}')
def update_record(pid:str,identity:str,body:dict):
    from .research_commands import update_record as execute
    return execute(pid,identity,body)

@router.delete(BASE+'/records/{identity}')
def delete_record(pid:str,identity:str,revision:int):return store.remove(pid,identity,revision)

@router.get(BASE+'/conversations/{identity}/messages')
def messages(pid:str,identity:str):return ai.messages(pid,identity)

@router.post(BASE+'/conversations/{identity}/messages')
def send_message(pid:str,identity:str,body:dict):return ai.send_message(pid,identity,body)

@router.post(BASE+'/notes/{identity}/ai')
def note_ai(pid:str,identity:str,body:dict):
    store.get(pid,identity,'note');return tasks.enqueue(pid,'studio',{**body,'action':'note','note_id':identity})

@router.post(BASE+'/completion')
def complete(pid:str,body:dict):
    section=security.require_section(body.get('section_id',''))
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于当前项目')
    return tasks.enqueue(pid,'studio',{**body,'action':'complete'},'studio-completion:'+store.digest(body))

@router.get(BASE+'/comments')
def comments(pid:str,section_id:str):
    section=security.require_section(section_id)
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于当前项目')
    return [{**row,'current':ai.comment_context(pid,row)[3]} for row in store.list_records(pid,'comment',section_id)]

@router.post(BASE+'/proposals/{identity}/adopt')
def adopt_proposal(pid:str,identity:str,body:dict):
    from .assistant_context import adopt
    return adopt(pid,identity,body.get('revision'))

@router.post(BASE+'/comments')
def add_comment(pid:str,body:dict):return ai.add_comment(pid,body)

@router.post(BASE+'/comments/{identity}/reply')
def comment_ai(pid:str,identity:str):
    store.get(pid,identity,'comment');return tasks.enqueue(pid,'studio',{'action':'comment_reply','comment_id':identity})

@router.post(BASE+'/canvas/{identity}/image')
def canvas_image(pid:str,identity:str,body:dict):
    from .asset_service import publish_canvas
    return publish_canvas(pid,identity,body)

@router.get(BASE+'/assets/{identity}')
def artifact_file(pid:str,identity:str):
    security.require_project(pid);row=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(identity,pid))
    if not row or not row['sha256']:raise HTTPException(404,'资源不存在')
    metadata=db.loads(row['metadata'],{})
    media=metadata.get('media_type') or mimetypes.guess_type(row['name'])[0]
    suffix='.txt'
    if row['kind'] in ('figure','image','screenshot'):
        with security.safe_object(row['sha256']).open('rb') as stream:header=stream.read(16)
        media,suffix=('image/jpeg','.jpg') if header.startswith(b'\xff\xd8\xff') else ('image/gif','.gif') if header.startswith(b'GIF8') else ('image/webp','.webp') if header[:4]==b'RIFF' and header[8:12]==b'WEBP' else ('image/png','.png')
    elif media=='application/vnd.openxmlformats-officedocument.wordprocessingml.document':suffix='.docx'
    filename=row['name'] if '.' in row['name'] else row['name']+suffix
    return FileResponse(security.safe_object(row['sha256']),media_type=media or 'application/octet-stream',filename=filename)

@router.post(BASE+'/exports/latex')
def export_latex(pid:str,body:dict):
    security.require_project(pid);return tasks.enqueue(pid,'studio',{**body,'action':'latex','language':body.get('language','zh')})

@router.post(BASE+'/deletion/preview')
def deletion_preview(pid:str,body:dict):return workspace_maintenance.deletion_preview(pid,body.get('reference_ids'))

@router.post(BASE+'/deletion')
def deletion(pid:str,body:dict):return workspace_maintenance.delete_references(pid,body)

@router.get('/studio/cache')
def cache():return workspace_maintenance.cache_status()

@router.post('/studio/cache/clear')
def clear_cache(body:dict):return workspace_maintenance.clear_cache(body)

@router.get('/studio/cache/orphans')
def orphan_preview():return workspace_maintenance.orphan_objects()

@router.post('/studio/cache/orphans')
def orphan_clear(body:dict):return workspace_maintenance.orphan_objects(body)

@router.get(BASE+'/parsing')
def parse_settings(pid:str):return parsing.settings(pid)

@router.put(BASE+'/parsing')
def configure_parse(pid:str,body:dict):return parsing.settings(pid,body)

@router.get(BASE+'/parsing/health')
def parsing_health(pid:str):return parsing.health(pid)

@router.post(BASE+'/parsing/{fid}')
def advanced_parse(pid:str,fid:str,body:dict):
    store.require_file(pid,fid);return tasks.enqueue(pid,'studio',{**body,'action':'parse','file_id':fid})

@router.post(BASE+'/parse-results/{identity}/adopt')
def adopt_parse(pid:str,identity:str,body:dict):return parsing.adopt(pid,identity,body.get('revision'))

@router.put(BASE+'/credentials/{name}')
def credential(pid:str,name:str,body:dict):
    security.require_project(pid)
    if name not in ('openalex',):raise HTTPException(422,'凭据类型无效')
    store.write_secret(name,body.get('value',''));return {'configured':bool(store.read_secret(name))}

def process(task,payload):
    from .research_tasks import process as handle
    return handle(task,payload)

@router.post(BASE+'/metadata-graph')
def metadata_graph(pid:str):
    security.require_project(pid);return tasks.enqueue(pid,'studio',{'action':'metadata_graph'})

@router.get(BASE+'/execution')
def execution_settings(pid:str):
    from .execution_service import settings
    return settings(pid)

@router.get(BASE+'/execution/results')
def execution_result(pid:str,message_id:str):
    store.get(pid,message_id,'message')
    row=db.one("SELECT id,status,result,error FROM tasks WHERE project_id=? AND kind='studio' AND json_extract(payload,'$.action')='execute' AND json_extract(payload,'$.message_id')=? ORDER BY created_at DESC,id DESC LIMIT 1",(pid,message_id))
    return {'task':row,'result':db.loads(row['result'],None) if row and row['status']=='succeeded' else None}

@router.put(BASE+'/execution')
def configure_execution(pid:str,body:dict):
    from .execution_service import settings
    return settings(pid,body)

@router.get(BASE+'/execution/health')
def execution_health(pid:str):
    from .execution_service import health
    return health(pid)

@router.post(BASE+'/execution/install')
def install_execution(pid:str):
    security.require_project(pid);return tasks.enqueue(pid,'studio',{'action':'install_execution'})

@router.post(BASE+'/execution')
def execute_python(pid:str,body:ExecutionCommand):
    body=body.model_dump(exclude_unset=True)
    if body.get('message_id'):store.get(pid,body['message_id'],'message')
    if body.get('product_refs'):
        from .product_execution import prepare
        from .execution_service import settings
        prepare(pid,body,settings(pid)['provider']=='remote-http')
    security.require_project(pid);return tasks.enqueue(pid,'studio',{**body,'action':'execute'})
