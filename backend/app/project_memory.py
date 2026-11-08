"""Project memory reuses versioned notes, source permissions and immutable blobs."""
from copy import deepcopy
import hashlib
import re
from fastapi import APIRouter, HTTPException
from . import db, security, documents, resource_refs
from . import research_records as records

router=APIRouter()
DEFAULT={'enabled':True,'max_input_bytes':10000,'recent_messages':16,'history_input_bytes':16000}
KINDS={'author_decision','working_hypothesis','open_question','chapter_navigation'}


def policy(pid):
    security.require_project(pid)
    return {**DEFAULT,**db.setting('memory_policy:'+pid,{})}


def validate_policy(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT) or type(value['enabled']) is not bool:raise HTTPException(422,'记忆策略字段无效')
    for key,lo,hi in (('max_input_bytes',1000,32000),('recent_messages',4,80),('history_input_bytes',2000,64000)):
        if type(value[key]) is not int or not lo<=value[key]<=hi:raise HTTPException(422,'记忆参数超范围：'+key)


def _entries(pid):
    security.require_project(pid)
    return [{**row,'data':db.loads(row['data'],{})} for row in db.rows(
        "SELECT * FROM studio_records WHERE project_id=? AND kind='note' AND deleted_at IS NULL "
        "AND json_extract(data,'$.memory_kind') IS NOT NULL ORDER BY updated_at DESC,id",(pid,))]


def state(pid,row,remote=False):
    data=row['data']
    if data.get('memory_state')!='active':return 'superseded'
    for sid,rid in data.get('section_versions',{}).items():
        sec=db.one("SELECT current_revision_id,state FROM sections WHERE id=? AND project_id=?",(sid,pid))
        if not sec or sec['state']=='archived' or sec['current_revision_id']!=rid:return 'stale'
    try:
        if not resource_refs.validate(pid,data.get('source_refs',[]),allow_stale=True,remote=remote):return 'stale'
        if remote:
            from .writing_agent.provenance import revision_permissions
            for rid in data.get('section_versions',{}).values():revision_permissions(pid,rid)
    except HTTPException:return 'unavailable'
    return 'current'


def view(pid,offset=0,limit=100):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise HTTPException(422,'记忆分页范围无效')
    rows=[];subjects={};entries=_entries(pid)
    for index,row in enumerate(entries):
        visible=offset<=index<offset+limit
        decision=row['data']['memory_kind']=='author_decision'
        if not visible and not decision:continue
        current=state(pid,row)
        if visible:rows.append({'id':row['id'],'revision':row['revision'],'data':row['data'],'state':current})
        if current=='current' and decision:
            subjects.setdefault(row['data']['subject'],[]).append(row['id'])
    conflicts=[{'subject':key,'memory_ids':ids,'state':'unresolved'} for key,ids in subjects.items() if len(ids)>1]
    return {'policy':policy(pid),'entries':rows,'conflicts':conflicts,'offset':offset,'limit':limit,'total':len(entries)}


@router.get('/projects/{pid}/memory')
def get_memory(pid,offset:int=0,limit:int=100):return view(pid,offset,limit)


@router.get('/projects/{pid}/context-preview')
@db.read_session()
def preview_context(pid,section_id:str):
    sec=security.require_section(section_id)
    if sec['project_id']!=pid:raise HTTPException(404,'章节不属于项目')
    from .writing_continuity import collect
    from .source_payload import token_upper_bound
    from . import provider
    data,manifest,_=collect(pid,section_id)
    cfg=provider.config();inputs=token_upper_bound(data['continuity']['policy'],db.dumps(data))
    reserve=int(cfg.get('max_output_tokens',8192));limit=int(cfg.get('context_limit',100000))
    return {'mode':policy_mode(pid),'paper_context':data['paper_context'],
            'provided_section_ids':[r['section_id'] for r in data['related_sections']]+([section_id] if data.get('current_section_context') else []),
            'catalogue_section_ids':[r['section_id'] for r in data.get('section_catalogue',[])],
            'memory':data.get('project_memory',{'items':[],'bytes':0,'deferred':[]}),
            'input_upper_bound':inputs,'reserved_output':reserve,'context_limit':limit,'fits_continuity_layer':inputs+reserve<=limit,
            'scope':'衔接层与项目记忆的预览；实际写作还需加入规则、证据、技能和输出契约，以发送前预算为准'}


