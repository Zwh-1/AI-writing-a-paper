import runpy
from pathlib import Path

import pytest

helpers=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_load_acceptance.py'))
summarize=helpers['summarize']


def samples_for_day():
    return [{'seconds':2,'finished_seconds':i*60+2,'question_index':i%80} for i in range(1440)]


def test_short_probe_cannot_claim_day_acceptance():
    report=summarize([{'seconds':2,'finished_seconds':2}],[],120,2,121,'completed')
    assert not report['long_term_24h_accepted']


def test_actual_full_continuous_day_is_required():
    report=summarize(samples_for_day(),[],86400,2,86401,'completed',True,80)
    assert report['long_term_24h_accepted'] and report['continuous_load_observed']
    assert report['scope']=='graph_wiki_plus_independent_neural_original_retrieval'
    assert report['distinct_questions_executed']==80
    assert not report['clean_machine_accepted']


@pytest.mark.parametrize('state,elapsed', [('running',86401),('cancelled',86401),('completed',86399)])
def test_requested_duration_and_incomplete_state_do_not_prove_acceptance(state,elapsed):
    assert not summarize(samples_for_day(),[],86400,2,elapsed,state)['long_term_24h_accepted']


def test_machine_sleep_or_missing_load_windows_invalidates_acceptance():
    data=[r for i,r in enumerate(samples_for_day()) if not 120<i<240]
    report=summarize(data,[],86400,2,86401,'completed')
    assert not report['long_term_24h_accepted'] and not report['continuous_load_observed']


def test_retrieval_errors_prevent_acceptance():
    assert not summarize(samples_for_day(),[{'error_type':'RuntimeError'}],86400,2,86401,'completed')['long_term_24h_accepted']
