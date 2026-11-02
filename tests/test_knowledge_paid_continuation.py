from pathlib import Path
import runpy

import pytest

module=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/continue_knowledge_paid_testing.py'))


@pytest.mark.parametrize('state',['queued','running','incomplete','failed','cancelled','outcome_unknown','budget_blocked'])
def test_does_not_continue_incomplete_or_unknown_semantic_task(state):
    task={'status':state,'result':'{"phase":"semantic","result":{"complete":true}}'}
    assert not module['completion_allowed'](task)


def test_requires_actual_semantic_completion_result():
    check=module['completion_allowed']
    assert check({'status':'succeeded','result':'{"phase":"semantic","result":{"complete":true}}'})
    assert not check({'status':'succeeded','result':'{"phase":"drift","result":{"complete":true}}'})
    assert not check({'status':'succeeded','result':'{"phase":"semantic","result":{"complete":false}}'})


@pytest.mark.parametrize('state',['outcome_unknown','failed','cancelled','budget_blocked'])
def test_failed_maintenance_cannot_be_blindly_repeated(state):
    assert module['maintenance_outcome']({'status':state},{'result':{'completed_item_ids':['previous']}})=='attention_required'


def test_maintenance_only_advances_with_real_progress():
    decide=module['maintenance_outcome']
    assert decide({'status':'incomplete'},{'result':{'completed_item_ids':['one']}})=='advance'
    assert decide({'status':'incomplete'},{'result':{'completed_item_ids':[]}})=='attention_required'
    assert decide({'status':'succeeded'},{'result':{'complete':True}})=='complete'
    assert decide({'no_available_items':True},{})=='drained'


def test_all_following_phases_retain_human_resume_evidence_and_same_cap_scope():
    for phase in ('maintenance','index','drift'):
        args=module['phase_arguments']({'payload':'{"paid_testing_resume_id":"explicit-evidence"}'},'original-allowance',Path('results'),phase)
        assert args.allocation=='original-allowance' and args.resume_authorization=='explicit-evidence'
        assert args.stream and args.phase==phase
