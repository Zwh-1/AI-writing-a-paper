"""GPT Image 2 via its own endpoint, credential, budget reservation and Images API."""
import base64
import hashlib
import io
import json
import math
from urllib.parse import urlsplit
import httpx
from fastapi import APIRouter,HTTPException
from PIL import Image,UnidentifiedImageError
from . import db,provider,security,tasks
from .summary_billing import BudgetExceeded,key_hash

router=APIRouter()
PROFILE='image-generation'
MODEL='gpt-image-2'
DEFAULT={'base_url':'https://api.hanhegufei.online','model':MODEL,'size':'2048x1152','quality':'high',
         'timeout_seconds':600,'max_request_cost':2.0,'image_price':None,'currency':'USD'}
SIZES={'2048x1152','1152x2048','1536x1536','1536x1024','1024x1536','1024x1024'}

class ImageResponseError(ValueError):
    """The server returned success, but the response is unusable and may be charged."""


def config():
    return {**DEFAULT,**db.setting('image_generation_provider',{}),'model':MODEL}


def ready():
    return bool(config()['base_url'] and security.key_status(PROFILE)['configured'])


@router.get('/image-generation/settings')
@provider.settings_atomic
def settings():
    cfg=config();credential=security.key_status(PROFILE)
    return {'provider':cfg,'credential':credential,'ready':bool(cfg['base_url'] and credential['configured']),
            'sizes':sorted(SIZES),'budget_currency':provider.config().get('currency','USD')}


@router.put('/image-generation/settings')
@provider.settings_atomic
def save_settings(body:dict):
    supplied=body.get('provider',{})
    if not isinstance(supplied,dict):raise HTTPException(422,'生图接口设置须为对象')
    if supplied.get('model',MODEL)!=MODEL:raise HTTPException(422,'本生图接口固定使用 gpt-image-2')
    cfg={**config(),**{k:v for k,v in supplied.items() if k in DEFAULT},'model':MODEL}
    if not isinstance(cfg['base_url'],str):raise HTTPException(422,'请填写生图 API 基础地址')
    url=urlsplit(cfg['base_url'].strip())
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise HTTPException(422,'生图地址须为不含凭据、查询参数的 HTTPS 基础地址')
    cfg['base_url']=cfg['base_url'].strip().rstrip('/')
    if not isinstance(cfg['size'],str) or cfg['size'] not in SIZES:raise HTTPException(422,'请选择支持的生图尺寸')
    if cfg['quality'] not in ('auto','low','medium','high'):raise HTTPException(422,'生图画质无效')
    for field,low,high in (('timeout_seconds',30,1800),('max_request_cost',.000001,10000)):
        value=cfg[field]
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not low<=value<=high:
            raise HTTPException(422,'生图配置数值无效：'+field)
    price=cfg['image_price']
    if price is not None and (isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price<0):
        raise HTTPException(422,'每张图片金额须为空或非负有限数值')
    if not isinstance(cfg['currency'],str) or cfg['currency'] not in ('USD','CNY'):raise HTTPException(422,'生图费用币种无效')
    if 'api_key' in body:
        value=body['api_key']
        if not isinstance(value,str) or not value.strip():raise HTTPException(422,'密钥不能为空；留空保存设置会保留原密钥')
        security.save_key(value.strip(),PROFILE)
    if body.get('clear_key') is True:security.save_key('',PROFILE)
    db.set_setting('image_generation_provider',cfg)
    return settings()


@provider.settings_atomic
def snapshot():
    cfg=config()
    try:key=security.get_key(PROFILE)
    except Exception as error:raise HTTPException(422,'独立生图密钥无法解密，请在当前 Windows 用户下重新填写生图 key') from error
    if not key:raise HTTPException(422,'请先在 gpt-image-2 专用设置中填写独立的生图 API key')
    if not cfg['base_url']:raise HTTPException(422,'请填写生图中转站地址')
    return cfg,key


def _input_asset(pid,aid):
    from .paper_visuals import visual_asset
    asset=visual_asset(pid,aid)
    if asset['kind']!='figure':raise HTTPException(422,'图片编辑须选择论文设计示意图')
    from .figure_standards import PROTECTED,inventory
    if asset['metadata'].get('figure_purpose') in PROTECTED or any(r['artifact_id']==aid and r['purpose'] in PROTECTED for r in inventory(pid)):
        raise HTTPException(403,'真实截图、原创或实验结果图片不能使用生图模型改动')
    if asset['metadata'].get('egress','local_only')=='local_only':raise HTTPException(403,'该图片仅限本机使用，不能发送生图模型')
    return asset


