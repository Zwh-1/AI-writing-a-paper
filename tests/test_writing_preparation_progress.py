from contextlib import nullcontext

import pytest

from test_writing_agent import env
from test_workbench import client, project
from app import db, knowledge_service, tasks, writing_live, writing_retrieval, writing_flow


def test_cancellation_after_project_preparation_prevents_literature_and_model_calls(env, monkeypatch):
    prepare = writing_live.prepare

    def cancel_after_prepare(pid, section, query, task_id):
        result = prepare(pid, section, query, task_id)
        db.execute('UPDATE tasks SET cancel_requested=1 WHERE id=?', (task_id,))
        return result

    def unexpected_literature(*args, **kwargs):
        pytest.fail('Cancelled preparation must stop before reading literature')

    monkeypatch.setattr(writing_live, 'prepare', cancel_after_prepare)
    monkeypatch.setattr(knowledge_service, 'prepare_writing', unexpected_literature)
    task = tasks.enqueue(env['pid'], 'write', {
        'section_id': env['sid'], 'base_revision_id': None, 'operation': 'generate',
    })
    assert tasks.run_one()
    saved = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert saved['status'] == 'cancelled'
    assert not db.rows('SELECT id FROM generation_runs WHERE task_id=?', (task['id'],))


def test_final_request_is_checked_again_after_catalogue_alignment(env, monkeypatch):
    monkeypatch.setattr(knowledge_service, 'prepare_writing', lambda *args, **kwargs: {
        'sources': [], 'knowledge_units': [], 'selected_unit_ids': [],
        'gaps': [], 'document_readings': [],
    })
    restrict = writing_retrieval.restrict

    def enlarged_catalogue(bundle, context):
        result = restrict(bundle, context)
        result['gaps'].append({'reason': 'x' * 1_000_000})
        return result

    monkeypatch.setattr(writing_retrieval, 'restrict', enlarged_catalogue)
    task = tasks.enqueue(env['pid'], 'write', {
        'section_id': env['sid'], 'base_revision_id': None, 'operation': 'generate',
    })
    assert tasks.run_one()
    saved = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert saved['status'] == 'failed' and '实际写作请求' in saved['error']
    report = db.loads(saved['result'])['context_budget']
    assert report['input_upper_bound'] + report['reserved_output'] > report['context_limit']
    assert report['component_bytes']['evidence_bundle'] > 1_000_000
    assert not db.rows('SELECT id FROM generation_runs WHERE task_id=?', (task['id'],))


def test_non_json_independent_review_remains_unverified_without_losing_candidate(client,monkeypatch):
    pid=project(client)
    monkeypatch.setattr(writing_flow, 'settings', lambda pid: {'semantic_review': True})
    monkeypatch.setattr(writing_flow, '_stage_fits', lambda *args: True)
    monkeypatch.setattr(writing_flow, '_cached_call',
                        lambda *args, **kwargs: {'text': '这是一段核验说明，未按约定返回JSON。', 'run_id': 'saved-review'})
    section={'id':'section','title':'绪论','kind':'introduction','goal':'核对依据','target_words':200}
    paragraph={'text':'实际论断','source_ids':['source']}
    context={'sources':[{'id':'source','text':'实际论断'}], 'writing_brief':{'points':[]}}
    local={'claim_checks':[{'claim_id':'claim','paragraph_index':0,'text':'实际论断',
                            'support':'supported'}], 'paragraphs':[]}
    result=writing_flow._semantic({'project_id':pid},section,[paragraph],context,{},local)
    assert result['status']=='incomplete' and result['coverage_complete'] is False
    assert result['claims']==[]
    assert result['findings'][0]['kind']=='verification_incomplete'
    assert result['findings'][0]['run_id']=='saved-review'


def test_revision_deduplicates_identical_reading_text_without_mutating_evidence():
    text='完整读取的研究条件与局限。'
    unit={'unit_id':'unit','text':text,'read_observations':[{
        'unit_id':'unit','text':text,'char_start':4,'char_end':4+len(text)}]}
    context={'rules':'完整强制规则','knowledge_units':[unit], 'document_readings':[{
        'reading_id':'reading','version':'v1','passages':[{'unit_id':'unit','text':text,
            'char_start':4,'char_end':4+len(text)}]}]}
    candidate={'paragraphs':[{'text':'待核查候选。','knowledge_unit_ids':['unit']}]}
    request=writing_flow._revision_request(context,candidate,[],1)
    assert request['knowledge_units'][0]['text']==text
    observed=request['knowledge_units'][0]['read_observations'][0]
    assert 'text' not in observed and observed['text_in_knowledge_unit']=='unit'
    assert observed['char_start']==4 and observed['char_end']==4+len(text)
    assert request['document_readings'][0]['passages'][0]['text_in_knowledge_unit']=='unit'
    assert unit['read_observations'][0]['text']==text
    assert context['document_readings'][0]['passages'][0]['text']==text
    assert request['rules']==context['rules'] and request['candidate_to_repair']==candidate


