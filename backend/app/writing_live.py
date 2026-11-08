"""Shared live input versions, repository refresh and evidence for every writer."""
import fnmatch
import hashlib
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path, PurePosixPath
from fastapi import APIRouter, HTTPException
from . import db, documents, security
from .writing_agent.common import digest

router = APIRouter()
_REPOSITORY_WATCH={}
_REPOSITORY_VALIDATION=ContextVar('writing_repository_validation',default=None)


@contextmanager
def repository_validation_scope():
    """Reuse one filesystem observation inside a pure read-only validation.

    The caller must leave this scope before writes, events or HTTP. A new scope
    always observes the filesystem again; this is not the UI's timed cache.
    """
    token=_REPOSITORY_VALIDATION.set({})
    try:yield
    finally:_REPOSITORY_VALIDATION.reset(token)


def versions(pid, ignore_task=None):
    security.require_project(pid)
    head=db.one('SELECT core_version_id,target_snapshot_id,contract_heads_json FROM writing_project_state WHERE project_id=?',(pid,))
    head=head or {'core_version_id':None,'target_snapshot_id':None,'contract_heads_json':'{}'}
    assets=db.rows('SELECT id,sha256,metadata FROM artifacts WHERE project_id=? ORDER BY id',(pid,))
    if ignore_task:
        assets=[a for a in assets if db.loads(a['metadata'],{}).get('writing_task_id')!=ignore_task]
    clock=db.one('SELECT materials,bibliography FROM project_change_clock WHERE project_id=?',(pid,))
    return {'material_revision':clock['materials'] if clock else 0,'bibliography_revision':clock['bibliography'] if clock else 0,
            'fact_version':digest(db.rows('SELECT id,revision,statement,status,verification,source_ids,artifact_id FROM facts WHERE project_id=? ORDER BY id',(pid,))),
            'writing_state':head,'files':db.rows('SELECT id,name,sha256,role,egress,active,status FROM files WHERE project_id=? ORDER BY id',(pid,)),
            'knowledge_heads':db.rows('SELECT h.file_id,h.parse_id FROM knowledge_heads h JOIN files f ON f.id=h.file_id WHERE f.project_id=? ORDER BY h.file_id',(pid,)),
            'selections':db.rows('SELECT k.section_id,k.unit_id FROM knowledge_selections k JOIN sections s ON s.id=k.section_id WHERE s.project_id=? ORDER BY k.section_id,k.unit_id',(pid,)),
            'repositories':db.rows('SELECT id,scope_revision,egress_policy FROM repository_bindings WHERE project_id=? ORDER BY id',(pid,)),
            'artifacts':assets,'format':db.setting('format:'+pid,{}),'template':db.setting('template:'+pid),
            'figure_policy':db.setting('figure_policy:'+pid,{})}


def repository_status(pid, progress=None):
    cached=_REPOSITORY_VALIDATION.get()
    if cached is None or progress is not None:
        return _repository_status(pid,progress)
    if pid not in cached:cached[pid]=_repository_status(pid)
    return deepcopy(cached[pid])


