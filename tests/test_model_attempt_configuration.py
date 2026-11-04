"""Five total model attempts survive settings and per-stage configurations."""
import pytest

from test_workbench import client
from test_deepseek_pool import pool_endpoint
from app import db, deepseek_pool, material_summary, model_routing, provider
from app import rule_review_runtime
from app.writing_agent import profiles


def configure_primary(client, attempts=5):
    response=client.put('/api/v1/settings',json={'api_key':'fake-retry-config-key','provider':{
        'base_url':'https://retry.example/v1','model':'deepseek-retry','max_attempts':attempts}})
    assert response.status_code==200,response.text
    return response


def test_fresh_provider_defaults_offer_five_total_attempts(client):
    assert provider.DEFAULT['max_attempts']==5
    assert model_routing.DEFAULT['max_attempts']==5
    assert material_summary.DEFAULT['max_attempts']==5
    assert deepseek_pool.DEFAULT_STRATEGY['retry_same_endpoint']==4
    assert provider.config()['max_attempts']==5
    assert material_summary.config()['max_attempts']==5


@pytest.mark.parametrize('attempts',[0,6,1.5,True,'5'])
def test_primary_rejects_attempt_counts_outside_one_to_five_integers(client,attempts):
    configure_primary(client)
    response=client.put('/api/v1/settings',json={'provider':{'max_attempts':attempts}})
    assert response.status_code==422
    assert provider.config()['max_attempts']==5


def test_primary_accepts_five_attempts(client):
    assert configure_primary(client).json()['provider']['max_attempts']==5


@pytest.mark.parametrize('attempts',[0,6,1.5,True,'5'])
def test_gpt_rejects_invalid_attempt_counts(client,attempts):
    assert client.put('/api/v1/gpt-fallback/settings',json={'provider':{'max_attempts':5}}).status_code==200
    response=client.put('/api/v1/gpt-fallback/settings',json={'provider':{'max_attempts':attempts}})
    assert response.status_code==422
    assert model_routing.config()['max_attempts']==5


@pytest.mark.parametrize('attempts',[0,6,1.5,True,'5'])
def test_pool_rejects_invalid_endpoint_attempt_counts(client,attempts):
    endpoint=pool_endpoint('retry','重试接口','https://retry.example/v1',max_attempts=attempts)
    assert client.put('/api/v1/deepseek-pool/settings',json={'endpoints':[endpoint]}).status_code==422


def test_pool_and_unified_profiles_accept_five_attempts(client):
    configure_primary(client)
    response=client.put('/api/v1/deepseek-pool/settings',json={'endpoints':[
        pool_endpoint('retry','重试接口','https://retry.example/v1',max_attempts=5)]})
    assert response.status_code==200,response.text
    assert response.json()['endpoints'][0]['max_attempts']==5
    settings=client.get('/api/v1/deepseek/settings')
    assert settings.status_code==200,settings.text
    response=client.post('/api/v1/deepseek/endpoints',json={'name':'新的重试接口','api_key':'fake-new-retry-key',
        'provider':{'base_url':'https://new-retry.example/v1','model':'deepseek-retry','max_attempts':5}})
    assert response.status_code==200,response.text
    endpoint=response.json()['endpoints'][-1]
    assert endpoint['max_attempts']==5
    rejected=client.put('/api/v1/deepseek/endpoints/'+endpoint['id'],json={'provider':{'max_attempts':6}})
    assert rejected.status_code==422


@pytest.mark.parametrize('attempts',[1,3,5])
def test_material_summary_respects_explicit_provider_attempts(client,attempts):
    response=client.put('/api/v1/material-summary/settings',json={'provider':{'max_attempts':attempts}})
    assert response.status_code==200,response.text
    assert response.json()['provider']['max_attempts']==attempts
    assert material_summary.config()['max_attempts']==attempts


def test_material_summary_keeps_unified_endpoint_attempts(client):
    configure_primary(client,5)
    client.get('/api/v1/deepseek/settings')
    assert material_summary.config()['max_attempts']==5


@pytest.mark.parametrize('attempts',[1,3,5])
def test_stage_and_rule_configs_do_not_silently_reduce_attempts(client,attempts):
    cfg={**provider.DEFAULT,'base_url':'https://retry.example/v1','model':'deepseek-retry',
         'max_attempts':attempts,'max_output_tokens':8192,'thinking_enabled':True}
    with provider.request_scope(cfg,'fake-scoped-retry-key',route='deepseek'):
        resolved,key=profiles.resolve_config({'route':'primary','max_output_tokens':2048})
    assert resolved['max_attempts']==attempts and key=='fake-scoped-retry-key'
    rule=rule_review_runtime.request_config(cfg,2048)
    assert rule['max_attempts']==attempts
    assert rule['thinking_enabled'] is False and rule['max_output_tokens']==2048