def policy_mode(pid):
    from .writing_flow import settings
    return settings(pid)['context_mode']


@router.put('/projects/{pid}/memory/policy')
def put_policy(pid,body:dict):
    values={**policy(pid),**body};validate_policy(values)
    db.set_setting('memory_policy:'+pid,values);db.audit(pid,'memory_policy_saved',values)
    return values


@router.post('/projects/{pid}/memory')
@db.atomic
def save_memory(pid,body:dict):
    security.require_project(pid)
    allowed={'id','expected_revision','kind','subject','text','source_refs','section_id','supersedes'}
    if set(body)-allowed:raise HTTPException(422,'记忆字段无效')
    kind=body.get('kind');text=body.get('text');subject=body.get('subject','')
    if not isinstance(kind,str) or kind not in KINDS or not isinstance(text,str) or not 1<=len(text.strip())<=6000 or not isinstance(subject,str) or not 1<=len(subject.strip())<=200:raise HTTPException(422,'记忆类型、主题或内容无效')
    for key in ('id','section_id'):
        if body.get(key) is not None and (not isinstance(body[key],str) or not 1<=len(body[key])<=160):raise HTTPException(422,'记忆标识无效：'+key)
    if body.get('expected_revision') is not None and (type(body['expected_revision']) is not int or body['expected_revision']<1):raise HTTPException(422,'记忆版本无效')
    supersedes=body.get('supersedes',[])
    if not isinstance(supersedes,list) or len(supersedes)>20 or any(not isinstance(x,str) or not 1<=len(x)<=160 for x in supersedes) or len(set(supersedes))!=len(supersedes):raise HTTPException(422,'被替代记忆范围无效')
    identity=body.get('id');old=None
    if identity:
        old=records.get(pid,identity,'note')
        if not old['data'].get('memory_kind'):raise HTTPException(404,'记录不是项目记忆')
    refs=body.get('source_refs',deepcopy(old['data'].get('source_refs',[])) if old else [])
    if not isinstance(refs,list) or len(refs)>30:raise HTTPException(422,'记忆来源范围无效')
    resource_refs.validate(pid,refs)
    versions=deepcopy(old['data'].get('section_versions',{})) if old else {};sid=body.get('section_id')
    if sid:
        section=security.require_section(sid)
        if section['project_id']!=pid:raise HTTPException(404,'记忆章节不属于项目')
        if not section['current_revision_id']:raise HTTPException(422,'章节尚无正文')
        versions={sid:section['current_revision_id']}
        ref=resource_refs.make(pid,'revision',section['current_revision_id'])
        if ref not in refs:refs=[*refs,ref]
    if kind=='chapter_navigation' and not versions:raise HTTPException(422,'章节导航须绑定正文版本')
    data={'title':'记忆：'+subject.strip(),'text':text.strip(),'memory_kind':kind,'subject':subject.strip(),
          'memory_state':'active','source_refs':refs,'section_versions':versions,
          'classification':'derived_navigation' if kind=='chapter_navigation' else 'author_intent' if kind=='author_decision' else 'unverified_working_memory',
          'use_for_writing':False,'evidence':False}
    duplicates=[x for x in _entries(pid) if x['data'].get('memory_state')=='active' and x['data'].get('memory_kind')==kind and x['data'].get('subject')==subject.strip() and x['data'].get('text')==text.strip() and x['data'].get('section_versions',{})==versions and x['data'].get('source_refs',[])==refs]
    duplicate=duplicates[0] if duplicates and not identity else None
    result=duplicate or records.save(pid,'note',data,identity=identity,expected_revision=body.get('expected_revision'))
    for previous in supersedes:
        if duplicate and previous==result['id']:continue
        old=records.get(pid,previous,'note')
        if previous==result['id'] or not old['data'].get('memory_kind'):raise HTTPException(422,'不能替代当前记忆或普通随记')
        records.save(pid,'note',{**old['data'],'memory_state':'superseded','superseded_by':result['id']},identity=previous,expected_revision=old['revision'])
    db.audit(pid,'project_memory_saved',{'id':result['id'],'revision':result['revision'],'kind':kind})
    return result


