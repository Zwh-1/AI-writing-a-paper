"""Agent recovery is task-local, serialized and still checks current permissions."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading

import httpx
import pytest

from test_workbench import client, file
from test_agent_gpt_failover import configure, success
from app import db, provider, tasks
from app.writing_agent import provider_adapter


def test_two_threads_share_one_agent_stage_response_and_reservation(client,monkeypatch):
    _,profile,task=configure(client)
    entered=threading.Event();release=threading.Event();second_started=threading.Event();sent=[]
    def handle(wire):
        sent.append(wire);entered.set()
        assert release.wait(5)
        return success('openai')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    args=(task,profile,'Keep original evidence.',{'question':'Use only the original.'},{'stage':'draft'})
    def second():
        second_started.set()
        return provider_adapter.call(*args)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first=executor.submit(provider_adapter.call,*args)
        assert entered.wait(5)
        following=executor.submit(second)
        assert second_started.wait(5)
        try:
            with pytest.raises(TimeoutError):following.result(timeout=0.05)
            assert len(sent)==1
        finally:release.set()
        assert first.result(timeout=10)['text']==following.result(timeout=10)['text']
    assert len(sent)==1
    assert db.one('SELECT COUNT(*) AS count FROM generation_runs WHERE task_id=?',(task['id'],))['count']==1


def test_changed_agent_input_does_not_bypass_an_unknown_position(client,monkeypatch):
    _,profile,task=configure(client);sent=[]
    def handle(wire):sent.append(wire);raise httpx.ReadTimeout('remote outcome unknown',request=wire)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(provider.ProviderError) as initial:
        provider_adapter.call(task,profile,'Original system.',{'question':'Original question.'},{'stage':'draft'})
    assert initial.value.unknown and len(sent)==1
    with pytest.raises(provider.ProviderError) as changed:
        provider_adapter.call(task,profile,'Changed system.',{'question':'Changed question.'},{'stage':'draft'})
    assert changed.value.unknown and len(sent)==1
    assert db.one('SELECT COUNT(*) AS count FROM generation_runs WHERE task_id=?',(task['id'],))['count']==1


def test_agent_cached_response_cannot_hide_permission_revocation(client,monkeypatch):
    pid,profile,task=configure(client)
    original=file(client,pid,'只适用于当前给定范围，不能推出部署结果。')
    sent=[]
    def handle(wire):sent.append(wire);return success('openai')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    args=(task,profile,'Keep scope.',{'question':'Use the original.'},{'stage':'draft','file_ids':[original['id']]})
    provider_adapter.call(*args)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    with pytest.raises(ValueError,match='禁止外发'):provider_adapter.call(*args)
    assert len(sent)==1


def test_agent_waiter_observes_cancellation_before_reusing_the_response(client,monkeypatch):
    _,profile,task=configure(client)
    entered=threading.Event();release=threading.Event();sent=[]
    def handle(wire):
        sent.append(wire);entered.set()
        assert release.wait(5)
        return success('openai')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    args=(task,profile,'Use exact originals.',{'question':'Use the original.'},{'stage':'draft'})
    with ThreadPoolExecutor(max_workers=2) as executor:
        first=executor.submit(provider_adapter.call,*args)
        assert entered.wait(5)
        second=executor.submit(provider_adapter.call,*args)
        db.execute('UPDATE tasks SET cancel_requested=1 WHERE id=?',(task['id'],))
        release.set()
        first.result(timeout=10)
        with pytest.raises(InterruptedError):second.result(timeout=10)
    assert len(sent)==1


def test_agent_completed_partial_cannot_be_reissued_by_changing_its_frame(client,monkeypatch):
    _,profile,task=configure(client);sent=[]
    def handle(wire):
        sent.append(wire)
        return httpx.Response(200,json={'choices':[{'message':{'content':'saved partial'},'finish_reason':'length'}],
                                       'usage':{'prompt_tokens':10,'completion_tokens':20}})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(provider.ProviderError) as initial:
        provider_adapter.call(task,profile,'Original system.',{'question':'Original question.'},{'stage':'draft'})
    assert initial.value.response_result is not None
    with pytest.raises(provider.ProviderError):
        provider_adapter.call(task,profile,'Changed system.',{'question':'Changed question.'},{'stage':'draft'})
    assert len(sent)==1
    assert db.one('SELECT status FROM generation_runs WHERE task_id=?',(task['id'],))['status']=='completed_invalid'


@pytest.mark.parametrize('partial',[False,True])
def test_appending_history_cannot_skip_an_unresolved_agent_stage(client,monkeypatch,partial):
    _,profile,task=configure(client);sent=[]
    def handle(wire):
        sent.append(wire)
        if partial:
            return httpx.Response(200,json={'choices':[{'message':{'content':'saved partial'},'finish_reason':'length'}],
                                           'usage':{'prompt_tokens':10,'completion_tokens':20}})
        raise httpx.ReadTimeout('unknown prior turn',request=wire)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    messages=[{'role':'user','content':'Use the original context.'}]
    with pytest.raises(provider.ProviderError):
        provider_adapter.call(task,profile,'Keep tool history.',{},{'stage':'investigate'},messages=messages)
    with pytest.raises(provider.ProviderError):
        provider_adapter.call(task,profile,'Keep tool history.',{},{'stage':'investigate'},
            messages=[*messages,{'role':'user','content':'Continue the next turn.'}])
    assert len(sent)==1
