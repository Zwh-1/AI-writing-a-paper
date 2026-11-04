import json
from fastapi import HTTPException
from .. import db,security,documents,provider
from .common import owned,candidate,state,section,blob,unblob,digest,fail,dependency
from .models import SessionInput,StartSessionInput
from . import profiles,context,prompts,provider_adapter,tools,judgment,drafting,sentences,verification,coherence

STAGES=('investigate','judgment','judgment_check','draft','annotation','semantic','coherence','regression')

@db.atomic
def start(pid,body):
    """Start from project data and current settings, with normal version guards."""
    b=StartSessionInput.model_validate(body);p=security.require_project(pid);s=section(b.section_id,pid);head=state(pid)
    if s['state']=='archived':fail('SECTION_ARCHIVED','已归档章节不能生成',409)
    if s['kind']=='abstract_en':fail('ABSTRACT_TRANSLATION_REQUIRED','英文摘要直接翻译已保存的中文摘要，请打开当前章节正文进行翻译')
    if head['revision']!=b.expected_project_state_revision or s['current_revision_id']!=b.base_revision_id:fail('INPUT_VERSION_CHANGED','项目或正文版本已变化，请重新读取后再开始写作',409)
    if not p['rule_package_id']:fail('RULE_PACKAGE_REQUIRED','请先发布本项目论文规则包')
    from .launch import preflight
    readiness=preflight(pid,s['id'],b.profile_id,b.budget_limit)
    if not readiness['can_start']:
        first=readiness['blockers'][0]
        fail(first['code'],first['message'],409 if first['code'] in ('PROFILE_STALE','SECTION_ARCHIVED','INPUT_VERSION_CHANGED') else 422,blockers=readiness['blockers'])
    profile=profiles.for_session(pid,b.profile_id)[0] if b.profile_id else profiles.current_default(pid)
    policy={'budget_limit':b.budget_limit if b.budget_limit is not None else readiness['default_budget']}
    if b.verification_profile_id:
        profiles.for_session(pid,b.verification_profile_id)
        policy['stage_profiles']={stage:b.verification_profile_id for stage in ('judgment_check','annotation','semantic','coherence','regression')}
    elif not b.profile_id:
        verifier=profiles.current_verifier(pid,profile)
        if verifier['id']!=profile['id']:policy['stage_profiles']={stage:verifier['id'] for stage in ('judgment_check','annotation','semantic','coherence','regression')}
        discourse=profiles.current_verifier(pid,profile,3072)
        if discourse['id']!=profile['id']:policy.setdefault('stage_profiles',{}).update({stage:discourse['id'] for stage in ('semantic','coherence')})
    if not head['core_version_id'] or not head['target_snapshot_id'] or not head['contracts'].get(s['id']):
        from .contracts import bootstrap_project
        bootstrap_project(pid);head=state(pid)
    contract_id=head['contracts'].get(s['id'])
    if not contract_id:fail('CONTRACT_REQUIRED','当前章节尚未建立写作任务')
    return create(pid,{'section_id':s['id'],'operation':'generate_unit','base_revision_id':b.base_revision_id,'expected_project_state_revision':head['revision'],'contract_version_id':contract_id,'profile_id':profile['id'],'policy':policy})