@router.post('/projects/{pid}/paper-visuals/image-tasks')
def enqueue_image(pid,body:dict):
    payload=_prepare(pid,body)
    if body.get('brief_hash') is not None and body['brief_hash']!=payload['image_brief']['hash']:
        raise HTTPException(409,'绘图要求或目标正文已变化，请重新预览后生成')
    snapshot()  # A free brief preview never needs a key; a paid task always does.
    return tasks.enqueue(pid,'paper_image',payload,body.get('request_id'))


@router.post('/projects/{pid}/paper-visuals/image-brief')
def preview_brief(pid,body:dict):
    payload=_prepare(pid,body)
    return {'brief':payload['image_brief'],'prompt':payload['prepared_prompt'],'paid_request':False}


@router.get('/projects/{pid}/paper-visuals/positions/{sid}')
def image_positions(pid,sid):
    from .image_briefs import choices
    security.require_project(pid);return choices(pid,sid)


@router.post('/projects/{pid}/paper-visuals/{aid}/alignment-review')
def alignment_review(pid,aid,body:dict):
    from .image_briefs import review
    security.require_project(pid);return review(pid,aid,body)


@router.post('/sections/{sid}/paper-visuals/{aid}/insert')
def insert_image(sid,aid,body:dict):
    from .image_briefs import insert
    return insert(sid,aid,body)


def _prepare(pid,body):
    from .paper_visuals import source_context,check_context,validate_spec,KINDS,_text
    security.require_project(pid)
    kind=body.get('kind')
    if not isinstance(kind,str) or kind not in KINDS or kind=='three_line_table':raise HTTPException(422,'请选择论文设计示意图类型；三线表请使用可编辑表格')
    from .figure_standards import assert_ai_allowed,metadata as figure_metadata
    annotations=figure_metadata(pid,body);assert_ai_allowed(pid,annotations['figure_purpose'])
    cfg=config()
    if cfg['currency']!=provider.config().get('currency','USD'):
        raise HTTPException(422,'生图费用币种与项目预算币种不同，请在专用设置中统一币种后使用')
    instruction=_text(body.get('instruction',''),'生图或修改要求',4000,True)
    title=_text(body.get('title') or KINDS[kind],'图片标题',100)
    context=source_context(pid,body.get('source_context'))
    aid=body.get('source_artifact_id')
    if aid:
        asset=_input_asset(pid,aid)
        context=source_context(pid,{**context,'source_artifact_id':aid})
    sid=body.get('section_id')
    if sid is not None:
        if not isinstance(sid,str):raise HTTPException(422,'章节标识无效')
        section=security.require_section(sid)
        if section['project_id']!=pid or section['state']=='archived':raise HTTPException(404,'生图章节不属于当前项目')
        if not section['current_revision_id']:raise HTTPException(422,'请先保存本节正文，再根据正文生图')
        context=source_context(pid,{**context,'revision_ids':list(dict.fromkeys([*context['revision_ids'],section['current_revision_id']]))})
    for related_sid in annotations['related_section_ids']:
        related_section=security.require_section(related_sid)
        if related_section['current_revision_id']:
            context=source_context(pid,{**context,'revision_ids':list(dict.fromkeys([*context['revision_ids'],related_section['current_revision_id']]))})
    spec=validate_spec(body['current_spec']) if body.get('current_spec') is not None else None
    if spec and spec['kind']!=kind:raise HTTPException(422,'结构类型与图片类型不一致')
    if not instruction and not context['revision_ids'] and spec is None:raise HTTPException(422,'请填写实际设计要求或选择已保存章节')
    if (aid or spec) and not instruction:raise HTTPException(422,'请填写明确的图片修改要求')
    check_context(pid,context)
    # Resolve the prompt before enqueuing so oversized chapters cannot create paid requests.
    payload={'kind':kind,'title':title,'instruction':instruction,'section_id':sid,'source_context':context,
             'current_spec':spec,'source_artifact_id':aid,'provider_profile':PROFILE,**annotations,
             **{k:body[k] for k in ('anchor_node_id','required_labels','forbidden_content','layout') if k in body}}
    from .image_briefs import build,prompt
    payload['image_brief']=build(pid,payload)
    payload['prepared_prompt']=prompt(payload['image_brief'])
    return payload


def _prompt(pid,payload):
    from .image_briefs import prompt
    return payload.get('prepared_prompt') or prompt(payload['image_brief'])