def test_revision_defers_only_unreferenced_automatic_documents(monkeypatch):
    monkeypatch.setattr(db,'setting',lambda key,default=None:{'auto-reading':'v1'})
    monkeypatch.setattr(writing_flow,'_stage_fits',lambda system,request,manifest:
        sum(len(row['text']) for row in request.get('knowledge_units',[]))<1000)
    context={'knowledge_units':[{'unit_id':'must','file_id':'explicit','text':'必须保留的完整原文。'},
                                {'unit_id':'auto','file_id':'automatic','text':'自动补读原文。'*500}],
             'sources':[], 'references':[], 'project_evidence':[],
             'document_readings':[{'reading_id':'auto-reading','file_id':'automatic','version':'v1'}],
             'rules':'完整规则'}
    candidate={'paragraphs':[{'text':'已关联原文的论断。','knowledge_unit_ids':['must']}]}
    manifest={'writing_state':{'project_id':'project'},'section_id':'section','knowledge_selection':['must']}
    request=writing_flow._revision_available_context(context,candidate,[],1,'system',manifest)
    assert [row['unit_id'] for row in request['knowledge_units']]==['must']
    assert len(context['knowledge_units'])==2
    assert not request['document_readings']
    assert request['rules']==context['rules'] and request['candidate_to_repair']==candidate
    assert request['evidence_gaps'][-1]['file_id']=='automatic'


def test_claim_bibliography_field_moves_losslessly_to_citation_intent():
    raw={'paragraphs':[{'text':'实际原句。','claim_spans':[{'local_id':'c1','text':'实际原句。',
         'reference_ids':['reference'],'knowledge_unit_ids':['unit']}]}]}
    candidate,original,error=tasks.candidate_from_text(db.dumps(raw))
    assert candidate is not None and not error
    claim=candidate['paragraphs'][0]['claim_spans'][0]
    assert claim['knowledge_unit_ids']==['unit'] and 'reference_ids' not in claim
    intent=candidate['paragraphs'][0]['citation_intents'][0]
    assert intent['reference_id']=='reference' and intent['claim_id']=='c1'
    assert intent['knowledge_unit_ids']==['unit'] and intent['kind']=='paraphrase'
    assert raw['paragraphs'][0]['claim_spans'][0]['reference_ids']==['reference']
    assert original==db.dumps(raw)


def test_unknown_claim_fields_are_not_silently_removed_by_field_translation():
    raw={'paragraphs':[{'text':'正文。','claim_spans':[{'local_id':'c1','text':'正文。',
          'reference_ids':['reference'],'unsupported_evidence_field':['invented']}]}]}
    candidate,_,error=tasks.candidate_from_text(db.dumps(raw))
    assert candidate is None and 'unsupported_evidence_field' in error


@pytest.mark.parametrize('phase_changes', [False, True])
def test_watchdog_recognizes_real_phase_changes_at_the_same_rounded_percentage(monkeypatch, phase_changes):
    class WorkerExit(BaseException):
        pass

    class StopAfterTwoChecks:
        steps = 0

        def wait(self, interval):
            self.steps += 1

        def is_set(self):
            return self.steps >= 3

    stop = StopAfterTwoChecks()
    clock = iter((100.0, 120.0, 120.0))
    audits = []

    def row(sql, params=()):
        if 'FROM tasks' in sql:
            return {'id': 'task', 'project_id': 'project', 'kind': 'write_paper',
                    'progress': 6, 'phase': str(stop.steps) if phase_changes else 'reading'}
        return {'at': None}

    def exit_worker(code):
        assert code == 3
        raise WorkerExit()

    monkeypatch.setattr(tasks, 'STOP', stop)
    monkeypatch.setattr(tasks, '_STALL', {'task': None, 'marker': None, 'since': 0.0})
    monkeypatch.setattr(tasks.time, 'time', lambda: next(clock))
    monkeypatch.setattr(db, 'setting', lambda *args: 10)
    monkeypatch.setattr(db, 'one', row)
    monkeypatch.setattr(db, 'execute', lambda *args: None)
    monkeypatch.setattr(db, 'audit', lambda *args: audits.append(args))
    monkeypatch.setattr(tasks, 'exit_immediately', exit_worker)
    with nullcontext() if phase_changes else pytest.raises(WorkerExit):
        tasks.watchdog(interval=0)
    assert bool(audits) is not phase_changes