@router.delete('/projects/{pid}/memory/{identity}')
def remove(pid,identity,expected_revision:int):
    row=records.get(pid,identity,'note')
    if not row['data'].get('memory_kind'):raise HTTPException(404,'记录不是项目记忆')
    return records.remove(pid,identity,expected_revision)


def chapter_navigation(section,revision,limit=900):
    """Extract whole author-written paragraphs; never invent a semantic summary."""
    blocks=[(i,n.get('attrs',{}).get('nodeId'),documents.plain(n).strip())
            for i,n in enumerate(db.loads(revision['document'],{}).get('content',[]))
            if n['type'] in ('paragraph','heading') and documents.plain(n).strip()]
    chosen=[];remaining=limit
    ranked=sorted(blocks,key=lambda row:(not bool(re.search(r'\d|结论|局限|范围|定义',row[2])),row[0]))
    for index,node_id,text in ranked:
        if len(text)<=remaining:chosen.append((index,node_id,text));remaining-=len(text)
    chosen.sort()
    return {'section_id':section['id'],'revision_id':revision['id'],'title':section['title'],
            'extracts':[{'paragraph_index':i,'node_id':n,'text':t} for i,n,t in chosen],
            'scope':'selected_whole_paragraphs','evidence':False,'complete':len(chosen)==len(blocks),
            'content_hash':hashlib.sha256(revision['text'].encode()).hexdigest(),
            'notice':'原文选段导航；未选段落没有提供，不代表语义摘要完整或事实已核验'}


@router.post('/projects/{pid}/memory/rebuild-chapters')
@db.atomic
def rebuild(pid):
    security.require_project(pid);saved=[]
    for sec in db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived'",(pid,)):
        revision=documents.current(sec)
        if not revision:continue
        nav=chapter_navigation(sec,revision)
        text='\n'.join(x['text'] for x in nav['extracts'])
        if not text:continue
        old=[x['id'] for x in _entries(pid) if x['data'].get('memory_kind')=='chapter_navigation' and sec['id'] in x['data'].get('section_versions',{}) and state(pid,x)!='current' and x['data'].get('memory_state')=='active']
        saved.append(save_memory(pid,{'kind':'chapter_navigation','subject':sec['title'],'text':text,'section_id':sec['id'],'supersedes':old})['id'])
    return {'memory_ids':saved,'method':'extractive_whole_paragraphs','model_calls':0}


@db.read_session()
def snapshot(pid):
    settings=policy(pid);validate_policy(settings)
    entries=[]
    if settings['enabled']:
        for row in _entries(pid):
            if state(pid,row)!='current':continue
            data=deepcopy(row['data']);raw=db.dumps(data).encode()
            entries.append({'id':row['id'],'revision':row['revision'],'blob':security.put_blob(raw),
                            'hash':hashlib.sha256(raw).hexdigest()})
    return {'policy':settings,'entries':entries}


