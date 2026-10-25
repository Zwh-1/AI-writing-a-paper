from copy import deepcopy
from .. import db,security,documents,rules
from .common import owned,state,section,blob,digest,fail
from . import evidence

def assemble(pid,sid,contract_id,profile,remote=True,evidence_ids=None,operation='generate'):
    p=security.require_project(pid);s=section(sid,pid);head=state(pid);contract=owned('contract',contract_id,pid);c=db.loads(contract['content_json'],{})
    if contract['section_id']!=sid:fail('INVALID_CONTRACT','任务不属于当前章节')
    if not head['core_version_id'] or not head['target_snapshot_id']:fail('PROJECT_NOT_READY','请先发布论文核心并选择代码快照')
    core=owned('core',head['core_version_id'],pid);snapshot=owned('snapshot',head['target_snapshot_id'],pid);core_content=db.loads(core['content_json'],{})
    for cid in [*core_content.get('claim_versions',[]),*c.get('claim_versions',[])]:
        owned('claim',cid,pid)
        for link in db.rows('SELECT evidence_id FROM claim_evidence_links WHERE claim_version_id=?',(cid,)):
            if remote and not evidence.allowed_remote(pid,link['evidence_id']):fail('SOURCE_ACCESS_REVOKED','核心或任务引用了禁止外发的论断依据',403)
    if snapshot['state']!='ready':fail('SNAPSHOT_UNSTABLE','当前来源快照不稳定')
    package=db.one('SELECT * FROM rule_packages WHERE id=? AND project_id=?',(p['rule_package_id'],pid))
    if not package:fail('RULE_PACKAGE_REQUIRED','请先发布论文规则包')
    rr=rules.applicable(package,s,operation);rr=[r for r in rr if r['category']!='format']
    if not rr:fail('RULE_PACKAGE_REQUIRED','当前章节没有适用内容规则')
    file_ids=set()
    for rule in rr:
        if rule.get('source_id'):
            source=db.one('SELECT file_id FROM sources WHERE id=?',(rule['source_id'],))
            if remote and (not source or not security.file_allows_remote(source['file_id'],pid)):fail('SOURCE_ACCESS_REVOKED','适用规则原件禁止外发',403)
            if source:file_ids.add(source['file_id'])
    intent_ids=[]
    for origin_id,content in ((core['id'],db.loads(core['content_json'],{})),(contract['id'],c)):
        text=db.dumps(content);version=digest(text);old=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='author' AND origin_id=? AND origin_version=?",(pid,origin_id,version))
        item=evidence.get(pid,old['id']) if old else evidence.create(pid,{'kind':'author_statement','origin_type':'author','origin_id':origin_id,'text':text,'conditions':['仅代表用户发布的研究意图或章节任务','不能证明实现、执行、部署或效果']})
        intent_ids.append(item['id'])
    current=documents.current(s)
    fact_ids=list(dict.fromkeys([*core_content.get('fact_ids',[]),*c.get('fact_ids',[])]))
    artifact_ids=list(dict.fromkeys([*core_content.get('artifact_ids',[]),*c.get('artifact_ids',[])]))
    facts=[];fact_evidence_ids=[]
    if current and remote:
        from .provenance import revision_permissions
        revision_permissions(pid,current['id'])
    for fid in fact_ids:
        f=db.one('SELECT * FROM facts WHERE id=? AND project_id=?',(fid,pid))
        if not f:fail('INVALID_FACT','论文核心或章节任务引用了不属于本项目的研究事实')
        for source_id in db.loads(f['source_ids'],[]):
            origin=db.one('SELECT file_id FROM sources WHERE id=?',(source_id,))
            if remote and (not origin or not security.file_allows_remote(origin['file_id'],pid)):fail('SOURCE_ACCESS_REVOKED','事实依据禁止外发',403)
        facts.append(f)
        synced=evidence.sync_fact(pid,fid)
        if remote and not evidence.allowed_remote(pid,synced['id']):fail('SOURCE_ACCESS_REVOKED','研究事实或其来源禁止外发',403)
        fact_evidence_ids.append(synced['id'])
    artifact_evidence_ids=[]
    for aid in artifact_ids:
        synced=evidence.sync_artifact(pid,aid)
        if remote and not evidence.allowed_remote(pid,synced['id']):fail('SOURCE_ACCESS_REVOKED','研究产物或其来源禁止外发',403)
        artifact_evidence_ids.append(synced['id'])
    ids=list(dict.fromkeys([*c.get('evidence_ids',[]),*fact_evidence_ids,*artifact_evidence_ids,*(evidence_ids or []),*intent_ids]))
    if len(ids)>80:fail('CONTEXT_BUDGET_EXCEEDED','本单元证据过多，请缩小范围')
    ee=[evidence.get(pid,eid,remote) for eid in ids]
    reference_sources={}
    for e in ee:
        if e['origin_type']=='source':
            source=db.one('SELECT file_id FROM sources WHERE id=?',(e['origin_id'],))
            if source:
                file_ids.add(source['file_id']);reference_sources.setdefault(source['file_id'],[]).append(e['origin_id'])
    references=[{**r,'metadata':db.loads(r['metadata'],{}),'source_ids':reference_sources[r['file_id']]} for r in db.rows('SELECT * FROM bibliography WHERE project_id=?',(pid,)) if r['file_id'] in reference_sources]
    for e in ee:
        if not e['version_current'] or e['validity'] not in ('current','historical'):fail('INPUT_VERSION_CHANGED','证据已过期',409)
    chosen=set(c.get('depends_on_sections',[]));ordered=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,))
    at=next((i for i,x in enumerate(ordered) if x['id']==sid),0)
    chosen.update(x['id'] for x in ordered[max(0,at-1):at+2] if x['id']!=sid)
    related=[];versions={}
    for chapter in ordered:
        if chapter['id'] not in chosen or not chapter['current_revision_id']:continue
        rev=documents.current(chapter)
        if remote:
            from .provenance import revision_permissions
            revision_permissions(pid,rev['id'])
        for d in db.rows("SELECT upstream_id FROM dependencies WHERE revision_id=? AND upstream_type='source'",(rev['id'],)):
            origin=db.one('SELECT file_id FROM sources WHERE id=?',(d['upstream_id'],))
            if remote and (not origin or not security.file_allows_remote(origin['file_id'],pid)):fail('SOURCE_ACCESS_REVOKED','关联正文的派生来源禁止外发',403)
        if chapter['id'] in c.get('depends_on_sections',[]):related_text=rev['text'];scope='full_required_dependency'
        else:
            from .sentences import paragraphs,paragraph_text
            blocks=paragraphs(db.loads(rev['document'],{}));before=next((i for i,x in enumerate(ordered) if x['id']==chapter['id']),0)<at;chosen_blocks=blocks[-2:] if before else blocks[:2];related_text='\n'.join(paragraph_text(n) for n in chosen_blocks);scope='complete_boundary_paragraphs'
        related.append({'section_id':chapter['id'],'title':chapter['title'],'revision_id':rev['id'],'text':related_text,'scope':scope,'full_text_available_via_read_section':True});versions[chapter['id']]=rev['id']
    payload={'project':{'title':p['title'],'brief':db.loads(p['brief'],{})},'thesis_core':core_content,'section':{'id':sid,'title':s['title'],'kind':s['kind'],'goal':s['goal']},'contract':c,'rules':[{'id':r['id'],'statement':r['statement']} for r in rr],'evidence':[{'id':e['id'],'kind':e['kind'],'conditions':e['conditions'],'payload':e['payload']} for e in ee],'facts':facts,'existing_document':db.loads(current['document'],{}) if current else None,'related_sections':related,'terminology':db.rows('SELECT zh,en FROM terms WHERE project_id=?',(pid,))}
    payload['project_inputs']={'rule_package_id':package['id'],'rule_ids':[r['id'] for r in rr],'evidence_ids':[e['id'] for e in ee],'reference_ids':[r['id'] for r in references],'material_evidence_ids':[e['id'] for e in ee if e['kind'] in ('material_observation','literature')]}
    manifest={'project_id':pid,'section_id':sid,'project_state_revision':head['revision'],'core_version_id':head['core_version_id'],'target_snapshot_id':head['target_snapshot_id'],'contract_version_id':contract_id,'rule_package_id':p['rule_package_id'],'evidence_versions':{e['id']:e['origin_version'] for e in ee},'fact_versions':{f['id']:f['revision'] for f in facts},'related_revisions':versions,'base_revision_id':s['current_revision_id'],'rule_ids':[r['id'] for r in rr],'file_ids':sorted(file_ids),'remote':remote,'profile_fingerprint':profile['route_fingerprint'],'prompt_version':'agent.v1','schema_version':'agent.v1','permissions_hash':digest([(e['id'],evidence.allowed_remote(pid,e['id'])) for e in ee])}
    payload['evidence']=[evidence.for_model(e) for e in ee];payload['references']=[{k:r[k] for k in ('id','metadata','source_ids','verification')} for r in references]
    for item,original in zip(payload['evidence'],ee):
        field='thesis_core' if original['origin_type']=='author' and original['origin_id']==core['id'] else 'contract' if original['origin_type']=='author' and original['origin_id']==contract['id'] else None
        if field:
            if db.loads(original['payload'].get('text'),None)==payload[field]:item['payload']={'context_field':field,'scope':'同一输入中该字段的完整作者声明；不代表实现或运行证据','original_payload_hash':original['payload_hash']}
    payload['evidence']=share_exact_code_text(payload['evidence'])
    manifest['permission_stamps']={e['id']:evidence.permission_stamp(pid,e['id']) for e in ee};manifest['project_brief_hash']=digest(db.loads(p['brief'],{}));manifest['context_policy_version']='phase_scoped.v4'
    manifest['reference_versions']={r['id']:r['revision'] for r in references}
    manifest['operation']=operation
    return payload,manifest

