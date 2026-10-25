"""All final mutations are inside one SQLite write transaction."""
from copy import deepcopy
from .. import db,documents,security
from .common import candidate,owned,section,blob,unblob,digest,fail,dependency
from . import sentences,verification
from .impact import check_manifest,retain_existing,resolve_covered

def receipt(pid,key,request_hash):
    if not isinstance(key,str) or not 8<=len(key)<=200:fail('IDEMPOTENCY_REQUIRED','采用需要8至200字符的幂等标识')
    old=db.one('SELECT * FROM writing_commit_receipts WHERE project_id=? AND idempotency_key=?',(pid,key))
    if not old:return None
    if old['request_hash']!=request_hash:fail('IDEMPOTENCY_CONFLICT','同一幂等标识不能用于不同请求',409)
    return {**unblob(old['result_blob']),'idempotent_replay':True}

def save_receipt(pid,key,request_hash,kind,identity,result):
    db.execute('INSERT INTO writing_commit_receipts VALUES(?,?,?,?,?,?,?,?)',(db.uid(),pid,key,request_hash,kind,identity,blob(result),db.now()))

def inspect(pid,cid,body):
    c=candidate(cid,pid);session=owned('session',c['session_id'],pid);s=section(session['section_id'],pid);decision=unblob(c['decision_blob']);manifest=decision['manifest']
    published=False
    for step in db.rows("SELECT output_blob FROM writing_steps WHERE session_id=? AND stage='draft' AND state='completed' AND output_blob IS NOT NULL",(session['id'],)):
        if unblob(step['output_blob']).get('candidate_id')==cid:published=True;break
    if not published:fail('CANDIDATE_NOT_COMMITTED','该候选尚未由有效执行阶段提交',409)
    if session['status'] in ('queued','running','outcome_unknown','cancelled'):fail('SESSION_NOT_READY','会话仍在执行、结果未知或已取消，不能采用',409)
    conflicts=check_manifest(pid,manifest)
    if s['current_revision_id']!=body.get('base_revision_id') or s['current_revision_id']!=manifest['base_revision_id']:conflicts.append('基础正文')
    if conflicts:fail('INPUT_VERSION_CHANGED','采用依据已变化',409,conflicts=conflicts)
    doc=unblob(c['document_blob'])
    if digest(doc)!=c['text_hash']:fail('INVALID_CANDIDATE','候选内容校验失败',409)
    analyses=db.rows('SELECT * FROM document_analyses WHERE candidate_id=? ORDER BY rowid DESC',(cid,));analysis=analyses[0] if analyses else None
    if analysis:
        manifest=deepcopy(manifest)
        for run in verification.runs(pid,analysis['id']):
            conflicts=check_manifest(pid,run['input_manifest'])
            if conflicts:fail('INPUT_VERSION_CHANGED','核验所用的扩展前提已变化',409,conflicts=conflicts)
            for field in ('related_revisions','evidence_versions','permission_stamps','fact_versions','reference_versions'):
                target=manifest.setdefault(field,{})
                for identity,version in run['input_manifest'].get(field,{}).items():
                    if identity in target and target[identity]!=version:fail('INPUT_VERSION_CHANGED','不同核验使用了不同输入版本',409)
                    target[identity]=version
    strict=body.get('accept_policy','draft')=='strict'
    if body.get('accept_policy','draft') not in ('draft','strict'):fail('INVALID_POLICY','采用策略无效')
    if strict:
        if not analysis:fail('VERIFICATION_UNRESOLVED','尚无句子核验结果',409)
        rr=verification.runs(pid,analysis['id']);human=[r for r in rr if r['stage']=='human_review' and r['status']=='completed']
        if not {'annotation','semantic','coherence'}.issubset({r['stage'] for r in rr if r['status']=='completed' and r['result'].get('coverage_complete')}):fail('VERIFICATION_UNRESOLVED','严格采用需要完整的句子、语义与衔接检查',409)
        if not human or human[-1]['result'].get('decision')!='approved' or human[-1]['result'].get('reviewed_text_hash')!=analysis['text_hash']:fail('VERIFICATION_UNRESOLVED','严格采用需要当前全文范围的明确人工核对',409)
        if analysis['state']!='human_reviewed':fail('VERIFICATION_UNRESOLVED','当前核验状态不满足严格采用',409)
        if c['parent_candidate_id'] and db.loads(session['policy_json'],{}).get('revision_candidate_id'):
            if not any(r['stage']=='regression' and r['status']=='completed' and r['result'].get('coverage_complete') for r in rr):fail('REGRESSION_REQUIRED','修订候选尚未通过旧/新命题与问题的独立回归',409)
    return c,session,s,doc,manifest,analysis