@db.read_session()
def context(pid,snap,query='',section_id=None):
    if not snap or not snap['policy']['enabled']:return {'items':[],'deferred':[],'bytes':0,'evidence':False}
    rows=[];deferred=[]
    words=set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]{2}',query.lower()))
    for ref in snap['entries']:
        try:
            row=records.get(pid,ref['id'],'note')
            if row['revision']!=ref['revision'] or state(pid,row,True)!='current':
                deferred.append({'id':ref['id'],'reason':'stale_or_unavailable'});continue
            raw=security.safe_object(ref['blob']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=ref['hash']:raise HTTPException(409,'记忆快照损坏')
            data=db.loads(raw.decode())
            score=(10 if data['memory_kind']=='author_decision' else 0)+(5 if section_id in data.get('section_versions',{}) else 0)+sum(word in data['subject'].lower()+' '+data['text'].lower() for word in words)
            rows.append((score,ref,data))
        except HTTPException as error:
            if error.status_code!=404:raise
            deferred.append({'id':ref['id'],'reason':'deleted'})
    rows.sort(key=lambda r:(-r[0],r[1]['id']));items=[];used=0;subjects={}
    for _,ref,data in rows:
        if data['memory_kind']=='author_decision':subjects.setdefault(data['subject'],[]).append(ref['id'])
    by_id={ref['id']:(ref,data) for _,ref,data in rows}
    def item(ref,data):
        return {'id':ref['id'],'revision':ref['revision'],'kind':data['memory_kind'],'subject':data['subject'],
              'text':data['text'],'classification':data['classification'],'section_versions':data.get('section_versions',{}),
              'source_refs':data.get('source_refs',[]),'evidence':False}
    handled=set()
    for _,ref,data in rows:
        if ref['id'] in handled:continue
        conflict=subjects.get(data['subject'],[]) if data['memory_kind']=='author_decision' else []
        # A budget must never silently choose one side of an unresolved decision.
        group=[item(*by_id[identity]) for identity in conflict] if len(conflict)>1 else [item(ref,data)]
        handled.update(x['id'] for x in group)
        size=sum(len(db.dumps(x).encode()) for x in group)
        if used+size>snap['policy']['max_input_bytes']:
            deferred.extend({'id':x['id'],'reason':'memory_conflict_budget' if len(conflict)>1 else 'memory_budget'} for x in group)
            continue
        items.extend(group);used+=size
    return {'items':items,'deferred':deferred,'bytes':used,'evidence':False,
            'conflicts':[{'subject':s,'memory_ids':ids} for s,ids in subjects.items() if len(ids)>1],
            'policy':'记忆仅为作者意图、待解问题或原文导航；不证明研究事实，冲突不得自行选择覆盖'}


def task_context(pid,query='',section_id=None,task=None):
    if task is None:
        from .task_transitions import active_owner
        owner=active_owner()
        if owner and owner['project_id']==pid:task=db.one('SELECT * FROM tasks WHERE id=?',(owner['id'],))
    if task:
        if task['project_id']!=pid:raise HTTPException(404,'记忆任务不属于当前项目')
        from .ai_runtime.policy import frozen
        snap=frozen(task).get('memory')
    else:snap=snapshot(pid)
    return context(pid,snap,query,section_id)


def validate_context(pid,value):
    for item in (value or {}).get('items',[]):
        row=records.get(pid,item['id'],'note')
        if row['revision']!=item['revision'] or state(pid,row,True)!='current':
            raise HTTPException(409,'已加载的项目记忆或其来源已变化，请重新准备上下文')


def history_context(rows,pending_id,settings):
    """Keep complete turns and visible omissions; no silent last-40 truncation."""
    at=next((i for i,row in enumerate(rows) if row['id']==pending_id),None)
    if at is None:raise HTTPException(409,'当前消息不在对话历史中')
    rows=rows[:at+1]
    selected=[];used=0;deferred=[]
    stopped=False
    for row in reversed(rows):
        value={'id':row['id'],'role':row['data']['role'],'text':row['data']['text']}
        size=len(db.dumps(value).encode())
        if not selected and size>settings['history_input_bytes']:raise HTTPException(422,'当前消息超过对话上下文预算')
        if stopped or len(selected)>=settings['recent_messages'] or used+size>settings['history_input_bytes']:
            stopped=True;deferred.append(row['id']);continue
        selected.append(value);used+=size
    selected.reverse()
    while len(selected)>1 and selected[0]['role']!='user':
        deferred.append(selected.pop(0)['id'])
    # Keep selected earlier complete user turns as attributed episodic memory.
    # This is exact text with message IDs, not an invented conversation summary.
    selected_ids={x['id'] for x in selected};episodes=[];episode_bytes=0
    for row in rows:
        if row['id'] in selected_ids or row['data']['role']!='user':continue
        episode={'message_id':row['id'],'role':'user','text':row['data']['text'],
                 'classification':'prior_user_request','evidence':False}
        size=len(db.dumps(episode).encode())
        if episode_bytes+size<=min(4000,max(0,settings['history_input_bytes']-sum(len(db.dumps(x).encode()) for x in selected))):
            episodes.append(episode);episode_bytes+=size
    return {'messages':selected,'history_scope':{'provided_message_ids':[x['id'] for x in selected],
            'omitted_message_ids':[x['id'] for x in rows if x['id'] not in {s['id'] for s in selected}],'complete':not deferred,'bytes':sum(len(db.dumps(x).encode()) for x in selected),
            'notice':'未提供消息仅可回查，不推定其内容；重要作者决定可单独保存为项目记忆'},
            'conversation_memory':{'episodes':episodes,'bytes':episode_bytes,'method':'whole_attributed_user_messages',
                 'notice':'早期用户请求的完整原话，不代表已执行、已确认或事实依据'}}
