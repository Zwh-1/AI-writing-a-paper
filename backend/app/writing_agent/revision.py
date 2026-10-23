from .. import db
from .common import owned,state,fail,candidate,unblob,blob,digest
from . import coordinator

@db.atomic
def revise(pid,session_id,body):
    old=owned('session',session_id,pid);head=state(pid);policy=db.loads(old['policy_json'],{});depth=policy.get('revision_depth',0)
    if depth>=policy.get('max_revision_rounds',2):fail('REVISION_LIMIT','自动修订轮数到限，请审阅已有版本',409)
    instructions=str(body.get('instructions','')).strip()
    if len(instructions)<4 or len(instructions)>6000:fail('INVALID_REVISION','请给出明确的定点修改问题')
    s=db.one('SELECT * FROM sections WHERE id=?',(old['section_id'],))
    previous=db.one('SELECT id FROM writing_candidates WHERE session_id=? ORDER BY created_at DESC LIMIT 1',(old['id'],))
    options={'max_revision_rounds':policy.get('max_revision_rounds',2),'budget_limit':policy.get('budget_limit'),'revision_instructions':instructions,'stage_profiles':body.get('stage_profiles',policy.get('stage_profiles',{}))}
    if not previous:fail('CANDIDATE_REQUIRED','修订需要已保存的候选',409)
    options['revision_candidate_id']=previous['id']
    result=coordinator.create(pid,{'section_id':s['id'],'operation':'revise_unit','base_revision_id':s['current_revision_id'],'expected_project_state_revision':head['revision'],'contract_version_id':head['contracts'][s['id']],'target_node_ids':body.get('target_node_ids',[]),'profile_id':body.get('profile_id',old['profile_id']),'policy':options})
    new_policy=result['policy'];new_policy['revision_depth']=depth+1;new_policy['revision_baseline_blob']=blob(baseline(pid,previous['id'],instructions));db.execute('UPDATE writing_sessions SET policy_json=? WHERE id=?',(db.dumps(new_policy),result['id']))
    return coordinator.get(pid,result['id'])

def baseline(pid,cid,instructions):
    from . import verification
    c=candidate(cid,pid);decision=unblob(c['decision_blob']);issues=[]
    analysis=db.one('SELECT id FROM document_analyses WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1',(cid,))
    if analysis:
        for run in verification.runs(pid,analysis['id']):
            for index,finding in enumerate(run['result'].get('findings',[])):
                issues.append({'issue_id':run['id']+':'+str(index),'stage':run['stage'],'finding':finding})
    issues.append({'issue_id':'requested','stage':'author_request','finding':{'message':instructions}})
    return {'candidate_id':cid,'text_hash':c['text_hash'],'original_document':unblob(c['document_blob']),'claims':decision['judgment']['claims'],'issues':issues,'evidence_versions':decision['manifest'].get('evidence_versions',{}),'claim_status':'Previously supported or proposed claims must be checked against evidence; do not preserve a demonstrably wrong claim.'}

def regression(task,profile,data,manifest,step_id,policy,assert_active=lambda:None):
    """Compare frozen old/new text independently of the editor's self-evaluation."""
    from pydantic import Field
    from typing import Literal
    from .models import Strict,Finding
    from . import provider_adapter,context,profiles,verification,prompts
    from .. import provider
    class ClaimCheck(Strict):
        local_id:str
        status:Literal['preserved','corrected_with_evidence','lost','strengthened','unknown']
        evidence_ids:list[str]=Field(default_factory=list)
        reason:str
    class IssueCheck(Strict):
        issue_id:str
        status:Literal['fixed','still_present','not_applicable','unknown']
        reason:str
    class Result(Strict):
        claim_checks:list[ClaimCheck]=Field(default_factory=list)
        issue_checks:list[IssueCheck]=Field(default_factory=list)
        findings:list[Finding]=Field(default_factory=list)
        coverage_complete:bool=False
        summary:str=''
    frozen=unblob(policy['revision_baseline_blob']);checks=[];issues=[];findings=[];complete=True
    _,cfg,_=profiles.for_session(task['project_id'],profile['id'])
    system=prompts.BOUNDARY+'这是独立修订回归检查，不是润色。对照original_document、candidate_document和原始证据，逐项返回claim_checks[{local_id,status:preserved|corrected_with_evidence|lost|strengthened|unknown,evidence_ids:[],reason}]、issue_checks[{issue_id,status:fixed|still_present|not_applicable|unknown,reason}]、findings、coverage_complete、summary。不能因为写作者说已修复就判通过。原命题错误可凭证据纠正，不能为通顺删条件或放大承诺；确认原审稿意见错误时可标not_applicable并说明证据。只检查本批给定项目，缺项不算通过。'
    batches=max(1,(len(frozen['claims'])+3)//4,(len(frozen['issues'])+3)//4)
    for index in range(batches):
        assert_active();claims=frozen['claims'][index*4:index*4+4];scope_issues=frozen['issues'][index*4:index*4+4]
        payload={key:data[key] for key in ('candidate_document','evidence','rules','thesis_core','contract') if key in data}
        payload.update(original_document=frozen['original_document'],claim_baseline=claims,prior_issues=scope_issues,baseline_hash=frozen['text_hash'])
        scoped={**manifest,'regression_batch':index,'revision_baseline_hash':frozen['text_hash']};context.save_context(step_id,payload,scoped,system,cfg)
        response=provider_adapter.call(task,profile,system,payload,scoped);result=Result.model_validate(provider.parse_json(response['text'])).model_dump()
        expected={c['local_id'] for c in claims};actual=[r['local_id'] for r in result['claim_checks']]
        issue_ids={i['issue_id'] for i in scope_issues};actual_issues=[r['issue_id'] for r in result['issue_checks']]
        if set(actual)-expected or len(actual)!=len(set(actual)) or set(actual_issues)-issue_ids or len(actual_issues)!=len(set(actual_issues)):fail('INVALID_REGRESSION','回归检查引用了范围外或重复项目')
        if set(actual)!=expected or set(actual_issues)!=issue_ids:complete=False;findings.append({'severity':'blocker','kind':'regression_missing','message':'原命题或原问题没有全部核对'})
        for item in result['claim_checks']:
            if set(item['evidence_ids'])-set(manifest.get('evidence_versions',{})):fail('INVALID_EVIDENCE','回归检查引用了未提供证据')
            if item['status'] in ('lost','strengthened','unknown') or (item['status']=='corrected_with_evidence' and not item['evidence_ids']):
                findings.append({'severity':'blocker','kind':'regression_'+item['status'],'message':item['local_id']+'：'+item['reason']});complete=False
        for item in result['issue_checks']:
            if item['status'] in ('still_present','unknown'):findings.append({'severity':'blocker','kind':'revision_issue_unresolved','message':item['issue_id']+'：'+item['reason']});complete=False
        complete=complete and result['coverage_complete'];checks.extend(result['claim_checks']);issues.extend(result['issue_checks']);findings.extend(result['findings'])
    return {'claim_checks':checks,'issue_checks':issues,'findings':findings,'coverage_complete':complete,'summary':'逐项对照旧正确命题、原问题和新正文；未知或未修复项继续阻断。','baseline_candidate_id':frozen['candidate_id'],'baseline_text_hash':frozen['text_hash'],'method':'model_assisted'}