def share_exact_code_text(items):
    """Share only byte-identical contained passages; keep every ID, condition and locator."""
    result=deepcopy(items);canonical=[]
    for item in sorted(result,key=lambda e:-len(e.get('payload',{}).get('text',''))):
        p=item.get('payload',{});text=p.get('text')
        if not isinstance(text,str) or not p.get('file_sha256') or not p.get('path'):continue
        shared=None
        for other in canonical:
            q=other['payload']
            if p['file_sha256']!=q['file_sha256'] or p['path']!=q['path'] or p['line_start']<q['line_start'] or p['line_end']>q['line_end']:continue
            lines=q['text'].splitlines(keepends=True);a=p['line_start']-q['line_start'];b=p['line_end']-q['line_start']+1
            if ''.join(lines[a:b])==text:shared=other;break
        if shared:
            p.pop('text');p['exact_text_reference']={'evidence_id':shared['id'],'line_start':p['line_start'],'line_end':p['line_end'],'text_hash':digest(text),'note':'相同行原文已完整出现于指定证据，不是摘要'}
        else:canonical.append(item)
    return result

def select_evidence(items,selected):
    selected=set(selected);by_id={item['id']:item for item in items}
    while True:
        references={by_id[key].get('payload',{}).get('exact_text_reference',{}).get('evidence_id') for key in selected if key in by_id}
        references.discard(None)
        if references-set(by_id):fail('INVALID_EVIDENCE','共享原文引用缺少当前授权证据')
        added=references-selected
        if not added:return [item for item in items if item['id'] in selected]
        selected.update(added)