@db.atomic
def create(pid,body):
    b=SessionInput.model_validate(body);p=security.require_project(pid);s=section(b.section_id,pid)
    if s['kind']=='abstract_en':fail('ABSTRACT_TRANSLATION_REQUIRED','英文摘要直接翻译已保存的中文摘要，请在提纲计划或写作工作台点击“翻译中文摘要”')
    head=state(pid);contract=owned('contract',b.contract_version_id,pid);profile=profiles.get(pid,b.profile_id)
    if head['policy'].get('agent_writing_enabled') is False:fail('FEATURE_DISABLED','此项目已关闭有据写作流程',409)
    if head['revision']!=b.expected_project_state_revision or s['current_revision_id']!=b.base_revision_id:fail('INPUT_VERSION_CHANGED','项目或正文版本已变化',409)
    if head['contracts'].get(s['id'])!=contract['id']:fail('INPUT_VERSION_CHANGED','请选择当前已发布的章节任务',409)
    if not profile['configuration_current']:fail('PROFILE_STALE','模型配置已变化',409)
    policy={'max_revision_rounds':2,'accept_policy':'draft','budget_limit':p['budget'],'target_node_ids':b.target_node_ids}
    allowed={'max_revision_rounds','accept_policy','budget_limit','stage_profiles','revision_instructions','revision_candidate_id'}
    if set(b.policy)-allowed:fail('INVALID_POLICY','会话策略包含未授权字段')
    policy.update(b.policy)
    if policy['accept_policy'] not in ('draft','strict') or type(policy['max_revision_rounds']) is not int or not 0<=policy['max_revision_rounds']<=2:fail('INVALID_POLICY','采用或修订策略无效')
    if isinstance(policy['budget_limit'],bool) or not isinstance(policy['budget_limit'],(int,float)) or not 0<policy['budget_limit']<=p['budget']:fail('INVALID_POLICY','会话预算必须在项目预算之内')
    if not isinstance(policy.get('stage_profiles',{}),dict) or set(policy.get('stage_profiles',{}))-set(STAGES):fail('INVALID_POLICY','阶段配置名称或结构无效')
    for identity in policy.get('stage_profiles',{}).values():profiles.get(pid,identity)
    if policy.get('revision_candidate_id'):
        source_candidate=candidate(policy['revision_candidate_id'],pid)
        if owned('session',source_candidate['session_id'],pid)['section_id']!=s['id']:fail('INVALID_REVISION','修订候选不属于当前章节')
        from .impact import check_manifest
        if check_manifest(pid,unblob(source_candidate['decision_blob'])['manifest']):fail('INPUT_VERSION_CHANGED','旧候选依据已变化，请按新版本任务重新调查',409)
        from .revision import baseline
        policy['revision_baseline_blob']=blob(baseline(pid,source_candidate['id'],policy.get('revision_instructions','核对原问题并保留正确命题')))
    elif b.operation=='revise_unit':fail('CANDIDATE_REQUIRED','修订必须给出已保存的原候选')
    payload,manifest=context.assemble(pid,s['id'],contract['id'],profile,True,operation='revise' if b.operation=='revise_unit' else 'generate')
    identity=db.uid();db.execute('INSERT INTO writing_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,pid,s['id'],b.operation,'queued',head['target_snapshot_id'],db.dumps(manifest),profile['id'],db.dumps(policy),db.now(),db.now()))
    enqueue(identity,'investigate')
    return get(pid,identity)

def enqueue(session_id,stage,iteration=0):
    from .. import tasks
    s=db.one('SELECT * FROM writing_sessions WHERE id=?',(session_id,));key=digest([session_id,stage,iteration,db.loads(s['base_versions_json'],{})]);old=db.one('SELECT * FROM writing_steps WHERE session_id=? AND stage=? AND iteration=? AND input_hash=?',(session_id,stage,iteration,key))
    if old:return old
    identity=db.uid();task=tasks.enqueue(s['project_id'],'agent_step',{'step_id':identity},'writing-step:'+identity)
    db.execute('INSERT INTO writing_steps VALUES(?,?,?,?,?,?,?,?,?,?)',(identity,session_id,stage,iteration,key,'queued',0,task['id'],None,None))
    return db.one('SELECT * FROM writing_steps WHERE id=?',(identity,))

def outputs(session_id):
    return {r['stage']:unblob(r['output_blob']) for r in db.rows("SELECT * FROM writing_steps WHERE session_id=? AND state='completed' AND output_blob IS NOT NULL ORDER BY iteration",(session_id,))}

def get(pid,identity):
    s=owned('session',identity,pid);ss=db.rows('SELECT * FROM writing_steps WHERE session_id=? ORDER BY rowid',(identity,));candidates=db.rows('SELECT id,parent_candidate_id,text_hash,created_at FROM writing_candidates WHERE session_id=? ORDER BY created_at',(identity,))
    for step in ss:
        step['output']=unblob(step['output_blob']) if step['output_blob'] else None;step['error']=db.loads(step['error_json'],None)
    budget=db.one("SELECT COUNT(*) requests FROM generation_budget_usage WHERE json_extract(manifest,'$.writing_session_id')=? AND status NOT IN ('rejected','failed_before_send')",(identity,))
    from .budget import scope_charge
    budget['charged']=scope_charge('writing_session_id',identity);budget['method']='ungrouped_costs_plus_full_touched_group_upper_bound'
    return {**s,'base_versions':db.loads(s['base_versions_json'],{}),'policy':db.loads(s['policy_json'],{}),'steps':ss,'candidates':candidates,'budget':budget}

def assert_lease(step_id,epoch):
    st=db.one('SELECT * FROM writing_steps WHERE id=?',(step_id,))
    if not st or st['lease_epoch']!=epoch or st['state']!='running':fail('STEP_LEASE_EXPIRED','旧执行者不能提交当前步骤',409)
    s=db.one('SELECT status FROM writing_sessions WHERE id=?',(st['session_id'],))
    task=db.one('SELECT cancel_requested,attempt,status FROM tasks WHERE id=?',(st['task_id'],))
    if not task or task['attempt']!=epoch or task['status']!='running':fail('STEP_LEASE_EXPIRED','任务代次或执行状态已变化',409)
    if task and task['cancel_requested'] or s['status']=='cancelled':raise InterruptedError('写作会话已取消')

def current_investigation_evidence(pid,identities):
    """Use the final reading revision while retaining immutable tool observations."""
    from ..writing_tool_runtime import current_evidence
    return current_evidence(pid,identities)


def investigate(task,step,session,profile,payload,manifest):
    from ..writing_tool_runtime import run_investigation
    return run_investigation(task,step,session,profile,payload,manifest)

def process_step(task,payload):
    st=db.one('SELECT * FROM writing_steps WHERE id=?',(payload['step_id'],))
    if not st:fail('NOT_FOUND','写作步骤不存在',404)
    session=owned('session',st['session_id'],task['project_id']);pid=session['project_id'];epoch=task['attempt']
    if st['state']=='completed':return unblob(st['output_blob'])
    if session['status']=='cancelled':raise InterruptedError('会话已取消')
    with db.transaction():
        active_task=db.one('SELECT attempt,status FROM tasks WHERE id=?',(task['id'],))
        if not active_task or active_task['attempt']!=epoch or active_task['status']!='running':fail('STEP_LEASE_EXPIRED','当前任务租约已失效',409)
        db.execute("UPDATE writing_steps SET state='running',lease_epoch=? WHERE id=?",(epoch,st['id']));db.execute("UPDATE writing_sessions SET status='running',updated_at=? WHERE id=?",(db.now(),session['id']))
    try:
        from .impact import check_manifest
        frozen_base=db.loads(session['base_versions_json'],{})
        if check_manifest(pid,frozen_base) or section(session['section_id'],pid)['current_revision_id']!=frozen_base.get('base_revision_id'):fail('INPUT_VERSION_CHANGED','会话依据或基础正文已变更，请建立新会话',409)
        previous=outputs(session['id']);policy=db.loads(session['policy_json'],{});profile=profiles.get(pid,policy.get('stage_profiles',{}).get(st['stage'],session['profile_id']));_,cfg,_=profiles.for_session(pid,profile['id'])
        base=db.loads(session['base_versions_json'],{});extra=previous.get('investigate',{}).get('evidence_ids',[]);data,manifest=context.assemble(pid,session['section_id'],base['contract_version_id'],profile,True,extra,'revise' if session['operation']=='revise_unit' else 'generate')
        from ..writing_tool_runtime import augment_context
        augment_context(pid,data,manifest,previous.get('investigate',{}))
        manifest.update(writing_session_id=session['id'],writing_step_id=st['id'],stage=st['stage'],lease_epoch=epoch)
        system={'investigate':prompts.INVESTIGATE,'judgment':prompts.JUDGMENT+' 另返回blocking_gaps列表。只有缺少当前任务必需命题的依据才列入blocking_gaps；任务本来不评价的性能、部署和未运行测试应作为限制保留，不要求补齐后才能写静态实现。section.title已给定，不得声称章标题尚未确定；没有逐小节清单、固定引用数量或年份范围不单独构成事实依据缺口，允许按已有章标题与研究主题组织论证。','judgment_check':judgment.CHECK_PROMPT,'draft':prompts.EDIT if session['operation']=='revise_unit' else prompts.DRAFT,'annotation':prompts.ANNOTATE,'semantic':prompts.VERIFY,'coherence':prompts.COHERENCE,'regression':prompts.VERIFY}[st['stage']]
        if st['stage']=='draft':system+=' 需要引用文献时在该段加reference_ids列表，只能使用references中有具体原文依据的给定ID，不手写猜测编号。references为空就不生成文献引用。'
        if 'judgment' in previous:data['judgment']=previous['judgment']['judgment']
        if policy.get('revision_instructions'):data['revision_instructions']=policy['revision_instructions']
        if policy.get('revision_candidate_id'):
            prior=candidate(policy['revision_candidate_id'],pid);prior_manifest=unblob(prior['decision_blob'])['manifest']
            if check_manifest(pid,prior_manifest):fail('INPUT_VERSION_CHANGED','修订所用旧候选依据已变化',409)
            if st['stage']=='draft':
                data['revision_source_document']=unblob(prior['document_blob'])
                data['revision_scope']={'requested_problem':policy.get('revision_instructions',''),'other_prior_findings_are_not_assumed_resolved':True}
        if st['stage'] in ('investigate','judgment','judgment_check'):
            data.pop('existing_document',None)
            data.pop('current_section_context',None);data.pop('paper_context',None);data.pop('continuity',None)
            data['related_sections']=[r for r in data.get('related_sections',[]) if r.get('scope')=='full_required_dependency']
            required={r['section_id'] for r in data['related_sections']};manifest['related_revisions']={sid:rid for sid,rid in manifest.get('related_revisions',{}).items() if sid in required}
            if policy.get('revision_baseline_blob'):
                baseline=unblob(policy['revision_baseline_blob']);data['previous_claims_to_check']=[{key:c[key] for key in ('local_id','statement','conditions','not_established') if key in c} for c in baseline['claims']]
            from .launch import PHASE_SCOPE
            data['phase_scope']=PHASE_SCOPE
        candidate_id=previous.get('draft',{}).get('candidate_id');analysis_id=previous.get('annotation',{}).get('analysis_id')
        if analysis_id and db.one('SELECT candidate_id FROM document_analyses WHERE id=?',(analysis_id,))['candidate_id']!=candidate_id:analysis_id=None
        if candidate_id:
            c=candidate(candidate_id,pid);data['candidate_document']=unblob(c['document_blob'])
        if analysis_id:data['sentences']=sentences.get(pid,analysis_id)['sentences']
        if st['stage']=='annotation':
            checkpoint=unblob(st['output_blob']) if st['output_blob'] else {}
            if checkpoint.get('analysis_id'):analysis_id=checkpoint['analysis_id']
            if not analysis_id:
                analysis=sentences.analyze(pid,data['candidate_document'],candidate_id=candidate_id);analysis_id=analysis['id']
                db.execute('UPDATE writing_steps SET output_blob=? WHERE id=? AND lease_epoch=?',(blob({'analysis_id':analysis_id}),st['id'],epoch))
            data['sentences']=[{k:r[k] for k in ('id','node_id','text','start_cp','end_cp')} for r in sentences.get(pid,analysis_id)['sentences']]
            # Independent sentence extraction needs the original sentences once,
            # not the same prose in both a document tree and a second list.
            for redundant in ('candidate_document','judgment','existing_document','related_sections','current_section_context','paper_context','revision_source_document','revision_findings'):data.pop(redundant,None)
            data['analysis_scope']='按原文顺序逐句抽取和核对依据；跨章关系另由语义及篇章阶段检查'
        if st['stage']=='semantic':
            for redundant in ('sentences','existing_document','revision_source_document','revision_findings'):data.pop(redundant,None)
            data['locator_scope']='本阶段用candidate_document中的真实nodeId定位；句子ID未提供时应为空，具体原句写入message。完整正文、判定和证据均保留。'
        if st['stage']=='draft':
            data['length_guidance']={'target_words_for_section':section(session['section_id'],pid)['target_words'],'unit_goal':'本次只完成任务规定的完整论证单元，不靠重复或虚构凑字数','do_not_repeat_project_metadata':True,'do_not_expand_scope_to_all_missing_experiments':True}
            if policy.get('target_node_ids'):
                from ..writing_continuity import selection_context
                existing=data.get('existing_document') or {'type':'doc','content':[]};targets=set(policy['target_node_ids'])
                data['selection_context']=selection_context(existing,targets)
                data['existing_document']={'type':'doc','content':[node for node in existing.get('content',[]) if node.get('attrs',{}).get('nodeId') in targets]}
                data['target_node_ids']=policy['target_node_ids'];data['scope']='selected_paragraphs'
                system+=' 本次只输出existing_document中选中段落的替换稿，结合selection_context的只读前后文完成承接，不输出选区外的正文。'
        if st['stage'] in ('annotation','semantic'):
            relevant={e['id'] for e in data['evidence']}
            if st['stage']=='semantic' and candidate_id:
                analysis_row=db.one('SELECT id FROM document_analyses WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1',(candidate_id,))
                if analysis_row:
                    relevant={link['evidence_id'] for link in db.rows('SELECT DISTINCT ce.evidence_id FROM sentence_claim_links sc JOIN sentence_spans sp ON sp.id=sc.sentence_id JOIN claim_evidence_links ce ON ce.claim_version_id=sc.claim_version_id WHERE sp.analysis_id=?',(analysis_row['id'],))}
                    relevant.update(eid for claim in data.get('judgment',{}).get('claims',[]) for eid in claim['evidence_ids'])
            data['evidence']=[{key:e[key] for key in ('id','kind','conditions','payload')} for e in context.select_evidence(data['evidence'],relevant)]
        if st['stage']=='coherence':
            for redundant in ('evidence','existing_document','sentences','revision_source_document','revision_findings'):data.pop(redundant,None)
            data['review_scope']='检查当前连续正文和有关边界的指代、组织与推理关系；证据支持性由前序阶段核对，本阶段不重新认证事实'
        if st['stage'] in ('semantic','coherence') and candidate_id:
            document=data.get('candidate_document',{});plain=all(n.get('type')=='paragraph' and all(child.get('type') in ('text','citation') for child in n.get('content',[])) for n in document.get('content',[]))
            if plain:
                data['candidate_paragraphs']=[sentences.paragraph_for_context(n,i+1) for i,n in enumerate(document['content'])]
                data.pop('candidate_document',None)
                data['locator_scope']='完整候选正文逐段列于candidate_paragraphs；文本中的引用占位符按顺序对应reference_ids和citation_positions_cp。node_id只用给定段落编号，句子ID未提供时必须为空。全篇疑点可使用空node_id；不要填章节号、命题号或猜测编号。'
        from .packing import fit
        fit(data,manifest,system,cfg)
        context_id=context.save_context(st['id'],data,manifest,system,cfg) if st['stage'] not in ('annotation','coherence','regression') else None
        if st['stage']=='investigate':out=investigate(task,st,session,profile,data,manifest)
        else:
            if st['stage']=='annotation':
                annotation_field_corrections=[]
                batch_size=2 if int(cfg['max_output_tokens'])<=4096 else 6
                batches=[data['sentences'][i:i+batch_size] for i in range(0,len(data['sentences']),batch_size)];merged={'sentences':[],'findings':[],'coverage_complete':True,'summary':''};summaries=[]
                if not batches:fail('EMPTY_DOCUMENT','候选没有可分析的句子')
                for batch_index,batch in enumerate(batches):
                    scoped={**data,'sentences':batch,'batch_scope':{'index':batch_index,'total':len(batches),'scope_note':'只标注本批全部句子，不补写未给句子；整份覆盖由程序合并'}}
                    scoped_manifest={**manifest,'annotation_batch':batch_index,'annotation_sentence_ids':[x['id'] for x in batch]};context.save_context(st['id'],scoped,scoped_manifest,system,cfg)
                    response=provider_adapter.call(task,profile,system,scoped,scoped_manifest)
                    normalized,field_corrections=verification.normalize_empty_sentence_metadata(provider.parse_json(response['text']));annotation_field_corrections.extend(field_corrections)
                    normalized,book_corrections=verification.normalize_annotation_sources(normalized,scoped_manifest,scoped.get('references',[]));annotation_field_corrections.extend(book_corrections)
                    part=verification.Annotation.model_validate(normalized).model_dump()
                    permitted={x['id'] for x in batch}
                    if {x['sentence_id'] for x in part['sentences']}-permitted:fail('INVALID_MAPPING','标注超出本批句子范围')
                    merged['sentences'].extend(part['sentences']);merged['findings'].extend(part['findings']);merged['coverage_complete']=merged['coverage_complete'] and part['coverage_complete'];summaries.append(part['summary']);assert_lease(st['id'],epoch)
                merged['summary']='；'.join(summaries)[:4000];parsed=merged
            elif st['stage']=='coherence':
                parsed,manifest=coherence.run(task,profile,data,manifest,st['id'],lambda:assert_lease(st['id'],epoch))
            elif st['stage']=='regression':
                from .revision import regression
                parsed=regression(task,profile,data,manifest,st['id'],policy,lambda:assert_lease(st['id'],epoch))
            else:
                raw=provider_adapter.call(task,profile,system,data,manifest);parsed=provider.parse_json(raw['text'])
            assert_lease(st['id'],epoch)
            if st['stage']=='judgment':
                normalized,corrections=judgment.normalize_redundant_books(parsed,manifest,data.get('references',[]))
                out={'judgment':judgment.validate(normalized,manifest),'field_corrections':corrections}
            elif st['stage']=='judgment_check':
                from .models import Verification
                result=Verification.model_validate(parsed).model_dump();rid=verification.save_run(pid,None,'judgment_check',manifest,result);out={'verification_run_id':rid,'result':result}
            elif st['stage']=='draft':
                s=section(session['section_id'],pid);current=documents.current(s);locks=[r['node_id'] for r in db.rows('SELECT node_id FROM locks WHERE section_id=?',(s['id'],))];doc,validated=drafting.document(parsed,db.loads(current['document'],{}) if current else None,policy.get('target_node_ids'),locks,data.get('references',[]),manifest.get('provided_evidence_ids',manifest['evidence_versions']),pid=pid,context=data,manifest=manifest);identity=db.uid()
                db.execute('INSERT INTO writing_candidates VALUES(?,?,?,?,?,?,?,?)',(identity,session['id'],policy.get('revision_candidate_id'),blob(doc),digest(doc),blob({'judgment':data['judgment'],'manifest':manifest,'draft_field_corrections':validated['field_corrections'],'citation_proposals':validated.get('citation_proposals',[]),'evidence_quality_findings':validated.get('evidence_quality_findings',[])}),context_id,db.now()));out={'candidate_id':identity,'missing_materials':validated['missing_materials'],'field_corrections':validated['field_corrections']}
            elif st['stage']=='annotation':
                rid,result=verification.apply_annotation(pid,analysis_id,parsed,manifest);out={'analysis_id':analysis_id,'verification_run_id':rid,'result':result,'field_corrections':annotation_field_corrections}
            else:
                refs=sentences.get(pid,analysis_id)['sentences'];known={x['id'] for x in refs};nodes={x['node_id'] for x in refs}
                nodes.update(n['node_id'] for scope in parsed.get('expanded_original_passages',[]) for n in scope['paragraphs'])
                result=coherence.validate({k:parsed[k] for k in ('findings','coverage_complete','summary')},known,nodes)
                result.update({k:v for k,v in parsed.items() if k not in result});rid=verification.save_run(pid,analysis_id,st['stage'],manifest,result);out={'analysis_id':analysis_id,'verification_run_id':rid,'result':result}
        with db.transaction():
            assert_lease(st['id'],epoch);db.execute("UPDATE writing_steps SET state='completed',output_blob=?,error_json=NULL WHERE id=? AND lease_epoch=?",(blob(out),st['id'],epoch))
            if st['stage']=='judgment' and out['judgment']['blocking_gaps']:status='needs_evidence'
            elif st['stage']=='judgment_check' and (not out['result']['coverage_complete'] or any(f['severity']=='blocker' for f in out['result']['findings'])):status='needs_review'
            elif st['stage']=='regression' or (st['stage']=='coherence' and session['operation']!='revise_unit'):status='needs_review'
            else:status='queued';enqueue(session['id'],STAGES[STAGES.index(st['stage'])+1],st['iteration'])
            db.execute('UPDATE writing_sessions SET status=?,updated_at=? WHERE id=?',(status,db.now(),session['id']))
        return out
    except Exception as error:
        from ..summary_billing import BudgetExceeded
        status='cancelled' if isinstance(error,InterruptedError) else 'budget_blocked' if isinstance(error,BudgetExceeded) else 'outcome_unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'stale' if isinstance(error,HTTPException) and error.status_code==409 else 'failed'
        with db.transaction():
            fresh=db.one('SELECT lease_epoch FROM writing_steps WHERE id=?',(st['id'],))
            if fresh and fresh['lease_epoch']==epoch:
                db.execute('UPDATE writing_steps SET state=?,error_json=? WHERE id=?',(status,db.dumps({'message':security.redact(error),'status':status}),st['id']));db.execute('UPDATE writing_sessions SET status=?,updated_at=? WHERE id=?',(status,db.now(),session['id']))
        raise

@db.atomic
def resume(pid,identity,acknowledge_unknown=False):
    s=owned('session',identity,pid)
    if s['status']=='outcome_unknown' and not acknowledge_unknown:fail('REMOTE_OUTCOME_UNKNOWN','请核对账单/远端状态；新尝试可能重复计费',409)
    if s['status'] not in ('failed','budget_blocked','outcome_unknown'):fail('RESUME_NOT_ALLOWED','此状态不能直接重试；来源或任务变化请建立新会话',409)
    st=db.one("SELECT * FROM writing_steps WHERE session_id=? AND state!='completed' ORDER BY rowid DESC LIMIT 1",(identity,))
    if not st:fail('NOT_FOUND','没有可恢复步骤')
    if acknowledge_unknown:
        # A new task is explicit, but the old unknown request remains charged.
        db.execute("UPDATE writing_steps SET state='abandoned_unknown' WHERE id=?",(st['id'],));enqueue(identity,st['stage'],st['iteration']+1)
    else:
        db.execute("UPDATE writing_steps SET state='queued',error_json=NULL WHERE id=?",(st['id'],));db.execute("UPDATE tasks SET status='queued',error=NULL,cancel_requested=0 WHERE id=?",(st['task_id'],))
    db.execute("UPDATE writing_sessions SET status='queued',updated_at=? WHERE id=?",(db.now(),identity));return get(pid,identity)

@db.atomic
def cancel(pid,identity):
    owned('session',identity,pid);db.execute("UPDATE writing_sessions SET status='cancelled',updated_at=? WHERE id=?",(db.now(),identity))
    db.execute("UPDATE tasks SET cancel_requested=1 WHERE id IN (SELECT task_id FROM writing_steps WHERE session_id=?)",(identity,));return get(pid,identity)

def recover_task(task):
    from ..task_transitions import transition
    st=db.one('SELECT * FROM writing_steps WHERE task_id=?',(task['id'],))
    if not st:return
    if st['state']=='completed':
        transition(task['id'],task['attempt'],'running',status='succeeded',result=db.dumps(unblob(st['output_blob'])),lease_until=None)
        return
    unknown=db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('pending','sent','outcome_unknown') LIMIT 1",(task['id'],))
    status='outcome_unknown' if unknown else 'queued'
    with db.transaction():
        if transition(task['id'],task['attempt'],'running',status=status,lease_until=None):
            db.execute('UPDATE writing_steps SET state=? WHERE id=?',(status,st['id']))
            db.execute('UPDATE writing_sessions SET status=? WHERE id=?',(status,st['session_id']))

@db.atomic
def reparse_saved_draft(pid,identity):
    """Recover a known paid draft locally; never submit another remote request."""
    session=owned('session',identity,pid)
    if session['status']!='failed':fail('REPARSE_NOT_ALLOWED','只有确定失败且已有完整响应的正文阶段可以本地重解析',409)
    step=db.one("SELECT * FROM writing_steps WHERE session_id=? AND stage='draft' AND state='failed' ORDER BY rowid DESC LIMIT 1",(identity,))
    if not step:fail('REPARSE_NOT_ALLOWED','没有可重新解析的正文阶段',409)
    if db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('pending','sent','outcome_unknown')",(step['task_id'],)):fail('REMOTE_OUTCOME_UNKNOWN','仍有远端未知请求，不能重解析或重发',409)
    run=db.one("SELECT * FROM generation_runs WHERE task_id=? AND status='completed' ORDER BY rowid DESC LIMIT 1",(step['task_id'],))
    if not run:fail('REPARSE_NOT_ALLOWED','没有已保存的完整正文响应',409)
    meta=db.loads(run['manifest'],{});wire=unblob(meta['output_blob'])
    from .identifiers import decode
    parsed=decode(provider.parse_json(wire['text']),meta.get('identifier_aliases',{}))
    from .impact import check_manifest
    if check_manifest(pid,meta):fail('INPUT_VERSION_CHANGED','保存响应所依据的来源已变化',409)
    frozen=None
    for row in db.rows('SELECT * FROM writing_contexts WHERE step_id=? ORDER BY rowid DESC',(step['id'],)):
        content=unblob(row['payload_blob'])
        if 'evidence' in content and 'contract' in content:frozen=(row,content);break
    if not frozen:fail('REPARSE_NOT_ALLOWED','缺少该次生成的冻结正文输入',409)
    context_row,data=frozen;manifest=unblob(context_row['manifest_blob']);s=section(session['section_id'],pid);policy=db.loads(session['policy_json'],{})
    if s['current_revision_id']!=manifest.get('base_revision_id'):fail('INPUT_VERSION_CHANGED','当前正文已改变',409)
    current=documents.current(s);locks=[r['node_id'] for r in db.rows('SELECT node_id FROM locks WHERE section_id=?',(s['id'],))]
    doc,validated=drafting.document(parsed,db.loads(current['document'],{}) if current else None,policy.get('target_node_ids'),locks,data.get('references',[]),manifest.get('provided_evidence_ids',manifest.get('evidence_versions',{})),pid=pid,context=data,manifest=manifest);cid=db.uid()
    decision={'judgment':data['judgment'],'manifest':manifest,'draft_field_corrections':validated['field_corrections'],'citation_proposals':validated.get('citation_proposals',[]),'evidence_quality_findings':validated.get('evidence_quality_findings',[]),'reparsed_generation_run_id':run['id'],'reparse_method':'local_schema_reparse_no_new_model_call'}
    db.execute('INSERT INTO writing_candidates VALUES(?,?,?,?,?,?,?,?)',(cid,identity,policy.get('revision_candidate_id'),blob(doc),digest(doc),blob(decision),context_row['id'],db.now()))
    out={'candidate_id':cid,'missing_materials':validated['missing_materials'],'field_corrections':validated['field_corrections'],'reparsed_generation_run_id':run['id'],'new_model_calls':0}
    db.execute("UPDATE writing_steps SET state='completed',output_blob=?,error_json=NULL WHERE id=?",(blob(out),step['id']));db.execute("UPDATE tasks SET status='succeeded',result=?,error=NULL WHERE id=?",(db.dumps(out),step['task_id']))
    enqueue(identity,'annotation',step['iteration']);db.execute("UPDATE writing_sessions SET status='queued',updated_at=? WHERE id=?",(db.now(),identity));db.audit(pid,'writing_draft_reparsed',{'session_id':identity,'generation_run_id':run['id'],'candidate_id':cid,'new_model_calls':0})
    return get(pid,identity)

@db.atomic
def recheck_candidate(pid,cid):
    c=candidate(cid,pid);session=owned('session',c['session_id'],pid)
    if session['status'] not in ('failed','needs_review','completed'):fail('RECHECK_NOT_ALLOWED','执行中、来源过期或远端未知时不能另开核验绕过原状态',409)
    from .impact import check_manifest
    if check_manifest(pid,unblob(c['decision_blob'])['manifest']):fail('INPUT_VERSION_CHANGED','候选依据已变化，请按新任务建立候选',409)
    n=db.one('SELECT COALESCE(MAX(iteration),0)+1 n FROM writing_steps WHERE session_id=?',(session['id'],))['n']
    db.execute('INSERT INTO writing_steps VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),session['id'],'draft',n,digest([cid,n]),'completed',0,None,blob({'candidate_id':cid,'missing_materials':[],'scope':'reanalyze_frozen_candidate_no_regeneration'}),None));enqueue(session['id'],'annotation',n);db.execute("UPDATE writing_sessions SET status='queued' WHERE id=?",(session['id'],))
    return get(pid,session['id'])

@db.atomic
def reconfigure(pid,identity,body):
    s=owned('session',identity,pid)
    if s['status'] in ('running','queued','completed','cancelled','stale','outcome_unknown'):fail('RECONFIGURE_NOT_ALLOWED','先停止确定失败的阶段；远端未知不可通过换配置绕过',409)
    from .impact import check_manifest
    if check_manifest(pid,db.loads(s['base_versions_json'],{})):fail('INPUT_VERSION_CHANGED','来源已变化，请建立新会话',409)
    changes=body.get('stage_profiles',{});note=str(body.get('note','')).strip()
    if not isinstance(changes,dict) or not changes or set(changes)-set(STAGES) or len(note)<8:fail('INVALID_PROFILE_CHANGE','请明确需要更新的阶段配置及原因')
    for profile_id in changes.values():
        if not profiles.get(pid,profile_id)['configuration_current']:fail('PROFILE_STALE','替代配置仍已过期',409)
    policy=db.loads(s['policy_json'],{});policy['stage_profiles']={**policy.get('stage_profiles',{}),**changes};policy.setdefault('profile_changes',[]).append({'stages':changes,'note':note,'at':db.now(),'old_steps_and_costs_preserved':True})
    db.execute('UPDATE writing_sessions SET policy_json=? WHERE id=?',(db.dumps(policy),identity));db.audit(pid,'writing_stage_profile_changed',{'session_id':identity,'note':note,'changes':changes})
    return get(pid,identity)

@db.atomic
def approve_judgment(pid,identity,note):
    session=owned('session',identity,pid)
    if session['status']!='needs_review' or 'draft' in outputs(identity) or len(str(note).strip())<8:fail('REVIEW_REQUIRED','需明确核对判定及问题，填写至少8字依据')
    from .impact import check_manifest
    manifest=db.loads(session['base_versions_json'],{})
    if check_manifest(pid,manifest):fail('INPUT_VERSION_CHANGED','输入已变更',409)
    verification.save_run(pid,None,'judgment_human',manifest,{'note':note,'method':'human_reviewed','session_id':identity},'human_reviewed')
    enqueue(identity,'draft');db.execute("UPDATE writing_sessions SET status='queued',updated_at=? WHERE id=?",(db.now(),identity));return get(pid,identity)