def merge_document(current,doc,body):
    placement=body.get('placement','replace_section');selected=body.get('selected_node_ids',[])
    if placement=='replace_section' and not selected:return deepcopy(doc)
    chosen=[n for n in doc.get('content',[]) if n.get('attrs',{}).get('nodeId') in selected]
    if not selected or len(chosen)!=len(set(selected)):fail('INVALID_SELECTION','请选择有效候选节点')
    old=(current or {}).get('content',[])
    if placement=='append':return {'type':'doc','content':deepcopy(old+chosen)}
    if placement=='replace_selected':
        ids=body.get('source_node_ids',[]);positions=[i for i,n in enumerate(old) if n.get('attrs',{}).get('nodeId') in ids]
        if not ids or len(positions)!=len(set(ids)) or positions!=list(range(min(positions),max(positions)+1)):fail('INVALID_SELECTION','被替换段落必须连续')
        if any(n['type']!='paragraph' for n in old[min(positions):max(positions)+1]):fail('INVALID_SELECTION','不能通过段落替换移除图表或标题')
        return {'type':'doc','content':deepcopy(old[:min(positions)]+chosen+old[max(positions)+1:])}
    fail('INVALID_PLACEMENT','放置方式无效')

def commit_document(pid,s,doc,manifest,body,analysis=None,kind='agent_accepted',task_id=None,citation_proposals=None):
    """The single mutation path for agent, legacy, partial and joint adoption."""
    current=documents.current(s);old_doc=db.loads(current['document'],{}) if current else None;merged=merge_document(old_doc,doc,body)
    partial=digest(merged)!=digest(doc)
    if partial and body.get('accept_policy')=='strict':fail('PARTIAL_REVIEW_REQUIRED','请先建立合并候选并复核后严格采用',409)
    retain_existing(pid,s['id'])
    result=documents.save(s,merged,body.get('base_revision_id'),kind=kind,task_id=task_id,citation_proposals=citation_proposals,clear_stale=False)
    fresh_analysis=sentences.analyze(pid,db.loads(result['document'],{}),revision_id=result['id'])
    if analysis:
        source=sentences.get(pid,analysis['id']);by_key={(r['node_id'],r['start_cp'],r['end_cp'],r['text_hash']):r for r in source['sentences']}
        sentence_ids={}
        for r in fresh_analysis['sentences']:
            old_span=by_key.get((r['node_id'],r['start_cp'],r['end_cp'],r['text_hash']))
            if not old_span:continue
            sentence_ids[old_span['id']]=r['id']
            db.execute('UPDATE sentence_spans SET role=? WHERE id=?',(old_span['role'],r['id']))
            for link in old_span['claims']:db.execute('INSERT INTO sentence_claim_links VALUES(?,?,?)',(r['id'],link['id'],link['role']))
        if not partial:
            db.execute('UPDATE document_analyses SET state=? WHERE id=?',(analysis['state'],fresh_analysis['id']))
            for run in verification.runs(pid,analysis['id']):
                copied=deepcopy(run['result']);copied['inherited_from_run']=run['id']
                for finding in copied.get('findings',[]):
                    if finding.get('sentence_id') in sentence_ids:finding['sentence_id']=sentence_ids[finding['sentence_id']]
                for item in copied.get('sentences',[]):
                    if item.get('sentence_id') in sentence_ids:item['sentence_id']=sentence_ids[item['sentence_id']]
                copied_manifest=deepcopy(run['input_manifest'])
                if s['id'] in copied_manifest.get('related_revisions',{}):copied_manifest['related_revisions'][s['id']]=result['id']
                verification.save_run(pid,fresh_analysis['id'],run['stage'],copied_manifest,copied,run['method'],run['status'])
        if not partial and analysis['state']=='human_reviewed':resolve_covered(pid,s['id'],manifest,fresh_analysis['id'])
    for eid,version in manifest.get('evidence_versions',{}).items():dependency(pid,'evidence',eid,version,'revision',result['id'],result['id'])
    for fid,version in manifest.get('fact_versions',{}).items():dependency(pid,'fact',fid,version,'revision',result['id'],result['id'])
    for chapter,version in manifest.get('related_revisions',{}).items():
        if chapter!=s['id']:dependency(pid,'revision',version,version,'revision',result['id'],result['id'])
    for field,up_type in (('core_version_id','core'),('contract_version_id','contract'),('target_snapshot_id','snapshot'),('rule_package_id','rule_package')):
        if manifest.get(field):dependency(pid,up_type,manifest[field],manifest[field],'revision',result['id'],result['id'])
    # Even an unverified legacy draft is explicitly tracked as such.
    dependency(pid,'writing_origin',task_id or fresh_analysis['id'],kind,'revision',result['id'],result['id'])
    return result,fresh_analysis,partial

