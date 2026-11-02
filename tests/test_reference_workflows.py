"""HTTP contracts exercised by the literature pages."""
import pytest
from test_workbench import client, project, file
from app import knowledge_units as units, model_install, document_reading as reader


@pytest.fixture(autouse=True)
def local_models(monkeypatch):
    monkeypatch.setattr(model_install, 'status', lambda name: {'ready': False})


def test_evidence_search_keeps_selected_original(client, monkeypatch):
    monkeypatch.setattr(model_install, 'status', lambda name: {'ready': False})
    pid = project(client)
    first = file(client, pid, '凭证验证仅限单用户。')
    second = file(client, pid, '凭证验证也讨论并发。蓝鲸是本文独有定位词。')
    for original in (first, second):
        units.build(pid, original['id'])
    base = f'/api/v1/projects/{pid}/evidence-knowledge/search'
    all_hits = client.post(base, json={'query': '凭证验证'}).json()['items']
    assert {item['file_id'] for item in all_hits} == {first['id'], second['id']}
    scoped = client.post(base, json={'query': '凭证验证', 'file_ids': [first['id']]}).json()['items']
    assert scoped and all(item['file_id'] == first['id'] for item in scoped)
    absent = client.post(base, json={'query': '蓝鲸', 'file_ids': [first['id']]}).json()['items']
    assert absent == []
    other = project(client, '另一项目')
    foreign = file(client, other, '凭证验证')
    assert client.post(base, json={'query': '凭证验证', 'file_ids': [foreign['id']]}).status_code == 404


def test_terms_crud_persists_and_rejects_cross_project_edits(client):
    pid, other = project(client), project(client, '另一项目')
    base = f'/api/v1/projects/{pid}/terms'
    created = client.post(base, json={'zh': '凭证', 'en': 'credential'})
    assert created.status_code == 200
    term = created.json()[0]
    changed = client.patch(base + '/' + term['id'], json={'zh': '凭证', 'en': 'verifiable credential'})
    assert changed.status_code == 200
    assert client.get(base).json()[0]['en'] == 'verifiable credential'
    assert client.patch(f'/api/v1/projects/{other}/terms/{term["id"]}', json={'zh': '凭证', 'en': 'other'}).status_code == 404
    assert client.delete(base + '/' + term['id']).status_code == 200
    assert client.get(base).json() == []


def test_resumed_reading_keeps_document_order_and_unambiguous_units(client):
    pid = project(client)
    original = file(client, pid, '研究方法为案例分析。\n\n适用条件仅为单用户。\n\n局限是不支持并发。')
    units.build(pid, original['id'])
    reading = reader.create(pid, {'file_id': original['id'], 'question': '方法的条件与局限是什么？'})
    reader.read_range(pid, reading['id'], {'ordinal_start': 3, 'ordinal_end': 3})
    reader.read_range(pid, reading['id'], {'ordinal_start': 1, 'ordinal_end': 2})
    resumed = reader.context(pid, reading['id'])
    assert [p['ordinal'] for p in resumed['passages']] == [1, 2, 3]
    assert '适用条件' in resumed['passages'][1]['text']
