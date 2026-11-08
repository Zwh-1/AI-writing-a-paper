"""Versioned skill discovery, immutable snapshots and bounded progressive loading."""
from copy import deepcopy
from pathlib import Path, PurePosixPath
import hashlib
import re
from fastapi import APIRouter, HTTPException
from . import db, security
from .config import ROOT

router=APIRouter()
SKILL_ROOT=ROOT/'skills'
DEFAULT={'enabled':True,'automatic':True,'selected_ids':[],'max_input_bytes':12000}
HOST_TOOLS=frozenset({'search_writing_materials','read_writing_materials','read_section','read_section_range',
    'list_literature_documents','open_literature_document','read_document_outline','read_document_range',
    'get_evidence','get_run_evidence','load_skill'})
# Explicit host aliases reuse the maintained prompts without opening file access.
HOST_RESOURCES={'academic-expression':'academic-expression.v1.txt',
    'academic-expression-review':'academic-expression-review.v1.txt','academic-visuals':'academic-visuals.v1.txt'}
CONTRACTS={'section_candidate','visual_spec','bibliography_record'}
VALIDATORS={'section_candidate':'schemas.SectionCandidate','visual_spec':'paper_visuals.validate_spec',
            'bibliography_record':'bibliography_format.validate'}


def _hash(data):return hashlib.sha256(data).hexdigest()


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _matches(stage,operations):
    # Underscores separate host stages (draft_unit, studio_paper_visuals).
    # Do not trigger a skill just because its name occurs inside another word.
    # A structural repair retains the guidance of its original generation
    # stage, while still allowing repair-specific skills to opt in.
    base={'candidate_format_repair':'draft','unit_format_repair':'argument_unit'}.get(stage)
    stages=stage+(' '+base if base else '')
    return any(re.search(r'(?<![a-z0-9])'+re.escape(op)+r's?(?![a-z0-9])',stages) for op in operations)


def discover():
    import yaml
    result=[]
    if _linked(SKILL_ROOT):raise ValueError('技能目录不允许符号链接或目录联接')
    for path in sorted(SKILL_ROOT.glob('*/SKILL.md')):
        if _linked(path) or _linked(path.parent):raise ValueError('技能目录不允许符号链接或目录联接')
        raw=path.read_bytes()
        if len(raw)>32000:raise ValueError('技能说明过长')
        match=re.fullmatch(r'---\s*\n(.*?)\n---\s*\n(.*)',raw.decode('utf-8-sig'),re.S)
        if not match:raise ValueError('技能缺少 frontmatter：'+path.parent.name)
        meta=yaml.safe_load(match[1])
        if not isinstance(meta,dict) or meta.get('name')!=path.parent.name or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',meta['name']):raise ValueError('技能名称无效')
        if not isinstance(meta.get('description'),str) or not 1<=len(meta['description'])<=1024:raise ValueError('技能描述无效')
        metadata=meta.get('metadata',{})
        if not isinstance(metadata,dict):raise ValueError('技能 metadata 须为对象')
        tools=metadata.get('tools',[])
        if not isinstance(tools,list) or any(not isinstance(t,str) for t in tools) or set(tools)-HOST_TOOLS:raise ValueError('技能请求了宿主未注册的工具')
        operations=metadata.get('operations',[])
        if not isinstance(operations,list) or any(not isinstance(op,str) or not op or len(op)>100 for op in operations):raise ValueError('技能触发操作无效')
        contracts=metadata.get('contracts',[])
        if not isinstance(contracts,list) or any(not isinstance(c,str) or c not in CONTRACTS for c in contracts):raise ValueError('技能契约未注册')
        standards=metadata.get('standards',[])
        if not isinstance(standards,list) or any(not isinstance(s,str) or len(s)>120 for s in standards):raise ValueError('技能规范关联无效')
        resources=[]
        for resource in sorted(path.parent.rglob('*')):
            if _linked(resource):raise ValueError('技能资源不允许符号链接或目录联接')
            if resource==path or resource.is_dir():continue
            if resource.is_symlink() or not resource.resolve().is_relative_to(path.parent.resolve()):raise ValueError('技能资源越界')
            if resource.suffix not in ('.md','.txt','.json'):raise ValueError('技能仅加载已注册的文字资源')
            data=resource.read_bytes()
            if len(data)>48000:raise ValueError('技能资源过长')
            data.decode('utf-8')
            resources.append({'path':resource.relative_to(path.parent).as_posix(),'hash':_hash(data),'blob':security.put_blob(data)})
        aliases=metadata.get('host_resources',{})
        if not isinstance(aliases,dict):raise ValueError('技能宿主资源无效')
        for name,alias in aliases.items():
            if not isinstance(name,str) or not name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name or not isinstance(alias,str) or alias not in HOST_RESOURCES:
                raise ValueError('技能宿主资源未注册')
            if any(r['path']==name for r in resources):raise ValueError('技能资源同名冲突')
            source=ROOT/'prompts'/HOST_RESOURCES[alias]
            if _linked(source) or _linked(source.parent):raise ValueError('宿主提示资源不允许符号链接或目录联接')
            data=source.read_bytes();data.decode('utf-8')
            if len(data)>48000:raise ValueError('技能资源过长')
            resources.append({'path':name,'hash':_hash(data),'blob':security.put_blob(data),'host_alias':alias})
        triggers=metadata.get('resource_operations',{})
        if not isinstance(triggers,dict) or set(triggers)-{r['path'] for r in resources} or any(not isinstance(ops,list) or any(not isinstance(op,str) or not op for op in ops) for ops in triggers.values()):raise ValueError('技能资源触发无效')
        instructions=match[2].strip().encode('utf-8')
        if not instructions:raise ValueError('技能说明不能为空')
        record={'id':meta['name'],'description':meta['description'],'instructions_hash':_hash(instructions),
                'instructions_blob':security.put_blob(instructions),'resources':resources,'allowed_tools':tools,
                'operations':operations,'standards':standards,'contracts':contracts,
                'validators':{c:VALIDATORS[c] for c in contracts},'resource_operations':triggers}
        record['version']=_hash(db.dumps(record).encode())
        result.append(record)
    return result


