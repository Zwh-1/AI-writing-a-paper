"""Live completion evidence and a human annotation packet, never fake acceptance."""
from fastapi import HTTPException
from . import db,security,knowledge_lineage as lineage

@db.read_session()
def semantic_compilation_status(pid):
    security.require_project(pid)
    row=db.one("SELECT id,detail FROM audit WHERE project_id=? AND action='knowledge_full_semantic_compilation_verified' ORDER BY id DESC LIMIT 1",(pid,))
    if not row:return {'complete':False,'file_count':0,'model_processed_ranges':0,'expected_ranges':0,'verified_at':None,'human_quality_accepted':False}
    record=db.loads(row['detail'],{});current=True
    from .material_policy import research_sql
    required={r['id'] for r in db.rows('SELECT f.id FROM files f WHERE f.project_id=? AND f.active=1 AND '+research_sql(),(pid,))}
    if set(record.get('source_versions',{}))!=required:current=False
    try:
        with lineage.validation_scope():
            for fid,stamp in record.get('source_versions',{}).items():
                if lineage.file_stamp(pid,fid)[2]!=stamp:current=False
            lineage.validate_refs(pid,record.get('wiki_refs',[]))
    except HTTPException:current=False
    return {'complete':bool(current and required and record.get('model_processed_ranges')==record.get('expected_ranges') and record.get('expected_ranges',0)>0),
        'file_count':record.get('file_count',0),'model_processed_ranges':record.get('model_processed_ranges',0),'expected_ranges':record.get('expected_ranges',0),
        'verified_at':record.get('verified_at'),'record_id':row['id'],'task_id':record.get('task_id'),'sources_and_pages_current':current,'human_quality_accepted':False}


def real_paper_status(pid):
    security.require_project(pid)
    versions=db.rows('SELECT r.id,r.section_id,r.text,r.document,s.title,s.kind,s.position FROM revisions r JOIN sections s ON s.current_revision_id=r.id WHERE s.project_id=? AND length(trim(r.text))>0 ORDER BY s.id',(pid,))
    citations=db.rows('SELECT c.* FROM citations c JOIN sections s ON s.current_revision_id=c.revision_id WHERE s.project_id=? ORDER BY c.id',(pid,))
    has_target=bool(versions and citations)
    content={'versions':[{k:r[k] for k in ('id','section_id','title','kind','position')}|{'text_hash':lineage.digest(r['text']),'document_hash':lineage.digest(r['document'])} for r in versions],
             'citations':citations}
    if has_target:
        content['project']=db.one('SELECT paper_type,brief,rule_package_id FROM projects WHERE id=?',(pid,))
        content['facts']=db.rows('SELECT * FROM facts WHERE project_id=? ORDER BY id',(pid,))
        content['bibliography']=db.rows('SELECT id,metadata,file_id,verification,revision FROM bibliography WHERE project_id=? ORDER BY id',(pid,))
        content['files']=db.rows('SELECT id,sha256,role,egress,active FROM files WHERE project_id=? ORDER BY id',(pid,))
        content['sources']=[{'id':r['id'],'file_id':r['file_id'],'hash':lineage.digest([r['text'],r['locator'],r['verified']])}
            for r in db.rows('SELECT s.id,s.file_id,s.text,s.locator,s.verified FROM sources s JOIN files f ON f.id=s.file_id WHERE f.project_id=? ORDER BY s.id',(pid,))]
    snapshot=lineage.digest(content)
    row=db.one("SELECT id,detail,created_at FROM audit WHERE project_id=? AND action='knowledge_real_paper_acceptance' ORDER BY id DESC LIMIT 1",(pid,))
    record=db.loads(row['detail'],{}) if row else {}
    accepted=bool(has_target and record.get('snapshot')==snapshot and record.get('result')=='accepted'
        and record.get('compared_original') is True and record.get('real_paper_confirmed') is True and record.get('reviewer'))
    return {'nonempty_current_revisions':len(versions),'current_citations':len(citations),'has_acceptance_target':has_target,
        'snapshot':snapshot,'human_quality_accepted':accepted,'review_record_id':row['id'] if row else None,
        'review_snapshot_current':bool(row and record.get('snapshot')==snapshot),
        'review_origin':'human_declared_not_independently_authenticated' if row else None,
        'note':'真实正文和引用版本必须存在；人工声明按内容快照留存，正文、引用或来源变化后需重新验收。'}


@db.atomic
def review_real_paper(pid,body):
    state=real_paper_status(pid)
    if not state['has_acceptance_target']:lineage.fail('REAL_PAPER_TARGET_REQUIRED','项目尚无非空正文及实际引用，不能登记真实论文效果验收',422)
    if body.get('expected_snapshot')!=state['snapshot']:lineage.fail('REAL_PAPER_CHANGED','正文、引用或来源已变化，请重新对照原件')
    if (not isinstance(body.get('reviewer'),str) or not body['reviewer'].strip() or not isinstance(body.get('note'),str) or not body['note'].strip()
            or body.get('compared_original') is not True or body.get('real_paper_confirmed') is not True or body.get('result') not in ('accepted','needs_revision')):
        lineage.fail('REAL_PAPER_REVIEW_REQUIRED','请由实际评审者确认真实正文、对照原件，并记录具体结论和说明',422)
    db.audit(pid,'knowledge_real_paper_acceptance',{'snapshot':state['snapshot'],'reviewer':body['reviewer'].strip(),'note':body['note'].strip(),
        'compared_original':True,'real_paper_confirmed':True,'result':body['result'],'origin':'human_declared_not_independently_authenticated'})
    return real_paper_status(pid)


