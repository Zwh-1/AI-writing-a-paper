"""Deterministic numeric/cache/concurrency tests; no model downloads or network."""
import hashlib
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from test_workbench import client, project, file
from test_reference_knowledge import fake_neural
from app import db, retrieval, reference_knowledge, security


@pytest.fixture(autouse=True)
def isolate_numeric_caches(monkeypatch):
    monkeypatch.setattr(retrieval, '_DENSE_MATRICES', OrderedDict())
    monkeypatch.setattr(retrieval, '_QUERY_VECTORS', OrderedDict())
    monkeypatch.setattr(retrieval, '_RERANK_SCORES', OrderedDict())


def vector_row(identity, values, text=None):
    vector = np.asarray(values, dtype=np.float32)
    row = {'id': identity, 'file_id': 'file-' + identity, 'text': text or identity,
           'name': 'synthetic', 'extension': '.txt', 'page_number': 1,
           'dimension': len(vector), 'vector': vector.tobytes()}
    row['text_hash'] = hashlib.sha256(retrieval.embedding_text(row).encode()).hexdigest()
    return row


def test_normalized_matrix_cache_invalidates_on_source_vector_model_and_project(monkeypatch):
    rows = [vector_row('b', [0, 2, 0]), vector_row('a', [3, 0, 0])]
    ordered, first, hit = retrieval._normalized_matrix('project-a', 'model-v1', rows, 3)
    assert not hit and [row['id'] for row in ordered] == ['a', 'b']
    assert np.allclose(first, [[1, 0, 0], [0, 1, 0]]) and not first.flags.writeable
    _, second, hit = retrieval._normalized_matrix('project-a', 'model-v1', list(reversed(rows)), 3)
    assert hit and second is first
    modified = [{**rows[0], 'vector': np.asarray([0, 0, 4], np.float32).tobytes()}, rows[1]]
    _, changed, hit = retrieval._normalized_matrix('project-a', 'model-v1', modified, 3)
    assert not hit and np.allclose(changed[1], [0, 0, 1])
    _, _, hit = retrieval._normalized_matrix('project-b', 'model-v1', modified, 3)
    assert not hit
    _, _, hit = retrieval._normalized_matrix('project-a', 'model-v2', modified, 3)
    assert not hit
    stale = [{**row, 'text': 'changed without rebuilding'} for row in modified]
    selected, matrix, _ = retrieval._normalized_matrix('project-a', 'model-v1', stale, 3)
    assert not selected and matrix.shape == (0, 3)


def test_vectorized_top_k_matches_cosine_and_has_deterministic_boundary_ties(monkeypatch):
    rng = np.random.default_rng(20261103)
    vectors = rng.normal(size=(2000, 32)).astype(np.float32)
    query = rng.normal(size=32).astype(np.float32)
    rows = [vector_row(f'{i:05}', values) for i, values in enumerate(vectors)]
    ordered, matrix, _ = retrieval._normalized_matrix('synthetic', 'test-model', rows, 32)
    expected = sorted([(float(values @ query / (np.linalg.norm(values) * np.linalg.norm(query))), row['id'])
                       for values, row in zip(vectors, rows)], key=lambda pair: (-pair[0], pair[1]))[:40]
    partition = np.argpartition
    calls = []
    monkeypatch.setattr(np, 'argpartition', lambda *a, **kw: calls.append(1) or partition(*a, **kw))
    result = retrieval._dense_top_k(matrix, query, ordered, 40)
    assert calls and [row['id'] for row in result] == [identity for _, identity in expected]
    assert [row['semantic_score'] for row in result] == pytest.approx([score for score, _ in expected], abs=2e-6)
    ties = [vector_row(f'{i:03}', [1, 1]) for i in range(100)]
    ordered, matrix, _ = retrieval._normalized_matrix('ties', 'test-model', ties, 2)
    assert [row['id'] for row in retrieval._dense_top_k(matrix, [1, 0], ordered, 7)] == [f'{i:03}' for i in range(7)]


def test_cached_matrix_never_repeats_per_document_normalization(monkeypatch):
    rows = [vector_row(str(i), [i + 1, 2, 3]) for i in range(250)]
    retrieval._normalized_matrix('normalization', 'test-model', rows, 3)
    norm = np.linalg.norm
    calls = []
    monkeypatch.setattr(np.linalg, 'norm', lambda *a, **kw: calls.append(1) or norm(*a, **kw))
    ordered, matrix, hit = retrieval._normalized_matrix('normalization', 'test-model', rows, 3)
    assert hit and not calls
    retrieval._dense_top_k(matrix, [1, 0, 0], ordered, 12)
    assert len(calls) == 1