def policy(pid):
    security.require_project(pid)
    return {**deepcopy(DEFAULT),**db.setting('skills:'+pid,{})}


def validate_policy(value,catalogue):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT):raise HTTPException(422,'技能策略字段无效')
    if type(value['enabled']) is not bool or type(value['automatic']) is not bool:raise HTTPException(422,'技能开关无效')
    ids=value['selected_ids']
    if not isinstance(ids,list) or len(ids)>8 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids) or set(ids)-{x['id'] for x in catalogue}:raise HTTPException(422,'技能选择不存在或重复')
    if type(value['max_input_bytes']) is not int or not 1000<=value['max_input_bytes']<=32000:raise HTTPException(422,'技能加载预算须为1000至32000字节')


def snapshot(pid):
    catalogue=discover();settings=policy(pid)
    validate_policy(settings,catalogue)
    from .academic_standards import profile
    selected=profile(pid)
    for entry in catalogue:entry['project_standards']=deepcopy(selected)
    return {'policy':settings,'catalogue':catalogue}


def _read(blob,expected):
    data=security.safe_object(blob).read_bytes()
    if _hash(data)!=expected:raise HTTPException(409,'技能资源完整性校验失败')
    return data.decode('utf-8')


def load(snapshot,identity,resource=None):
    if not isinstance(identity,str) or not identity or resource is not None and (not isinstance(resource,str) or not resource):
        raise HTTPException(422,'技能标识或资源路径无效')
    entry=next((x for x in snapshot['catalogue'] if x['id']==identity),None)
    if not entry:raise HTTPException(404,'技能不在本任务冻结目录内')
    if resource:
        name=PurePosixPath(resource)
        if name.is_absolute() or '..' in name.parts or '\\' in resource:raise HTTPException(403,'技能资源路径越界')
        item=next((r for r in entry['resources'] if r['path']==resource),None)
        if not item:raise HTTPException(404,'技能资源不存在')
        text=_read(item['blob'],item['hash']);content_hash=item['hash']
    else:text=_read(entry['instructions_blob'],entry['instructions_hash']);content_hash=entry['instructions_hash']
    return {'skill_id':identity,'version':entry['version'],'resource':resource,'text':text,'content_hash':content_hash,
            'allowed_tools':entry['allowed_tools'],'resources':[{'path':r['path'],'hash':r['hash']} for r in entry['resources']],
            'kind':'workflow_guidance','evidence':False,'standards':entry.get('standards',[]),
            'project_standards':entry.get('project_standards'),'contracts':entry.get('contracts',[]),
            'validators':entry.get('validators',{})}