def _repository_status(pid, progress=None):
    from .writing_agent import repository
    state=db.one('SELECT target_snapshot_id FROM writing_project_state WHERE project_id=?',(pid,))
    snapshot=db.one('SELECT * FROM repository_snapshots WHERE id=? AND project_id=?',(state['target_snapshot_id'],pid)) if state and state['target_snapshot_id'] else None
    if not snapshot:return {'bound':False,'changed':False,'snapshot_id':None,'changed_files':[]}
    binding=db.one('SELECT * FROM repository_bindings WHERE id=? AND project_id=?',(snapshot['binding_id'],pid))
    if not binding:return {'bound':False,'changed':True,'snapshot_id':snapshot['id'],'changed_files':[]}
    if snapshot['mode']=='git_commit':return {'bound':True,'changed':False,'snapshot_id':snapshot['id'],'mode':'git_commit','changed_files':[]}
    root=Path(binding['root_path_local']);changed=[]
    if not root.is_absolute() or not root.is_dir() or repository.reparse(root):
        return {'bound':True,'changed':True,'snapshot_id':snapshot['id'],'unavailable':True,'changed_files':[]}
    try:resolved_root=root.resolve()
    except OSError:
        return {'bound':True,'changed':True,'snapshot_id':snapshot['id'],'unavailable':True,'changed_files':[]}
    rows=db.rows('SELECT relative_path,blob_sha256,observed_metadata_json FROM repository_snapshot_files WHERE snapshot_id=?',(snapshot['id'],))
    expected={r['relative_path']:r['blob_sha256'] for r in rows}
    observed={r['relative_path']:db.loads(r['observed_metadata_json'],{}) for r in rows}
    # Ordinary files cannot redirect resolution after the per-file reparse
    # check. Resolve each shared parent once for this scan, including junctions.
    inside_parents={root:True}
    for index,(relative, sha) in enumerate(expected.items(),1):
        if progress and (index%200==0 or index==len(expected)):progress('正在核对已登记的项目文件',index,len(expected))
        path=root/relative
        try:
            if repository.reparse(path):changed.append(relative);continue
            if path.parent not in inside_parents:
                inside_parents[path.parent]=path.parent.resolve().is_relative_to(resolved_root)
            if not inside_parents[path.parent]:changed.append(relative);continue
            stat=path.stat();before=observed[relative]
            if before.get('size')==stat.st_size and before.get('mtime_ns')==stat.st_mtime_ns:continue
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:changed.append(relative)
        except OSError:changed.append(relative)
    scope=db.loads(binding['scope_json'],{});patterns=scope.get('include',[])
    from .writing_agent.common import unblob
    manifest=unblob(snapshot['manifest_blob'])
    omitted={x['path'] for x in [*manifest.get('gaps',[]),*manifest.get('excluded',[])]}
    walked=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        parent=Path(directory);dirs[:]=[d for d in dirs if d not in repository.EXCLUDED and not repository.reparse(parent/d)]
        walked+=len(files)
        if progress and walked%500<len(files):progress('正在扫描项目目录查找新增文件',walked)
        for name in files:
            path=parent/name;relative=path.relative_to(root).as_posix();p=PurePosixPath(relative)
            if relative in expected or name.startswith('.env') or p.suffix.lower() not in repository.EXTENSIONS and name not in ('Dockerfile','AGENTS.md'):continue
            if patterns and not any(fnmatch.fnmatchcase(relative,pattern) for pattern in patterns):continue
            # Files omitted by the capture's secret/size/decoding guards are
            # already recorded as gaps, not newly added project evidence.
            if relative not in omitted:changed.append(relative)
    return {'bound':True,'changed':bool(changed),'snapshot_id':snapshot['id'],'mode':snapshot['mode'],'changed_files':sorted(set(changed))}


def refresh_repository(pid, progress=None):
    from .writing_agent import repository
    from .writing_agent.common import state, update_state
    status=repository_status(pid,progress)
    if not status['bound'] or not status['changed']:return status
    if status.get('unavailable'):raise HTTPException(409,'绑定的项目目录当前不可读取，请重新绑定后写作')
    old=db.one('SELECT binding_id FROM repository_snapshots WHERE id=?',(status['snapshot_id'],))
    binding=db.one('SELECT egress_policy FROM repository_bindings WHERE id=?',(old['binding_id'],))
    if binding['egress_policy']!='selected_content':raise HTTPException(403,'绑定项目禁止外发，不能刷新后用于模型写作')
    if progress:progress('项目代码有变化，正在重新采集完整快照',0)
    captured=repository.capture(pid,old['binding_id'],progress=progress)
    if captured['state']!='ready':raise HTTPException(409,'项目在读取期间仍发生变化，请待文件保存完成后重试')
    current=state(pid);update_state(pid,current['revision'],target_snapshot_id=captured['id'])
    from .writing_agent.impact import changed
    changed(pid,'snapshot',captured['id'],'项目代码已更新，写作依据已切换到新快照')
    _REPOSITORY_WATCH.pop(pid,None)
    return {'bound':True,'changed':False,'snapshot_id':captured['id'],'refreshed':True}


