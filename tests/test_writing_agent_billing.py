from copy import deepcopy
from app.writing_agent.billing import reconcile

def sample():return {'model':'test','unit':'USD','requests':10,'input_tokens':100,'output_tokens':50,'amounts':{'cost':'1','actual_cost':'0.1','account_cost':'1'},'remaining':'10'}
def pair():
    before=sample();after=deepcopy(before);after.update(requests=11,input_tokens=130,output_tokens=60,amounts={'cost':'1.02','actual_cost':'0.102','account_cost':'1.02'},remaining='9.98')
    return before,after,{'input_tokens':30,'output_tokens':10,'raw':{}}

def test_exact_single_request_reconciles_conservative_account_amount():
    before,after,usage=pair();proof=reconcile(before,after,usage);assert proof['amount']==0.02 and proof['currency']=='USD'

def test_concurrent_requests_do_not_release_unknown_reservation():
    before,after,usage=pair();after['requests']=12;assert reconcile(before,after,usage) is None

def test_unmatched_tokens_do_not_release_unknown_reservation():
    before,after,usage=pair();after['input_tokens']=131;assert reconcile(before,after,usage) is None

def test_currency_or_missing_stat_do_not_release_unknown_reservation():
    before,after,usage=pair();after['unit']='CNY';assert reconcile(before,after,usage) is None;assert reconcile(None,after,usage) is None

def test_negative_or_infinite_costs_do_not_reconcile():
    before,after,usage=pair();after['amounts']['cost']='0.5';assert reconcile(before,after,usage) is None
    after['amounts']['cost']='Infinity';assert reconcile(before,after,usage) is None

def test_provider_explicitly_excluding_cached_input_is_matched():
    before,after,usage=pair();usage['raw']={'prompt_cache_hit_tokens':20};after['input_tokens']=110
    proof=reconcile(before,after,usage);assert proof and proof['usage_match']['input_counting']=='cache_excluded'

def test_exact_two_request_closed_interval_requires_count_and_token_totals():
    before,after,usage=pair();after['requests']=12
    assert reconcile(before,after,usage) is None
    proof=reconcile(before,after,usage,2);assert proof and proof['matched_requests']==2