def request_image(cfg,key,prompt,reference=None):
    """One Images API request; no text-model fallback and no implicit retry."""
    values={'model':MODEL,'prompt':prompt,'n':1,'size':cfg['size'],'quality':cfg['quality'],
            'output_format':'png','background':'opaque'}
    headers={'Authorization':'Bearer '+key}
    with provider.client(cfg) as client:
        if reference is None:
            response=client.post(provider.endpoint(cfg['base_url'],'/v1/images/generations'),headers=headers,json=values)
        else:
            response=client.post(provider.endpoint(cfg['base_url'],'/v1/images/edits'),headers=headers,
                                 data={k:str(v) for k,v in values.items()},files=[('image[]',('reference.png',reference,'image/png'))])
    provider.response_error(response,'GPT 生图接口')
    try:data=response.json()
    except ValueError as error:raise ImageResponseError('生图接口未返回有效 JSON，请确认中转站支持 Images API') from error
    if not isinstance(data,dict):raise ImageResponseError('生图响应不是 JSON 对象')
    return data


def _decode(data):
    actual_model=data.get('model')
    if actual_model and (not isinstance(actual_model,str) or actual_model!=MODEL and not actual_model.startswith(MODEL+'-')):
        raise ValueError('中转站返回了其他模型的结果；请确认 gpt-image-2 映射，程序不会自动换模型')
    items=data.get('data')
    if not isinstance(items,list) or len(items)!=1 or not isinstance(items[0],dict):raise ValueError('生图接口未返回一张有效图片')
    encoded=items[0].get('b64_json')
    if not isinstance(encoded,str) or not encoded or len(encoded)>80_000_000:
        raise ValueError('接口未返回有效 b64_json 图片，请确认中转站的 GPT Images 响应格式')
    try:
        original=base64.b64decode(encoded,validate=True)
        with Image.open(io.BytesIO(original)) as image:
            width,height=image.size
            if image.format not in ('PNG','JPEG','WEBP') or min(width,height)<128 or width*height>40_000_000:
                raise ValueError('接口返回的图片格式或尺寸无效')
            image.load();output=io.BytesIO()
            if image.mode=='RGBA':
                background=Image.new('RGBA',image.size,'white');background.alpha_composite(image);image=background.convert('RGB')
            else:image=image.convert('RGB')
            image.save(output,format='PNG',dpi=(300,300))
    except (ValueError,UnidentifiedImageError,OSError,Image.DecompressionBombError) as error:
        raise ValueError('无法读取生图结果：'+str(error)[:200]) from error
    return output.getvalue(),(width,height),items[0].get('revised_prompt')