@db.atomic
def adopt(pid,cid,body,key,_checked=None):
    request_hash=digest({'candidate':cid,'body':body})
    prior=receipt(pid,key,request_hash)
    if prior:return prior
    old=db.one('SELECT * FROM writing_adoptions WHERE project_id=? AND idempotency_key=?',(pid,key))
    if old:
        if old['request_hash']!=request_hash:fail('IDEMPOTENCY_CONFLICT','同一幂等标识不能用于不同请求',409)
        return {'adoption_id':old['id'],'revision_ids':db.loads(old['result_revision_ids_json'],[]),'idempotent_replay':True}
    c,session,s,doc,manifest,analysis=_checked or inspect(pid,cid,body)
    result,fresh_analysis,partial=commit_document(pid,s,doc,manifest,body,analysis)
    receipt_id=db.uid();db.execute('INSERT INTO writing_adoptions VALUES(?,?,?,?,?,?,?,?,?)',(receipt_id,pid,key,request_hash,cid,db.dumps(body.get('selected_node_ids',[])),db.dumps([result['id']]),blob({'manifest':manifest,'source_analysis_id':analysis['id'] if analysis else None,'new_analysis_id':fresh_analysis['id'],'partial':partial,'policy':body.get('accept_policy','draft')}),db.now()))
    db.execute("UPDATE writing_sessions SET status='completed',updated_at=? WHERE id=?",(db.now(),session['id']));db.audit(pid,'writing_adopted',{'candidate_id':cid,'revision_id':result['id'],'partial':partial,'policy':body.get('accept_policy','draft')})
    response={'adoption_id':receipt_id,'revision_ids':[result['id']],'analysis_id':fresh_analysis['id'],'partial':partial,'review_state':'needs_review' if partial else (analysis['state'] if analysis else 'unverified')}
    save_receipt(pid,key,request_hash,'agent',cid,response)
    return response

@db.atomic
def adopt_legacy(sid,body,key=''):
    """Adapt old candidates to the same transaction; legacy drafts cannot claim strict review."""
    s=security.require_section(sid);pid=s['project_id'];request_hash=digest({'section':sid,'body':body,'kind':'legacy'})
    key=key or 'legacy:'+request_hash
    prior=receipt(pid,key,request_hash)
    if prior:return prior
    policy=body.get('accept_policy','draft')
    if policy not in ('draft','strict'):fail('INVALID_POLICY','采用策略无效')
    if policy=='strict':fail('VERIFICATION_UNRESOLVED','旧候选没有当前逐句核验，请在有据写作中核验后严格采用',409)
    task=db.one("SELECT * FROM tasks WHERE id=? AND kind='write' AND status='succeeded' AND project_id=?",(body.get('task_id'),pid))
    if not task:fail('NOT_FOUND','候选任务不存在或未完成',404)
    output=db.loads(task['result'],{});manifest=output.get('manifest',{});p=security.require_project(pid)
    if output.get('section_id')!=sid or not output.get('document'):fail('INVALID_CANDIDATE','候选稿无有效结构')
    if s['kind']=='abstract_en' and not manifest.get('abstract_translation'):
        fail('ABSTRACT_TRANSLATION_REQUIRED','此英文摘要候选未记录中文摘要翻译来源，请重新翻译后采用',409)
    if manifest.get('abstract_translation'):
        from ..abstract_translation import check_source
        check_source(pid,manifest['abstract_translation'])
    if manifest.get('rule_package_id')!=p['rule_package_id']:fail('INPUT_VERSION_CHANGED','候选稿依据的规则已更新',409)
    for fid,version in manifest.get('fact_revisions',{}).items():
        fact=db.one('SELECT revision FROM facts WHERE id=? AND project_id=?',(fid,pid))
        if not fact or fact['revision']!=version:fail('INPUT_VERSION_CHANGED','候选依据的研究事实已变更',409)
    for fid in manifest.get('file_ids',[]):
        if not security.file_allows_remote(fid,pid):fail('SOURCE_ACCESS_REVOKED','候选所用资料外发权限已变化',409)
    if manifest.get('writing_state'):
        conflicts=check_manifest(pid,manifest['writing_state'])
        if conflicts:fail('INPUT_VERSION_CHANGED','候选的论文依据已变化',409,conflicts=conflicts)
    expected=output.get('base_revision_id',manifest.get('base_revision_id',body.get('base_revision_id')))
    if expected!=body.get('base_revision_id'):fail('INPUT_VERSION_CHANGED','候选基础正文不匹配',409)
    adapted={'base_revision_id':body.get('base_revision_id'),'accept_policy':'draft','placement':'replace_section'}
    if 'node_ids' in body:adapted.update(placement=body.get('mode','append'),selected_node_ids=body['node_ids'],source_node_ids=body.get('source_node_ids',[]))
    tracked={**manifest.get('writing_state',{}),'fact_versions':manifest.get('fact_revisions',{}),'rule_package_id':p['rule_package_id']}
    result,analysis,partial=commit_document(pid,s,output['document'],tracked,adapted,kind='accepted',task_id=task['id'],citation_proposals=output.get('citation_proposals',[]))
    for source in manifest.get('source_ids',[]):db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'source',source,source,sid,result['id']))
    for fid,version in manifest.get('fact_revisions',{}).items():db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'fact',fid,str(version),sid,result['id']))
    response={**result,'analysis_id':analysis['id'],'review_state':'unverified','accept_policy':'draft','partial':partial}
    save_receipt(pid,key,request_hash,'legacy',task['id'],response);db.audit(pid,'writing_adopted',{'task_id':task['id'],'revision_id':result['id'],'policy':'draft','unverified':True,'adapter':'legacy'})
    return response