def enrich(task,system,user,manifest):
    from .ai_runtime.policy import frozen,payload
    snap=frozen(task).get('skills')
    if not snap or not snap['policy']['enabled']:return system,manifest
    setting=snap['policy'];stage=str(manifest.get('writing_stage') or manifest.get('stage') or manifest.get('operation') or payload(task).get('operation') or task['kind'])
    explicit=payload(task).get('skill_ids',setting['selected_ids'])
    if not isinstance(explicit,list) or len(explicit)>8 or any(not isinstance(x,str) for x in explicit) or len(set(explicit))!=len(explicit) or set(explicit)-{x['id'] for x in snap['catalogue']}:raise HTTPException(422,'本任务指定的技能无效')
    selection_hash=_hash(db.dumps([stage,setting,explicit,[(r['id'],r['version']) for r in snap['catalogue']]]).encode())
    selected=[row for row in snap['catalogue'] if row['id'] in explicit or setting['automatic'] and _matches(stage,row['operations'])]
    if manifest.get('skills_selection_hash')==selection_hash:
        usage=manifest.get('skills_usage',[])
        if {r['skill_id'] for r in usage}=={r['id'] for r in selected} and all(
                load(snap,r['skill_id'])['text'].strip() in system and all(
                    load(snap,r['skill_id'],path)['text'].strip() in system for path in r.get('resource_paths',[]))
                for r in usage if r['status']=='loaded'):
            # Transport notes may be added after preparation. Keep the original
            # skill contribution and provenance when all loaded text survives.
            return system,{**manifest,'skills_system_hash':_hash(system.encode())}
    selected.sort(key=lambda row:(0,explicit.index(row['id'])) if row['id'] in explicit else (1,row['id']))
    usage=[];parts=[];used=0
    def block(label,text):return label+'：\n'+text
    def cost(text):return len(('\n\n'+text).encode())
    explicit_remaining=sum(cost(block('技能 '+row['id'],load(snap,row['id'])['text'])) for row in selected
                           if row['id'] in explicit and load(snap,row['id'])['text'].strip() not in system)
    if explicit_remaining>setting['max_input_bytes']:raise HTTPException(422,'明确指定的技能超过加载预算')
    for row in selected:
        item=load(snap,row['id']);present=item['text'].strip() in system
        instruction=block('技能 '+row['id'],item['text']);size=0 if present else cost(instruction)
        if row['id'] in explicit:explicit_remaining-=size
        if used+size>setting['max_input_bytes']:
            if row['id'] in explicit:raise HTTPException(422,'明确指定的技能超过加载预算')
            usage.append({'skill_id':row['id'],'version':row['version'],'status':'deferred','reason':'skill_budget'})
            continue
        used+=size
        if not present:parts.append(instruction)
        loaded=[];deferred=[]
        for path,operations in row.get('resource_operations',{}).items():
            if not _matches(stage,operations):continue
            resource=load(snap,row['id'],path);resource_block=block('技能资源 '+row['id']+'/'+path,resource['text']);size=cost(resource_block)
            # Host prompts and other selected skills may already supply this
            # exact frozen resource. Keep its provenance without duplicating it.
            if resource['text'].strip() in system+'\n'+'\n'.join(parts):
                loaded.append({'path':path,'hash':resource['content_hash'],'delivery':'existing_prompt'})
                continue
            if used+size+explicit_remaining>setting['max_input_bytes']:deferred.append(path);continue
            used+=size;parts.append(resource_block)
            loaded.append({'path':path,'hash':resource['content_hash'],'delivery':'skill_resource'})
        usage.append({'skill_id':row['id'],'version':row['version'],'instructions_hash':item['content_hash'],
                      'status':'loaded','trigger':'explicit' if row['id'] in explicit else 'operation:'+stage,
                      'resource_paths':[r['path'] for r in loaded],'resources':loaded,'deferred_resources':deferred,
                      'standards':row.get('standards',[]),'project_standards':row.get('project_standards'),
                      'contracts':row.get('contracts',[]),'validators':row.get('validators',{})})
    if parts:system+='\n\n'+'\n\n'.join(parts)
    return system,{**manifest,'skills_usage':usage,'skills_input_bytes':used,'skills_stage':stage,
                   'skills_selection_hash':selection_hash,'skills_system_hash':_hash(system.encode())}


def validate_output(task,value,contract):
    """Invoke the actual host validator; skill metadata cannot execute code."""
    if contract not in CONTRACTS:raise ValueError('未知技能输出契约')
    if contract=='section_candidate':
        from .schemas import SectionCandidate
        SectionCandidate.model_validate(value)
    elif contract=='visual_spec':
        from .paper_visuals import validate_spec
        validate_spec(value)
    else:
        from .bibliography_format import validate,normalize
        issues=validate(normalize(value))
        if any(r['severity']=='error' for r in issues):raise HTTPException(422,{'code':'SKILL_OUTPUT_INVALID','issues':issues})
    from .ai_runtime.policy import frozen
    snap=frozen(task).get('skills') or {}
    return {'contract':contract,'validator':VALIDATORS[contract],'status':'pass',
            'scope':'structure_only_not_semantic_compliance',
            'skill_versions':{r['id']:r['version'] for r in snap.get('catalogue',[]) if contract in r.get('contracts',[])}}


@router.get('/projects/{pid}/skills')
def get_skills(pid):
    current=snapshot(pid)
    return {'policy':current['policy'],'catalogue':[{k:v for k,v in x.items() if k not in ('instructions_blob',)} for x in current['catalogue']]}


@router.put('/projects/{pid}/skills/policy')
def put_policy(pid,body:dict):
    settings={**policy(pid),**body};catalogue=discover()
    validate_policy(settings,catalogue)
    db.set_setting('skills:'+pid,settings);db.audit(pid,'skill_policy_saved',settings)
    return get_skills(pid)


@router.get('/projects/{pid}/skills/{identity}')
def get_skill(pid,identity,resource:str|None=None):
    return load(snapshot(pid),identity,resource)
