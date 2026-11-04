"""Unavailable optional editing preserves only a fully checked valid candidate."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project,response  # noqa: F401
from test_writing_flow_safety import _running,_basis
from app import db,provider,security,tasks,writing_flow,expense_evidence
from app.summary_billing import BudgetExceeded


def scenario(env,monkeypatch,failure,*,critical=False,complete=True):
    writing_flow.save_settings(env['pid'],{'max_revision_rounds':2,'originality_check':False})
    task=_running(env);section=security.require_section(env['sid']);context,manifest=_basis(env)
    candidate=tasks.candidate_from_text(db.dumps({'paragraphs':[{'text':'模块职责清晰有助于维护。',
        'source_ids':[context['sources'][0]['id']]}],'missing_materials':['真实运行记录仍待补证']}))[0]
    if critical:candidate['paragraphs'][0]['claim_spans']=[{'local_id':'c1','text':'不在正文中的主张。'}]
    before_context,before_manifest=deepcopy(context),deepcopy(manifest)
    calls={'semantic':0,'revision':0};unknown_id='unresolved-existing-run';blocked=[]
    original_preflight=expense_evidence.preflight
    def preflight(*args,**kwargs):
        if failure=='budget' and calls['semantic']:
            blocked.append(1);raise BudgetExceeded('项目仍有费用未核实请求，禁止新模型请求')
        return original_preflight(*args,**kwargs)
    monkeypatch.setattr(expense_evidence,'preflight',preflight)
    def transport(system,user,images=None):
        payload=db.loads(user)
        if 'claim_reviews' in system:
            calls['semantic']+=1
            if failure=='budget':
                db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?)',(unknown_id,task['id'],task['project_id'],'outcome_unknown','fixture','openai',
                    db.dumps({'section_id':section['id'],'writing_stage':'evidence_revision','writing_unit_index':9}),.546764,db.now()))
            return response({'claim_reviews':[{'claim_id':claim['claim_id'],'support':'unsupported',
                'source_quotes':[],'reason':'未解决的来源支持缺口'} for claim in payload['claims']],
                'point_reviews':[],'findings':[]})
        calls['revision']+=1
        if failure in ('unknown','revoke_after_unknown','cancel_after_unknown'):
            if failure=='revoke_after_unknown':db.execute("UPDATE files SET egress='local_only' WHERE id=?",(env['file']['id'],))
            if failure=='cancel_after_unknown':db.execute('UPDATE tasks SET cancel_requested=1 WHERE id=?',(task['id'],))
            raise provider.ProviderError('synthetic service_unavailable; execution unknown',unknown=True)
        if failure=='partial':raise provider.ProviderError('synthetic partial revision',response_result=response({'paragraphs':[{'text':'partial'}]}))
        if failure=='cancel':raise InterruptedError('cancelled')
        if failure=='permission':raise HTTPException(403,'source permission revoked')
        if failure=='source_version':raise HTTPException(409,'source version changed')
        raise AssertionError('unexpected paid revision')
    monkeypatch.setattr(provider,'generate',transport)
    if not complete:
        original_semantic=writing_flow._semantic
        def incomplete(*args,**kwargs):
            result=original_semantic(*args,**kwargs);result['coverage_complete']=False;return result
        monkeypatch.setattr(writing_flow,'_semantic',incomplete)
    def run():return writing_flow.finish(task,section,candidate,'只依据原文',context,manifest,tasks.candidate_from_text)
    return task,candidate,context,manifest,before_context,before_manifest,calls,blocked,run


@pytest.mark.parametrize('failure',['unknown','partial','budget'])
def test_optional_failure_preserves_valid_checked_candidate_and_all_cost_reservations(writing_project,monkeypatch,failure):
    task,candidate,_,_,before_context,before_manifest,calls,blocked,run=scenario(writing_project,monkeypatch,failure)
    current,context,manifest,report,bundle=run()
    assert current['paragraphs']==candidate['paragraphs'] and current['missing_materials']==candidate['missing_materials']
    assert context==before_context and bundle is None
    assert all(manifest[key]==value for key,value in before_manifest.items())
    assert calls=={'semantic':1,'revision':0 if failure=='budget' else 1}
    assert report['status']=='needs_review' and report['human_review']=='pending'
    assert report['semantic']['coverage_complete'] and report['semantic']['claims'][0]['support']=='unsupported'
    assert any(f['kind']=='source_support_unsupported' for f in report['findings'])
    finding=next(f for f in report['findings'] if f['kind']=='revision_unavailable')
    round_=report['repair_rounds'][0]
    assert not round_['accepted'] and round_['rejection_stage']=='optional_revision_unavailable'
    assert finding['human_review']==finding['semantic_review']=='pending'
    assert finding['cost_reservations_unchanged'] and not finding['external_retry_performed']
    rows=db.rows('SELECT id,status,reserved,cost FROM generation_runs WHERE task_id=? ORDER BY rowid',(task['id'],))
    assert len(rows)==2
    if failure=='budget':
        assert blocked==[1] and finding['run_id'] is None and finding['budget_reason']
        unknown=next(r for r in rows if r['id']=='unresolved-existing-run')
        assert unknown['status']=='outcome_unknown' and unknown['cost'] is None and unknown['reserved']==.546764
        assert unknown['id'] in finding['unresolved_run_ids']
    else:
        assert finding['run_id']==rows[1]['id']
        assert rows[1]['status']==('outcome_unknown' if failure=='unknown' else 'completed_invalid')
        assert finding['unknown_outcome']==(failure=='unknown')
        assert finding['partial_response']==(failure=='partial')
        if failure=='unknown':assert rows[1]['cost'] is None and rows[1]['id'] in finding['unresolved_run_ids']
        else:assert finding['output_blob'] and rows[1]['cost']==.00001


@pytest.mark.parametrize('critical,complete',[(True,True),(False,False)])
def test_unchecked_or_locally_invalid_original_still_raises(writing_project,monkeypatch,critical,complete):
    *_,run=scenario(writing_project,monkeypatch,'unknown',critical=critical,complete=complete)
    with pytest.raises(provider.ProviderError):run()


@pytest.mark.parametrize('failure,error',[('cancel',InterruptedError),('permission',HTTPException),
    ('source_version',HTTPException),('revoke_after_unknown',ValueError),('cancel_after_unknown',InterruptedError)])
def test_cancellation_and_source_changes_are_never_softened(writing_project,monkeypatch,failure,error):
    *_,run=scenario(writing_project,monkeypatch,failure)
    with pytest.raises(error):run()