def save_context(step_id,payload,manifest,system,cfg):
    from ..source_payload import token_upper_bound
    reserve=int(cfg.get('max_output_tokens',8192));limit=int(cfg.get('context_limit',32000))
    def estimate_payload():
        from .identifiers import encode
        model_payload,_=encode(payload,manifest)
        return token_upper_bound(system,db.dumps({'messages':[{'role':'user','content':db.dumps(model_payload)}],'tools':[]}))
    # Optional boundary passages can be recovered by the expansion stage. Keep
    # explicit dependency chapters and every condition/rule/evidence item.
    if estimate_payload()+reserve>limit:
        optional=[r for r in payload.get('related_sections',[]) if r.get('scope')=='complete_boundary_paragraphs']
        if optional:
            payload['related_sections']=[r for r in payload['related_sections'] if r.get('scope')!='complete_boundary_paragraphs']
            payload['omitted_optional_boundary_sections']=[{'section_id':r['section_id'],'revision_id':r['revision_id'],'reason':'budget; full original available through bounded coherence expansion'} for r in optional]
            omitted={r['section_id'] for r in optional};manifest['related_revisions']={sid:rid for sid,rid in manifest.get('related_revisions',{}).items() if sid not in omitted};manifest['optional_context_omissions']=payload['omitted_optional_boundary_sections']
    serialized=db.dumps(payload)
    for profile in ('','gpt-fallback','material-summary'):
        key=security.get_key(profile)
        if key and len(key)>12 and key in serialized:fail('SECRET_IN_CONTEXT','输入中出现已配置凭据，已阻止外发',403)
    from .identifiers import encode
    model_payload,_=encode(payload,manifest)
    frame={'messages':[{'role':'user','content':db.dumps(model_payload)}],'tools':[]}
    estimate=token_upper_bound(system,db.dumps(frame))
    if estimate+reserve>limit:fail('CONTEXT_BUDGET_EXCEEDED','必要规则、证据和正文超过预算；请缩小单元或提高已验证的接口预算',input_estimate=estimate,reserved_output=reserve,limit=limit)
    identity=db.uid();budget={'input_estimate':estimate,'reserved_generated_total':reserve,'context_limit':limit,'estimate_method':'utf8_bytes_plus_protocol_reserve','reasoning_and_visible_share_limit':'provider_specific_not_double_counted'}
    db.execute('INSERT INTO writing_contexts VALUES(?,?,?,?,?,?,?)',(identity,step_id,blob(manifest),blob(payload),digest(payload),db.dumps(budget),manifest.get('permissions_hash','')))
    return identity

def save_wire_context(step_id,frame,manifest,system,cfg):
    from ..source_payload import token_upper_bound
    estimate=token_upper_bound(system,db.dumps(frame));reserve=int(cfg.get('max_output_tokens',8192));limit=int(cfg.get('context_limit',32000))
    if estimate+reserve>limit:fail('CONTEXT_BUDGET_EXCEEDED','实际协议消息、工具和必要原文超过预算，未发送请求',input_estimate=estimate,reserved_output=reserve,limit=limit)
    identity=db.uid();budget={'input_estimate':estimate,'reserved_generated_total':reserve,'context_limit':limit,'estimate_method':'actual_wire_frame_utf8_bytes_plus_protocol_reserve','scope':'actual_messages_tools_and_provider_state'}
    db.execute('INSERT INTO writing_contexts VALUES(?,?,?,?,?,?,?)',(identity,step_id,blob({**manifest,'context_kind':'actual_wire_frame'}),blob({'system':system,'frame':frame}),digest({'system':system,'frame':frame}),db.dumps(budget),manifest.get('permissions_hash','')))
    return identity