def prepare(pid,section,query,task_id=None):
    from .writing_agent import evidence, repository
    from .writing_agent.common import unblob
    head=db.one('SELECT * FROM writing_project_state WHERE project_id=?',(pid,))
    core=db.one('SELECT * FROM thesis_core_versions WHERE id=? AND project_id=?',(head['core_version_id'],pid)) if head and head['core_version_id'] else None
    cid=db.loads(head['contract_heads_json'],{}).get(section['id']) if head else None
    contract=db.one('SELECT * FROM section_contract_versions WHERE id=? AND project_id=?',(cid,pid)) if cid else None
    c=db.loads(contract['content_json'],{}) if contract else {}
    snapshot=head['target_snapshot_id'] if head else None
    ids=list(dict.fromkeys([*c.get('evidence_ids',[]),*c.get('required_evidence_ids',[])]))
    pinned=set(c.get('required_evidence_ids',[]))
    # Newly read recovery sources belong to this task, not a republished contract.
    recovery_ids=[]
    if task_id:
        task=db.one('SELECT kind,payload FROM tasks WHERE id=? AND project_id=?',(task_id,pid))
        if task and task['kind']=='write_paper':
            task_payload=db.loads(task['payload'],{})
            if 'recovery_evidence_ids_by_section' in task_payload:
                recovery=task_payload['recovery_evidence_ids_by_section']
                if not isinstance(recovery,dict):
                    raise HTTPException(422,'任务恢复证据必须按章节映射')
                if section['id'] in recovery:
                    recovery_ids=recovery[section['id']]
                    if (not isinstance(recovery_ids,list) or len(recovery_ids)>200
                            or any(not isinstance(identity,str) or not identity.strip()
                                   or identity!=identity.strip() for identity in recovery_ids)):
                        raise HTTPException(422,'当前章节的恢复证据须为最多200个有效编号的列表')
                    recovery_ids=list(dict.fromkeys(recovery_ids))
                    ids=list(dict.fromkeys([*ids,*recovery_ids]))
                    pinned.update(recovery_ids)
    recovery_required=set(recovery_ids)
    claim_ids=[*c.get('claim_versions',[]),*(db.loads(core['content_json'],{}).get('claim_versions',[]) if core else [])]
    for claim in claim_ids:
        pinned.update(r['evidence_id'] for r in db.rows('SELECT evidence_id FROM claim_evidence_links WHERE claim_version_id=?',(claim,)))
    items=[];gaps=[]
    for identity in ids:
        item=evidence.get(pid,identity,remote=True)
        if item['origin_type']=='source':
            from .knowledge_service import source_is_excluded
            if source_is_excluded(pid,item['origin_id']):
                if item['id'] in pinned:raise HTTPException(409,'明确指定的项目原文已被排除或替换，请重新选择依据')
                gaps.append('旧章节任务中的资料来源已被排除，未继续使用');continue
        if not item['version_current'] or item['validity'] not in ('current','historical'):
            if identity in recovery_required:
                raise HTTPException(409,'明确指定的任务恢复证据已过期，请重新读取：'+identity)
            gaps.append('章节证据已过期：'+identity);continue
        if item['origin_type']=='code' and item['origin_id']!=snapshot:
            if identity in recovery_required:
                raise HTTPException(409,'明确指定的任务恢复代码证据不属于当前快照：'+identity)
            gaps.append('章节代码证据来自旧快照，已使用当前项目快照重新取证');continue
        items.append(item)
    if section['kind']=='results':
        known={item['id'] for item in items}
        for row in db.rows("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='artifact' AND kind IN ('test_run','data_result') AND validity='current' ORDER BY created_at DESC,id LIMIT 12",(pid,)):
            if row['id'] in known or not evidence.allowed_remote(pid,row['id']):continue
            item=evidence.get(pid,row['id'],remote=True)
            if item['version_current']:
                items.append(item);known.add(item['id'])
    grounding=None
    if snapshot:
        snap=db.one('SELECT * FROM repository_snapshots WHERE id=? AND project_id=?',(snapshot,pid))
        binding=db.one('SELECT * FROM repository_bindings WHERE id=?',(snap['binding_id'],)) if snap else None
        if snap and binding and binding['egress_policy']=='selected_content':
            from .project_grounding import collect as collect_project
            grounding,observed=collect_project(pid,snapshot,{**section,'goal':c.get('goal') or section.get('goal','')},query,items)
            known={item['id'] for item in items}
            items.extend(item for item in observed if item['id'] not in known)
            pinned.update(grounding['required_evidence_ids'])
    from .writing_agent.packing import rank,terms
    query_terms=terms(query)
    prefer_code=section['kind'] not in ('literature','literature_review')
    items.sort(key=lambda item:(item['id'] not in pinned, (item['origin_type']!='code') if prefer_code else (item['kind']!='literature'),
                               -rank(item['payload'].get('text','')+' '+db.dumps(item['payload'].get('locator',{})),query_terms),item['id']))
    fixed=[i for i in items if i['id'] in pinned];code=[i for i in items if i['id'] not in pinned and i['origin_type']=='code'];other=[i for i in items if i['id'] not in pinned and i['origin_type']!='code']
    balanced=[]
    while code or other:
        for group in ([code,other] if prefer_code else [other,code]):
            if group:balanced.append(group.pop(0))
    items=fixed+balanced
    from .paper_format import resolve
    formatting=resolve(pid,section=section)
    assets=[]
    for a in db.rows("SELECT * FROM artifacts WHERE project_id=? AND kind IN ('figure','table') ORDER BY created_at,id",(pid,)):
        meta=db.loads(a['metadata'],{})
        if meta.get('egress','local_only')!='selected_content' or meta.get('source_file_id') and not security.file_allows_remote(meta['source_file_id'],pid):continue
        stale=False
        for eid in meta.get('evidence_ids',[]):
            observed=evidence.get(pid,eid)
            if not observed['version_current'] or observed['validity'] not in ('current','historical') or not evidence.allowed_remote(pid,eid):stale=True;break
            if observed['origin_type']=='code' and snapshot:
                path=observed['payload'].get('path');current_file=db.one('SELECT blob_sha256 FROM repository_snapshot_files WHERE snapshot_id=? AND relative_path=?',(snapshot,path))
                if not current_file or current_file['blob_sha256']!=observed['payload'].get('file_sha256'):stale=True;break
        if stale:gaps.append('图表“'+a['name']+'”的项目依据已更新，未自动沿用');continue
        assets.append({'id':a['id'],'kind':a['kind'],'title':a['name'],'caption':meta.get('spec',{}).get('title',a['name']),
                       'spec':meta.get('spec'),'explanation':meta.get('explanation',''),'purpose':meta.get('figure_purpose','unknown'),
                       'source_revision_ids':meta.get('source_revision_ids',[])})
    payload={'thesis_core':db.loads(core['content_json'],{}) if core else {},'section_contract':c,
             'project_evidence':[{**evidence.for_model(i),'origin_type':i['origin_type'],'file_id':i['payload'].get('file_id'),
                                  'source_id':i['origin_id'] if i['origin_type']=='source' else None} for i in items],
             'automatic_project_evidence_ids':[i['id'] for i in items if i['id'] not in pinned],
             'project_source_policy':'项目核心与章节合同是研究意图，代码观察、测试定义、实际运行和部署证据必须分开。仅使用给定完整观察范围，不能把静态代码当测试通过。',
             'available_visuals':assets,'format_context':{k:formatting[k] for k in ('config','conflicts','version')},'project_input_gaps':gaps}
    manifest={'live_input_versions':versions(pid,task_id),'evidence_versions':{i['id']:i['origin_version'] for i in items},
              'permission_stamps':{i['id']:evidence.permission_stamp(pid,i['id']) for i in items},
              'asset_versions':{a['id']:digest([a['sha256'],db.loads(a['metadata'],{})]) for a in db.rows('SELECT * FROM artifacts WHERE project_id=?',(pid,)) if any(x['id']==a['id'] for x in assets)}}
    if grounding:
        chapter=grounding.pop('chapter_materials',None)
        if section['kind']=='results':
            from .chapter_materials import empty,with_execution_records
            chapter=with_execution_records(chapter or empty(snapshot),section,payload['project_evidence'])
        if chapter and any(chapter.get(lane) for lane in ('database_tables','frontend_pages','api_routes','test_cases','execution_records')):
            payload['chapter_materials']=chapter
        grounding['hash']=digest({key:value for key,value in grounding.items() if key!='hash'})
        payload['project_grounding']=grounding
        manifest['project_grounding_hash']=grounding['hash']
    elif section['kind']=='results':
        from .chapter_materials import empty,with_execution_records
        chapter=with_execution_records(empty(snapshot),section,payload['project_evidence'])
        if chapter['execution_records']:payload['chapter_materials']=chapter
    if payload.get('chapter_materials'):
        required=set(payload['chapter_materials']['required_evidence_ids'])
        payload['automatic_project_evidence_ids']=[eid for eid in payload['automatic_project_evidence_ids'] if eid not in required]
    if recovery_ids:manifest['task_scoped_recovery_evidence_ids']=recovery_ids
    return payload,manifest,items