def generate(task):
    from .paper_visuals import check_context,source_context,_asset_result
    payload=db.loads(task['payload'],{});pid=task['project_id'];context=payload['source_context']
    from .figure_standards import assert_ai_allowed,metadata as figure_metadata
    assert_ai_allowed(pid,payload.get('figure_purpose','design_schematic'))
    from .image_briefs import assert_current
    if not payload.get('image_brief'):payload=_prepare(pid,payload)
    assert_current(pid,payload['image_brief'])
    cfg,key=snapshot();check_context(pid,context)
    if cfg['currency']!=provider.config().get('currency','USD'):raise BudgetExceeded('生图费用币种与项目预算币种不同')
    prompt=_prompt(pid,payload);reference=None
    if payload.get('source_artifact_id'):
        asset=_input_asset(pid,payload['source_artifact_id'])
        path=security.safe_object(asset['sha256'])
        if not path.is_file():raise ValueError('参考图片文件缺失，请重新打开图表')
        reference=path.read_bytes()
    elif payload.get('current_spec'):
        from .paper_visual_render import render_diagram
        reference=render_diagram(payload['current_spec'])[0]
    if reference is not None and len(reference)>50_000_000:raise ValueError('参考图片超过50MB，请缩小图片后再修改')
    reservation=cfg['image_price'] if cfg['image_price'] is not None else cfg['max_request_cost']
    identity=db.uid();manifest={'provider':cfg,'provider_profile':PROFILE,'provider_route':PROFILE,
        'credential_hash':key_hash(key),'operation':'image_edit' if reference is not None else 'image_generate',
        'visual_context':context,'prompt_blob':security.put_blob(prompt.encode('utf-8')),
        'outgoing_context_hash':hashlib.sha256(prompt.encode()).hexdigest(),'source_artifact_id':payload.get('source_artifact_id'),
        'image_brief':payload['image_brief']}
    if reference is not None:manifest['reference_blob']=security.put_blob(reference)
    with db.transaction():
        project=security.require_project(pid)
        used=db.one("SELECT COALESCE(SUM(budget_charge),0) amount FROM generation_budget_usage WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')",(pid,))['amount']
        if used+reservation>project['budget']:raise BudgetExceeded('项目预算不足以预留本次 gpt-image-2 生图费用')
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                   (identity,task['id'],pid,'pending',MODEL,'images',db.dumps(manifest),reservation,db.now()))
    sent=False;received=False
    try:
        assert_ai_allowed(pid,payload.get('figure_purpose','design_schematic'))
        assert_current(pid,payload['image_brief'])
        check_context(pid,context)
        if payload.get('source_artifact_id'):_input_asset(pid,payload['source_artifact_id'])
        tasks.event(task['id'],'正在使用 gpt-image-2 '+('修改图片' if reference is not None else '生成图片'),45)
        db.execute("UPDATE generation_runs SET status='sent' WHERE id=?",(identity,));sent=True
        with provider.request_scope(cfg,key):data=request_image(cfg,key,prompt,reference)
        received=True
        usage=data.get('usage',{}) if isinstance(data.get('usage',{}),dict) else {}
        normalized={'input_tokens':usage.get('input_tokens'),'output_tokens':usage.get('output_tokens'),'raw':usage}
        db.execute('UPDATE generation_runs SET status=?,usage=?,manifest=? WHERE id=?',('completed',db.dumps(normalized),db.dumps(manifest),identity))
        png,size,revised=_decode(data)
        alignment_status='pending_human_review';alignment_message='需逐项核对图片与目标段落'
        try:assert_current(pid,payload['image_brief'])
        except HTTPException as error:
            alignment_status='source_changed';alignment_message=str(error.detail)
        sha=security.put_blob(png);manifest.update({'image_blob':sha,'width':size[0],'height':size[1]})
        spec={'kind':payload['kind'],'title':payload['title'],'prompt':prompt,'render_engine':MODEL,
              'brief_hash':payload['image_brief']['hash'],'prompt_version':payload['image_brief']['version']}
        metadata={'generator':'paper-image-v1','render_engine':MODEL,'renderer_version':'gpt-image-2-v1','spec':spec,**figure_metadata(pid,payload),
                  'egress':'selected_content','source_revision_ids':context['revision_ids'],'parent_artifact_id':payload.get('source_artifact_id'),
                  'media_type':'image/png','width':size[0],'height':size[1],'requested_size':cfg['size'],'quality':cfg['quality'],
                  'generation_run_id':identity,'task_id':task['id'],'prompt_blob':manifest['prompt_blob'],
                  'revised_prompt':revised if isinstance(revised,str) else None,'image_brief':payload['image_brief'],
                  'alignment_status':alignment_status,'alignment_message':alignment_message,'prompt_version':payload['image_brief']['version']}
        aid=db.uid()
        with db.transaction():
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,payload['title'],'figure',sha,db.dumps(metadata),db.now()))
            manifest['artifact_id']=aid
            db.execute('UPDATE generation_runs SET cost=?,manifest=?,remote_id=? WHERE id=?',
                       (cfg['image_price'],db.dumps(manifest),str(data.get('id') or data.get('created') or ''),identity))
        asset=_asset_result({**db.one('SELECT * FROM artifacts WHERE id=?',(aid,)),'metadata':metadata})
        return {'asset':asset,'spec':spec,'source_context':source_context(pid,context),'render_engine':MODEL,
                'missing_materials':[],'automatic_acceptance':False,'run_id':identity}
    except Exception as error:
        received=received or isinstance(error,ImageResponseError)
        before_send=isinstance(error,(httpx.ConnectError,httpx.ConnectTimeout))
        unknown=sent and not received and not before_send and (isinstance(error,httpx.RequestError) or isinstance(error,provider.ProviderError) and error.unknown)
        status='completed_invalid' if received else 'failed_before_send' if not sent or before_send else 'outcome_unknown' if unknown else 'rejected'
        # A received response may be charged even when its image bytes are invalid.
        db.execute('UPDATE generation_runs SET status=?,error=?,manifest=? WHERE id=?',(status,security.redact(str(error).replace(key,'[已隐藏密钥]')),db.dumps(manifest),identity))
        if isinstance(error,httpx.RequestError):raise provider.ProviderError('生图连接失败' if before_send else '生图请求已发送，远端结果未知；请核对后重试',unknown=unknown) from error
        raise