@db.atomic
def adopt_many(pid,items,key,joint_review_id=None):
    if not isinstance(items,list) or not 1<=len(items)<=10:fail('INVALID_PATCH','联合补丁需要1至10项')
    previous=[]
    for i,item in enumerate(items):
        receipt=db.one('SELECT * FROM writing_adoptions WHERE project_id=? AND idempotency_key=?',(pid,key+':'+str(i)))
        if receipt:
            if receipt['request_hash']!=digest({'candidate':item['candidate_id'],'body':item['body']}):fail('IDEMPOTENCY_CONFLICT','联合请求幂等标识已用于其他内容',409)
            previous.append({'adoption_id':receipt['id'],'revision_ids':db.loads(receipt['result_revision_ids_json'],[]),'idempotent_replay':True})
    if previous:
        if len(previous)!=len(items):fail('IDEMPOTENCY_CONFLICT','联合采用回执不完整',409)
        return {'results':previous}
    checked=[inspect(pid,x['candidate_id'],x['body']) for x in items]
    if len({x[2]['id'] for x in checked})!=len(checked):fail('INVALID_PATCH','同一联合补丁不能重复修改一个章节')
    edited={x[2]['id'] for x in checked}
    joint_needed=any(item['body'].get('accept_policy')=='strict' and edited.intersection(entry[4].get('related_revisions',{})) for item,entry in zip(items,checked))
    if joint_needed:
        if not joint_review_id:fail('JOINT_REVIEW_REQUIRED','请先建立并复核全部联合候选',409)
        from .joint import require_approved
        require_approved(pid,joint_review_id,items)
    results=[adopt(pid,item['candidate_id'],item['body'],key+':'+str(i),_checked=checked[i]) for i,item in enumerate(items)]
    if joint_needed:
        from .joint import rebind_committed
        rebind_committed(pid,results,checked)
    return {'results':results,'joint_review_id':joint_review_id}

@db.atomic
def prepare_partial(pid,cid,body):
    c,session,s,doc,manifest,analysis=inspect(pid,cid,{**body,'accept_policy':'draft'});current=documents.current(s);merged=merge_document(db.loads(current['document'],{}) if current else None,doc,body)
    identity=db.uid();db.execute('INSERT INTO writing_candidates VALUES(?,?,?,?,?,?,?,?)',(identity,session['id'],cid,blob(merged),digest(merged),c['decision_blob'],c['context_id'],db.now()))
    iteration=db.one('SELECT COALESCE(MAX(iteration),0)+1 n FROM writing_steps WHERE session_id=?',(session['id'],))['n']
    db.execute('INSERT INTO writing_steps VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),session['id'],'draft',iteration,digest([identity,'merged']),'completed',0,None,blob({'candidate_id':identity,'missing_materials':[]}),None))
    from .coordinator import enqueue
    enqueue(session['id'],'annotation',iteration);db.execute("UPDATE writing_sessions SET status='queued' WHERE id=?",(session['id'],))
    return {'candidate_id':identity,'session_id':session['id'],'status':'queued_for_full_merged_review'}