def status(pid):
    security.require_project(pid)
    from .material_policy import research_sql
    files=db.rows('SELECT f.id,f.name FROM files f WHERE f.project_id=? AND f.active=1 AND '+research_sql(),(pid,))
    semantic=[]
    for f in files:
        units=db.one("SELECT COUNT(*) n FROM knowledge_units u JOIN knowledge_heads h ON h.parse_id=u.parse_id WHERE h.file_id=? AND u.review_state!='excluded'",(f['id'],))['n']
        rows=db.rows("SELECT DISTINCT o.unit_id FROM knowledge_observations o JOIN knowledge_dependencies d ON d.upstream_type='observation' AND d.upstream_id=o.id JOIN kg_assertions a ON a.claim_version_id=d.downstream_id AND d.downstream_type='assertion' WHERE o.project_id=? AND o.file_id=? AND a.method='llm' AND a.lifecycle NOT IN ('stale','superseded','excluded')",(pid,f['id']))
        semantic.append({**f,'current_units':units,'units_with_model_candidates':len(rows),'full_semantic_quality_verified':False})
    cases=db.rows('SELECT state,COUNT(*) n FROM knowledge_evaluation_cases WHERE project_id=? GROUP BY state',(pid,))
    from .expense_evidence import ledger
    expenses=ledger(pid)
    external=db.setting('knowledge_external_acceptance:'+pid,{'clean_machine':False,'long_term_load':False,'real_human_blind_review':False,'cloud_real_service':False,'provider_bill_authenticity':False})
    labelled=sum(c['n'] for c in cases if c['state']=='labelled')
    from .knowledge_comparisons import report
    verified=[]
    for comparison in db.rows("SELECT id FROM knowledge_comparisons WHERE project_id=? AND state!='running'",(pid,)):
        if report(pid,comparison['id'])['semantic_quality_verified']:verified.append(comparison['id'])
    paper=real_paper_status(pid);paper_accepted=paper['human_quality_accepted'];compiled=semantic_compilation_status(pid)
    all_complete=compiled['complete'] and labelled>=80 and bool(verified) and paper_accepted and expenses['gap_count']==0 and all(external.values())
    return {'project_id':pid,'software':{'automatic_routing':True,'argument_plans':True,'durable_maintenance':True,'wiki_conflict_merge':True,'support_adjudication':True,'evaluation_reports':True,'complete_transfer':True,'encrypted_cloud_protocol':True},
            'semantic_coverage':semantic,'semantic_compilation':compiled,'evaluation':cases,'comparisons':db.one('SELECT COUNT(*) n FROM knowledge_comparisons WHERE project_id=?',(pid,))['n'],
            'human_review_records':db.one('SELECT COUNT(*) n FROM knowledge_review_events WHERE project_id=?',(pid,))['n'],
            'verified_comparison_ids':verified,'real_paper_readiness':paper,
            'expense_evidence_gaps':expenses['gap_count'],'unknown_reserved_by_currency':{k:v['reservation'] for k,v in expenses['by_currency'].items()},
            'external_acceptance':external,
            'all_complete':all_complete,'note':'功能存在、模型候选、人工评审和真实环境验收分别记录，未实际完成的外部验收不会自动记为通过。'}

def annotation_packet(pid):
    security.require_project(pid);cases=db.rows('SELECT * FROM knowledge_evaluation_cases WHERE project_id=? ORDER BY category,id',(pid,))
    import re
    from . import material_policy,knowledge_units
    originals={r['name']:r for r in db.rows('SELECT * FROM files WHERE project_id=? AND active=1',(pid,))};result=[]
    for i,case in enumerate(cases):
        # These suggestions aid a real reviewer. They never change gold state.
        title=re.search('《([^》]+)》',case['question']);original=originals.get(title.group(1)) if title else None
        intent='guidance' if case['category']=='guidance' else 'research'
        candidate=None;gap='需要人工选择与任务相符的原件'
        if case['category']=='project':gap='项目任务需要代码定义、真实运行或项目记录，不能用文献替代项目执行证据'
        elif original and material_policy.source_context(original,intent)['allowed_for_intent']:
            rows=db.rows("SELECT u.* FROM knowledge_units u JOIN knowledge_heads h ON h.parse_id=u.parse_id WHERE h.file_id=? AND u.review_state!='excluded' ORDER BY u.ordinal",(original['id'],))
            usable=[r for r in rows if 150<=len(r['text'])<=12000] or rows
            source=usable[i%len(usable)] if usable else None
            if source:
                try:
                    item=knowledge_units.get(pid,source['id'],intent=intent)
                    if item['current']:
                        candidate={'unit_id':source['id'],'file_id':original['id'],'name':original['name'],'locator':item['locator'],
                            'text':source['text'][:12000],'version':source['content_hash'],'char_start':0,'char_end':min(12000,len(source['text'])),
                            'whole_unit_included':len(source['text'])<=12000,'semantic_relevance_verified':False};gap=None
                except HTTPException:gap='原件或解析版本已经变化，请重新定位'
        elif original:gap='任务类别与题目指定原件的已授权用途不一致，需要人工改写任务或重新选择原件'
        result.append({'id':case['id'],'category':case['category'],'question':case['question'],'state':case['state'],'gold':db.loads(case['gold_json'],{}),
                       'source_suggestion':candidate,'annotation_origin':'machine_navigation_only','human_reviewer':None,'human_passed':False,
                       'preparation_gap':gap,'real_task_confirmed':False,'compared_original':False,
                       'required_review':['将问题改写为真实写作任务','定位可接受的等价原文范围','登记必要条件与不可推出项','实际对照原件后填写核对人','隐去组合完成盲审后再揭示']})
    return {'format':'knowledge-human-annotation.v1','project_id':pid,'cases':result,'human_acceptance':False,'created_at':db.now()}
