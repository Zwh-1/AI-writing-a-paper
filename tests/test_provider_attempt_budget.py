"""One request owns at most five attempts across pools and outer retries."""
import json

import httpx
import pytest

from test_workbench import client, project
from test_deepseek_pool import configure_pool, pool_endpoint
from app import db, provider, provider_stream, tasks


def success(streaming):
    choice={'message':{'content':'{"ok":true}'},'delta':{'content':'{"ok":true}'},'finish_reason':'stop'}
    usage={'prompt_tokens':7,'completion_tokens':5}
    if streaming:
        return httpx.Response(200,headers={'content-type':'text/event-stream'},
            content=('data: '+json.dumps({'id':'fixture','choices':[choice],'usage':usage})+'\n\ndata: [DONE]\n\n').encode())
    return httpx.Response(200,json={'id':'fixture','choices':[choice],'usage':usage})


def setup_pool(client, streaming, retries):
    pid=project(client)
    endpoints=[pool_endpoint('one','one','https://one.example/v1',streaming=streaming,priority=1),
               pool_endpoint('two','two','https://two.example/v1',streaming=streaming,priority=2),
               pool_endpoint('three','three','https://three.example/v1',streaming=streaming,priority=3)]
    configure_pool(client,pid,endpoints,{'mode':'priority','retry_same_endpoint':retries,
                                        'circuit_breaker':{'enabled':False}})
    return pid


@pytest.mark.parametrize('streaming',[False,True])
@pytest.mark.parametrize('succeed_on_fifth',[False,True])
def test_pool_and_outer_retry_share_five_total_attempts(client,monkeypatch,streaming,succeed_on_fifth):
    pid=setup_pool(client,streaming,1)
    calls=[]
    def handle(request):
        calls.append(request.url.host)
        if succeed_on_fifth and len(calls)==5:return success(streaming)
        return httpx.Response(429,json={'error':'rate limit'},headers={'retry-after':'0'})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    # The request wrapper and pool both have a retry budget. They must consume
    # the same allowance, including pool switches and metered outer recursion.
    task=tasks.enqueue(pid,'provider_probe',{})
    if succeed_on_fifth:
        result=tasks.model_call(task,'Return JSON.','input',{})
        assert result['text']=='{"ok":true}'
    else:
        with pytest.raises(provider.ProviderError) as captured:
            tasks.model_call(task,'Return JSON.','input',{})
        assert captured.value.attempts_used==5
        assert captured.value.attempt_limit==5
        assert captured.value.attempt_budget_exhausted
    assert calls==['one.example','one.example','two.example','two.example','three.example']
    assert provider.attempts_used()==0 and provider.attempts_remaining()==5


@pytest.mark.parametrize('streaming',[False,True])
def test_standalone_pool_enforces_five_even_with_ten_retries_per_endpoint(client,monkeypatch,streaming):
    setup_pool(client,streaming,10)
    calls=[]
    def handle(request):
        calls.append(request.url.host)
        return httpx.Response(429,json={'error':'rate limit'})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    call=provider_stream.pool_generate if streaming else provider.pool_generate
    with pytest.raises(provider.ProviderError) as captured:call('Return JSON.','input')
    assert len(calls)==5 and captured.value.attempts_used==5


@pytest.mark.parametrize('streaming',[False,True])
def test_explicit_one_attempt_is_not_expanded_by_pool_configuration(client,monkeypatch,streaming):
    setup_pool(client,streaming,4)
    saved=db.setting('provider');saved['max_attempts']=1;db.set_setting('provider',saved)
    calls=[]
    def handle(request):
        calls.append(request.url.host)
        return httpx.Response(429,json={'error':'rate limit'})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    call=provider_stream.pool_generate if streaming else provider.pool_generate
    with pytest.raises(provider.ProviderError) as captured:call('Return JSON.','input')
    assert len(calls)==1 and captured.value.attempt_limit==1


def test_nested_scopes_never_refill_an_existing_allowance():
    with provider.attempt_budget(5):
        with provider._model_attempt():pass
        with provider.attempt_budget(5):
            assert provider.attempts_used()==1 and provider.attempts_remaining()==4
            with provider._model_attempt():
                with provider._model_attempt():pass
        assert provider.attempts_used()==2 and provider.attempts_remaining()==3
    assert provider.attempts_used()==0 and provider.attempts_remaining()==5


@pytest.mark.parametrize('streaming',[False,True])
@pytest.mark.parametrize('unknown',[False,True])
def test_unknown_or_partial_output_is_not_replayed_under_shared_budget(client,monkeypatch,streaming,unknown):
    setup_pool(client,streaming,4)
    calls=[]
    def rejected(*args,**kwargs):
        calls.append(1)
        raise provider.ProviderError('saved uncertain response',unknown=unknown,
             response_result=None if unknown else {'text':'partial'},retryable=True)
    if streaming:
        monkeypatch.setattr(provider_stream,'_stream_generate_once',rejected)
        call=provider_stream.pool_generate
    else:
        monkeypatch.setattr(provider,'_pool_generate_once',rejected)
        call=provider.pool_generate
    with pytest.raises(provider.ProviderError):call('Return JSON.','input')
    assert len(calls)==1
