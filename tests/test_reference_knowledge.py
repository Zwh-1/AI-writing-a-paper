import hashlib

import numpy as np
import pytest
from fastapi import HTTPException

from test_workbench import client, project, file, section, save, manual_package
from app import db, parsers, retrieval, model_install, tasks, security
from app import reference_knowledge as knowledge


@pytest.fixture(autouse=True)
def no_neural_inference(monkeypatch):
    original = model_install.status
    monkeypatch.setattr(model_install, 'status', lambda model: {**original(model), 'ready': False})


def fake_neural(monkeypatch):
    monkeypatch.setattr(model_install, 'status', lambda model: {'ready': True, 'model': model, 'fingerprint': 'test-model', 'path': 'unused'})
    def embed(texts, model):
        return [np.asarray([1.0, float('credential' in text.lower()), float('identity' in text.lower())], dtype=np.float32) for text in texts]
    monkeypatch.setattr(retrieval, 'embed', embed)
    class Scorer:
        def rerank(self, query, documents, **kwargs):
            return [0.0 for document in documents]
    monkeypatch.setattr(retrieval, 'model_instance', lambda *args, **kwargs: Scorer())


def test_research_original_and_chunks_are_knowledge_storage(client):
    pid = project(client)
    original = file(client, pid, 'A credential verification document with exact original statements.')
    document = db.one('SELECT * FROM knowledge_documents WHERE file_id=?', (original['id'],))
    sources = db.rows('SELECT * FROM sources WHERE file_id=?', (original['id'],))
    assert document['state'] == 'ready' and document['blob_sha256'] == original['sha256']
    assert document['chunk_count'] == len(sources)
    chunks = db.rows('SELECT * FROM knowledge_chunks WHERE document_id=?', (document['id'],))
    assert {item['source_id'] for item in chunks} == {source['id'] for source in sources}
    assert chunks[0]['source_hash'] == hashlib.sha256(sources[0]['text'].encode()).hexdigest()


def test_knowledge_sync_is_idempotent_and_keeps_citation_source_ids(client):
    pid = project(client); original = file(client, pid, 'Stable source for credential verification.')
    first = knowledge.sync_document(original['id'])
    source_ids = [row['source_id'] for row in db.rows('SELECT * FROM knowledge_chunks')]
    second = knowledge.sync_document(original['id'])
    assert first == second
    assert [row['source_id'] for row in db.rows('SELECT * FROM knowledge_chunks')] == source_ids
    assert len(db.rows('SELECT * FROM source_fts')) == len(source_ids)


def test_knowledge_search_returns_reference_and_original_page(client):
    pid = project(client); original = file(client, pid, 'This credential uses a soulbound token for academic verification.')
    reference = client.post(f'/api/v1/projects/{pid}/references', json={'metadata': {'title': 'Academic credential study', 'authors': ['Researcher A'], 'year': '2025'}, 'file_id': original['id']}).json()
    result = knowledge.search(pid, 'soulbound credential')
    assert result['items'] and result['mode'] == 'keyword'
    item = result['items'][0]
    assert item['reference_id'] == reference['id']
    assert item['reference_title'] == 'Academic credential study'
    assert item['page_number'] == 1 and item['knowledge_document_id']
    assert item['knowledge_chunk_hash'] == knowledge.text_hash(item['text'])
    assert result['external_requests'] is False


def test_knowledge_search_is_project_scoped_and_excludes_examples(client):
    pid = project(client); original = file(client, pid, 'credential identity from project one')
    other = project(client, '其他项目'); foreign = file(client, other, 'credential identity from project two')
    example = file(client, pid, 'credential identity writing example', role='example')
    result = knowledge.search(pid, 'credential identity')
    assert {item['file_id'] for item in result['items']} == {original['id']}
    assert not db.one('SELECT * FROM knowledge_documents WHERE file_id=?', (example['id'],))
    foreign_source = db.one('SELECT id FROM sources WHERE file_id=?', (foreign['id'],))['id']
    with pytest.raises(HTTPException) as error:
        knowledge.get_chunk(pid, foreign_source)
    assert error.value.status_code == 404