def validate(pid,manifest,task_id=None):
    if manifest.get('live_input_versions') is not None and manifest['live_input_versions']!=versions(pid,task_id):
        raise HTTPException(409,{'code':'WRITING_INPUT_CHANGED','message':'项目、资料、文献、图表或格式已更新，请基于当前依据重新生成候选'})
    if manifest.get('live_input_versions') is not None and repository_status(pid).get('changed'):
        raise HTTPException(409,{'code':'PROJECT_SOURCE_CHANGED','message':'绑定项目的当前文件已变化，已保留候选；请更新项目依据后重新核对或生成'})


@router.get('/projects/{pid}/writing-live')
def live(pid,stamp:bool=False):
    if stamp:
        security.require_project(pid)
        clock=db.one('SELECT revision FROM project_change_clock WHERE project_id=?',(pid,))
        cached=_REPOSITORY_WATCH.get(pid)
        if not cached or time.monotonic()-cached[0]>5:
            cached=(time.monotonic(),repository_status(pid));_REPOSITORY_WATCH[pid]=cached
        return {'version':str(clock['revision'] if clock else 0)+':'+digest(cached[1])}
    from .figure_standards import inventory
    from .paper_format import resolve
    p=security.require_project(pid);source_versions=versions(pid)
    sections=db.rows("SELECT id,current_revision_id,title,kind FROM sections WHERE project_id=? AND state!='archived' ORDER BY position,id",(pid,))
    numbering={};index=[]
    for s in sections:
        revision=documents.current(s);ids=[]
        for node in documents.nodes(db.loads(revision['document'],{}) if revision else {}):
            if node.get('type')=='citation':
                rid=node.get('attrs',{}).get('citationId')
                if rid not in numbering:numbering[rid]=len(numbering)+1
                ids.append(rid)
        index.append({**s,'citation_ids':ids})
    refs=db.rows('SELECT id,metadata,revision,file_id FROM bibliography WHERE project_id=? ORDER BY id',(pid,))
    facts=db.rows('SELECT id,revision,statement,status,verification FROM facts WHERE project_id=? ORDER BY id',(pid,))
    fmt=resolve(pid)
    fingerprint=digest([source_versions,index,refs,facts,p['brief'],p['rule_package_id'],fmt])
    return {'version':fingerprint,'sections':index,'citation_numbers':numbering,
            'references':[{**r,'metadata':db.loads(r['metadata'],{})} for r in refs],
            'figures':[{k:v for k,v in r.items() if k!='metadata'} for r in inventory(pid)],
            'format':fmt,'formats_by_section':{s['id']:resolve(pid,section=s) for s in sections},
            'material_count':len(source_versions['files']), 'reference_count':len(refs),
            'fact_count':len(facts),'repository':source_versions['writing_state'],
            'evidence_count':db.one("SELECT COUNT(*) n FROM evidence_items WHERE project_id=? AND validity='current'",(pid,))['n']}


@router.post('/projects/{pid}/writing-live/refresh-project')
def refresh(pid):
    security.require_project(pid)
    return refresh_repository(pid)