def test_invalid_vector_rows_are_excluded_without_poisoning_matrix():
    rows = [vector_row('valid', [1, 2]), vector_row('zero', [0, 0]),
            vector_row('nan', [np.nan, 1]), vector_row('dimension', [1, 2, 3]),
            {**vector_row('broken', [1, 2]), 'vector': b'bad'}]
    selected, matrix, hit = retrieval._normalized_matrix('invalid', 'test-model', rows, 2)
    assert [row['id'] for row in selected] == ['valid']
    assert matrix.shape == (1, 2) and np.isfinite(matrix).all()


def test_dense_cache_preserves_scope_and_rechecks_permissions_on_every_request(client, monkeypatch):
    fake_neural(monkeypatch)
    pid = project(client)
    originals = [file(client, pid, 'credential identity ' + str(index)) for index in range(3)]
    db.set_setting('reference_knowledge:' + pid, {'rerank_enabled': False})
    reference_knowledge.build_index(pid)
    first = reference_knowledge.search(pid, 'credential identity', remote=True)
    second = reference_knowledge.search(pid, 'credential identity', remote=True)
    assert not first['pipeline']['dense']['matrix_cache_hit']
    assert second['pipeline']['dense']['matrix_cache_hit']
    assert second['final_ids'] == first['final_ids']
    assert second['pipeline']['dense']['vectorized_top_k']
    checked = []
    original = security.file_allows_remote
    def allowed(fid, project_id=None):
        checked.append(fid)
        return original(fid, project_id)
    monkeypatch.setattr(security, 'file_allows_remote', allowed)
    client.patch('/api/v1/files/' + originals[0]['id'], json={'egress': 'local_only'})
    restricted = reference_knowledge.search(pid, 'credential identity', remote=True)
    assert checked and all(item['file_id'] != originals[0]['id'] for item in restricted['items'])
    assert restricted['coverage']['eligible'] == 2
    assert all(value >= 0 for value in restricted['timings_ms'].values())
    scoped = retrieval.hybrid(pid, 'credential identity', remote=True, knowledge=True,
                              force=True, rerank_override='fusion', file_ids=[originals[1]['id']])
    assert {item['file_id'] for item in scoped['items']} == {originals[1]['id']}


def test_slow_query_embedding_does_not_hold_global_cache_lock(monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(retrieval, 'model_key', lambda name: 'test:' + name)
    retrieval._QUERY_VECTORS[('test:fast', 'warm')] = np.asarray([9, 1], np.float32)
    def embed(texts, name):
        started.set()
        assert release.wait(5)
        return [np.asarray([1, 2], np.float32)]
    monkeypatch.setattr(retrieval, 'embed', embed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(retrieval.query_embedding, 'cold', 'slow')
        try:
            assert started.wait(2)
            warm = pool.submit(retrieval.query_embedding, 'warm', 'fast')
            assert np.array_equal(warm.result(timeout=2), [9, 1])
        finally:
            release.set()
        assert np.array_equal(slow.result(timeout=2), [1, 2])


def test_slow_reranker_does_not_block_other_model_cache(monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(retrieval, 'model_key', lambda name: 'test:' + name)
    retrieval._QUERY_VECTORS[('test:fast', 'warm')] = np.asarray([5, 2], np.float32)
    class Scorer:
        def rerank(self, query, texts, batch_size):
            started.set()
            assert release.wait(5)
            return [0.5 for _ in texts]
    monkeypatch.setattr(retrieval, 'model_instance', lambda *a, **kw: Scorer())
    candidates = [{'id': 'one', 'text': 'synthetic material', 'fusion_rank': 1}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(retrieval.cross_encoder_rerank, 'cold', candidates, 'slow')
        try:
            assert started.wait(2)
            warm = pool.submit(retrieval.query_embedding, 'warm', 'fast')
            assert np.array_equal(warm.result(timeout=2), [5, 2])
        finally:
            release.set()
        assert slow.result(timeout=2)[0]['rerank_score'] == 0.5