def test_local_only_papers_can_be_searched_locally_but_not_sent(client):
    pid = project(client); original = file(client, pid, 'local credential privacy evidence')
    client.patch('/api/v1/files/' + original['id'], json={'egress': 'local_only'})
    assert knowledge.search(pid, 'credential')['items']
    assert knowledge.search(pid, 'credential', remote=True)['items'] == []
    source = db.one('SELECT id FROM sources WHERE file_id=?', (original['id'],))['id']
    with pytest.raises(HTTPException) as error:
        knowledge.get_chunk(pid, source, remote=True)
    assert error.value.status_code == 403


def test_parent_permission_revocation_changes_knowledge_observation_epoch(client):
    pid = project(client); original = file(client, pid, 'credential derived text')
    parent = file(client, pid, 'parent original', role='example')
    security.link_derivation(original['id'], parent['id'], 'test provenance')
    before = knowledge.permission_epoch(pid)
    client.patch('/api/v1/files/' + parent['id'], json={'egress': 'local_only'})
    assert knowledge.permission_epoch(pid) != before
    assert knowledge.search(pid, 'credential', remote=True)['items'] == []


def test_archived_or_reclassified_files_leave_knowledge_search(client):
    pid = project(client); original = file(client, pid, 'credential original')
    client.patch('/api/v1/files/' + original['id'], json={'active': 0})
    assert knowledge.search(pid, 'credential')['items'] == []
    assert knowledge.status(pid)['documents'] == 0
    client.patch('/api/v1/files/' + original['id'], json={'active': 1, 'role': 'example'})
    assert knowledge.search(pid, 'credential')['items'] == []
    client.patch('/api/v1/files/' + original['id'], json={'role': 'research'})
    assert knowledge.search(pid, 'credential')['items']


def test_excluded_page_is_not_a_knowledge_chunk(client):
    pid = project(client); original = file(client, pid, 'credential page content')
    page = db.one('SELECT id FROM pages WHERE file_id=?', (original['id'],))['id']
    response = client.patch('/api/v1/pages/' + page, json={'review_state': 'not_applicable', 'review_note': 'not part of this research scope'})
    assert response.status_code == 200, response.text
    assert knowledge.search(pid, 'credential')['items'] == []
    assert not db.rows('SELECT * FROM knowledge_chunks')


def test_source_text_change_updates_keyword_index_without_breaking_source_id(client):
    pid = project(client); original = file(client, pid, 'oldcredential first version')
    source = db.one('SELECT * FROM sources WHERE file_id=?', (original['id'],))
    db.execute('UPDATE sources SET text=? WHERE id=?', ('newidentity second version', source['id']))
    assert knowledge.search(pid, 'oldcredential')['items'] == []
    found = knowledge.search(pid, 'newidentity')['items']
    assert found[0]['id'] == source['id'] and found[0]['text'] == 'newidentity second version'


def test_vectors_are_persisted_and_hybrid_search_uses_only_knowledge_sources(client, monkeypatch):
    fake_neural(monkeypatch)
    pid = project(client); original = file(client, pid, 'credential identity knowledge original')
    example = file(client, pid, 'credential identity writing example', role='example')
    result = knowledge.build_index(pid)
    assert result['knowledge_base']['vectors'] == result['knowledge_base']['chunks'] == 1
    assert db.one('SELECT dimension FROM neural_vectors')['dimension'] == 3
    found = knowledge.search(pid, 'credential identity')
    assert found['mode'] == 'neural_keyword_rrf'
    assert found['reranker'] == 'cross_encoder'
    assert {item['file_id'] for item in found['items']} == {original['id']}
    assert set(found['items'][0]['channels']) == {'keyword', 'neural'}


def test_vector_index_is_incremental_and_old_vector_not_used_for_new_text(client, monkeypatch):
    fake_neural(monkeypatch)
    pid = project(client); original = file(client, pid, 'credential knowledge')
    assert knowledge.build_index(pid)['indexed'] == 1
    assert knowledge.build_index(pid)['indexed'] == 0
    source = db.one('SELECT id FROM sources WHERE file_id=?', (original['id'],))['id']
    db.execute('UPDATE sources SET text=? WHERE id=?', ('newidentity revised knowledge', source))
    result = knowledge.search(pid, 'newidentity')
    assert result['mode'] == 'keyword' and result['items'][0]['id'] == source
    assert knowledge.status(pid)['vectors'] == 0
    assert knowledge.build_index(pid)['indexed'] == 1
    assert knowledge.status(pid)['vectors'] == 1


def test_missing_vector_model_keeps_a_usable_knowledge_base(client):
    pid = project(client); file(client, pid, 'credential original text')
    result = knowledge.build_index(pid)
    assert result['indexed'] == 0 and result['knowledge_base']['chunks'] == 1
    assert result['warnings']
    assert knowledge.search(pid, 'credential')['items']
    assert not db.rows('SELECT * FROM generation_runs')


def test_index_task_deduplication_and_knowledge_http_api(client):
    pid = project(client); original = file(client, pid, 'credential original text')
    first = client.post(f'/api/v1/projects/{pid}/knowledge-base/index').json()
    second = client.post(f'/api/v1/projects/{pid}/knowledge-base/index').json()
    assert first['id'] == second['id']
    assert tasks.run_one()
    assert db.one('SELECT status FROM tasks WHERE id=?', (first['id'],))['status'] == 'succeeded'
    response = client.post(f'/api/v1/projects/{pid}/knowledge-base/search', json={'query': 'credential'})
    assert response.status_code == 200 and response.json()['items']
    source = response.json()['items'][0]['id']
    assert client.get(f'/api/v1/projects/{pid}/knowledge-base/chunks/{source}').status_code == 200
    assert client.get(f'/api/v1/projects/{pid}/knowledge-base').json()['chunks'] == 1
    assert client.post(f'/api/v1/projects/{pid}/knowledge-base/search', json={'query': ' '}).status_code == 422


def test_document_chunk_pagination_and_project_boundary(client):
    pid = project(client); original = file(client, pid, 'credential paragraph text. ' * 300)
    prefix = f'/api/v1/projects/{pid}/knowledge-base/documents/{original["id"]}/chunks'
    first = client.get(prefix + '?limit=2').json()
    second = client.get(prefix + '?limit=2&offset=2').json()
    assert first['total'] > 2 and len(first['items']) == 2
    assert not set(item['id'] for item in first['items']) & set(item['id'] for item in second['items'])
    other = project(client, '其他项目')
    assert client.get(f'/api/v1/projects/{other}/knowledge-base/documents/{original["id"]}/chunks').status_code == 404


def test_normal_writing_retrieval_uses_knowledge_source_ids(client):
    pid = project(client); original = file(client, pid, 'credential identity paper source')
    found = parsers.search(pid, 'credential identity', remote=True)
    assert found and found[0]['knowledge_document_id']
    assert found[0]['file_id'] == original['id']
    assert all(item['id'] == db.one('SELECT id FROM sources WHERE id=?', (item['id'],))['id'] for item in found)


def test_agent_exposes_literature_knowledge_tools():
    from app.writing_agent import tools
    names = {item['function']['name'] for item in tools.schemas()}
    assert {'search_literature', 'read_literature'} <= names


def test_repeated_database_initialization_preserves_knowledge_objects(client):
    pid = project(client); original = file(client, pid, 'credential knowledge')
    before = db.one('SELECT * FROM knowledge_documents WHERE file_id=?', (original['id'],))
    db.init()
    assert db.one('SELECT * FROM knowledge_documents WHERE file_id=?', (original['id'],)) == before
