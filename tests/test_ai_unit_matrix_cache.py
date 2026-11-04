"""Numeric unit-index snapshots preserve live provenance; no models or HTTP."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading

import numpy as np
import pytest

from test_workbench import client, project, file
from test_reference_knowledge import fake_neural
from app import db, knowledge_service as service, knowledge_units as units, retrieval, security


@pytest.fixture(autouse=True)
def numeric_cache(monkeypatch):
    monkeypatch.setattr(retrieval, '_UNIT_MATRICES', OrderedDict())
    monkeypatch.setattr(retrieval, '_QUERY_VECTORS', OrderedDict())
    monkeypatch.setattr(retrieval, '_RERANK_SCORES', OrderedDict())


def row(chunk, unit, values, text='private original text'):
    vector = np.asarray(values, dtype=np.float32)
    digest = hashlib.sha256(text.encode()).hexdigest()
    return dict(chunk_id=chunk, unit_id=unit, parse_id='parse-' + unit,
                file_id='file-' + unit, file_hash='file-hash', input_hash='input-hash',
                text_hash=digest, current_hash=digest, dimension=len(vector), vector=vector.tobytes())


def legacy_scores(rows, query, limit=100):
    query = np.asarray(query, np.float32)
    best = {}
    for item in rows:
        vector = np.frombuffer(item['vector'], np.float32)
        score = float(vector @ query / (np.linalg.norm(vector) * np.linalg.norm(query)))
        best[item['unit_id']] = max(best.get(item['unit_id'], -np.inf), score)
    return sorted(best.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]


def test_vectorized_unit_max_scores_match_previous_cosine_and_stable_ties():
    rng = np.random.default_rng(20261103)
    rows = [row(str(index), f'unit-{index // 3:04}', rng.normal(size=32)) for index in range(1200)]
    query = rng.normal(size=32).astype(np.float32)
    expected = legacy_scores(rows, query)
    actual, hit = retrieval.unit_dense_scores('pid', 'model-v1', rows, query)
    fallback, _ = retrieval.unit_dense_scores('pid', 'model-v1', rows, query, cache_enabled=False)
    assert not hit and [uid for uid, _ in actual] == [uid for uid, _ in expected]
    assert [score for _, score in actual] == pytest.approx([score for _, score in expected], abs=2e-6)
    assert [uid for uid, _ in fallback] == [uid for uid, _ in expected]
    ties = [row('c-' + uid, uid, [1, 1]) for uid in ('z', 'b', 'a')]
    for enabled in (True, False):
        result, _ = retrieval.unit_dense_scores('ties', 'model-v1', ties, [1, 0], cache_enabled=enabled)
        assert [uid for uid, _ in result] == ['a', 'b', 'z']


@pytest.mark.parametrize('change', ['vector', 'chunk', 'unit', 'parse', 'file', 'input', 'text'])
def test_index_changes_invalidate_numeric_snapshot(change):
    rows = [row('chunk-a', 'unit-a', [1, 2]), row('chunk-b', 'unit-b', [2, 1])]
    retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0])
    _, hit = retrieval.unit_dense_scores('pid', 'model-v1', list(reversed(rows)), [0, 1])
    assert hit
    changed = [dict(item) for item in rows]
    if change == 'vector':changed[0]['vector'] = np.asarray([3, 4], np.float32).tobytes()
    elif change == 'text':changed[0].update(text_hash='new-hash', current_hash='new-hash')
    else:changed[0][{'chunk': 'chunk_id', 'unit': 'unit_id', 'parse': 'parse_id',
                    'file': 'file_hash', 'input': 'input_hash'}[change]] = 'changed'
    _, hit = retrieval.unit_dense_scores('pid', 'model-v1', changed, [1, 0])
    assert not hit and len(retrieval._UNIT_MATRICES) == 1


def test_database_project_and_verified_model_namespace_do_not_share_snapshot(monkeypatch, tmp_path):
    rows = [row('chunk-a', 'unit-a', [1, 2])]
    retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0])
    _, hit = retrieval.unit_dense_scores('other', 'model-v1', rows, [1, 0])
    assert not hit
    _, hit = retrieval.unit_dense_scores('pid', 'model-v2', rows, [1, 0])
    assert not hit
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'another.sqlite3')
    _, hit = retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0])
    assert not hit


def test_bad_zero_nan_inf_and_stale_vectors_do_not_poison_snapshot():
    rows = [row('valid', 'valid', [1, 2]), row('zero', 'zero', [0, 0]),
            row('nan', 'nan', [np.nan, 1]), row('inf', 'inf', [np.inf, 1]),
            row('dimension', 'dimension', [1, 2, 3]),
            {**row('broken', 'broken', [1, 2]), 'vector': b'bad'},
            {**row('bool', 'bool', [1, 2]), 'dimension': True},
            {**row('stale', 'stale', [1, 2]), 'current_hash': 'changed'}]
    for enabled in (True, False):
        result, _ = retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0], cache_enabled=enabled)
        assert [uid for uid, _ in result] == ['valid'] and np.isfinite(result[0][1])
    ids, matrix = next(iter(retrieval._UNIT_MATRICES.values()))
    assert ids == ('valid',) and matrix.shape == (1, 2) and not matrix.flags.writeable
    assert 'private original text' not in str(retrieval._UNIT_MATRICES.keys())


@pytest.mark.parametrize('query', [[0, 0], [np.nan, 1], [np.inf, 1], [[1, 0]]])
def test_invalid_query_is_rejected_without_caching(query):
    with pytest.raises(ValueError, match='无效向量'):
        retrieval.unit_dense_scores('pid', 'model-v1', [row('a', 'a', [1, 0])], query)
    assert not retrieval._UNIT_MATRICES


def test_warm_matrix_only_normalizes_the_query_once(monkeypatch):
    rows = [row(str(index), str(index), [index + 1, 2, 3]) for index in range(80)]
    retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0, 0])
    norm = np.linalg.norm
    calls = []
    monkeypatch.setattr(np.linalg, 'norm', lambda *a, **kw: calls.append(1) or norm(*a, **kw))
    _, hit = retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0, 0])
    assert hit and calls == [1]


def test_disabled_cache_recomputes_without_reading_or_replacing_warm_snapshot(monkeypatch):
    rows = [row('a', 'a', [1, 2])]
    retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0])
    saved = next(iter(retrieval._UNIT_MATRICES.values()))
    monkeypatch.setattr(retrieval, '_normalized_unit_matrix', lambda *a, **kw: pytest.fail('Disabled matrix cache read'))
    result, hit = retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0], cache_enabled=False)
    assert not hit and result[0][0] == 'a' and next(iter(retrieval._UNIT_MATRICES.values())) is saved


def test_numeric_snapshots_have_bounded_entries_and_bytes(monkeypatch):
    rows = [row('a', 'a', [1, 2])]
    for index in range(12):retrieval.unit_dense_scores(str(index), 'model-v1', rows, [1, 0])
    assert len(retrieval._UNIT_MATRICES) == 8
    retrieval._UNIT_MATRICES.clear()
    monkeypatch.setattr(retrieval, '_MATRIX_CACHE_BYTES', 24)
    for index in range(8):retrieval.unit_dense_scores(str(index), 'model-v1', rows, [1, 0])
    assert sum(value[1].nbytes for value in retrieval._UNIT_MATRICES.values()) <= 24
    retrieval._UNIT_MATRICES.clear()
    retrieval.unit_dense_scores('oversized', 'model-v1', rows * 2, [1, 0])
    assert not retrieval._UNIT_MATRICES  # Individual entries larger than half the budget are not retained.


def test_concurrent_same_snapshot_builds_once_and_results_cannot_mutate_it(monkeypatch):
    rows = [row(str(index), str(index), [index + 1, 2]) for index in range(50)]
    original = retrieval._build_unit_matrix
    calls = []; started = threading.Event(); release = threading.Event()
    def build(*args):
        calls.append(1); started.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(retrieval, '_build_unit_matrix', build)
    with ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(retrieval.unit_dense_scores, 'pid', 'model-v1', rows, [1, 0])
        assert started.wait(2)
        rest = [pool.submit(retrieval.unit_dense_scores, 'pid', 'model-v1', rows, [1, 0]) for _ in range(7)]
        release.set()
        results = [first.result(timeout=5), *(future.result(timeout=5) for future in rest)]
    assert calls == [1] and sum(hit for _, hit in results) == 7
    assert all(result == results[0][0] for result, _ in results)
    results[0][0].clear()
    assert retrieval.unit_dense_scores('pid', 'model-v1', rows, [1, 0])[0]


def indexed_library(client, monkeypatch):
    from app import ai_cache_policy
    fake_neural(monkeypatch)
    enabled = [True]
    monkeypatch.setattr(ai_cache_policy, 'settings', lambda: {'unit_matrix_cache_enabled': enabled[0]})
    pid = project(client)
    originals = [file(client, pid, 'credential identity original evidence ' + str(index)) for index in range(2)]
    for original in originals:units.build(pid, original['id'])
    db.set_setting('reference_knowledge:' + pid, {'semantic_enabled': True, 'rerank_enabled': False})
    service.index_units(pid)
    return pid, originals, enabled


def test_live_permission_rechecks_and_switch_fallback_preserve_search_results(client, monkeypatch):
    pid, originals, enabled = indexed_library(client, monkeypatch)
    first = service.search(pid, 'credential identity', remote=True)
    second = service.search(pid, 'credential identity', remote=True)
    assert not first['unit_pipeline']['dense']['matrix_cache_hit']
    assert second['unit_pipeline']['dense']['matrix_cache_hit']
    expected = [(item['id'], item.get('fusion_score')) for item in second['items']]
    enabled[0] = False
    fallback = service.search(pid, 'credential identity', remote=True)
    assert [(item['id'], item.get('fusion_score')) for item in fallback['items']] == expected
    assert not fallback['unit_pipeline']['dense']['cache_enabled']
    enabled[0] = True
    db.execute("UPDATE files SET egress='local_only' WHERE id=?", (originals[0]['id'],))
    checked = []; original = security.file_allows_remote
    def allowed(fid, *args):checked.append(fid); return original(fid, *args)
    monkeypatch.setattr(security, 'file_allows_remote', allowed)
    restricted = service.search(pid, 'credential identity', remote=True)
    assert originals[0]['id'] in checked
    assert restricted['items'] and all(item['file_id'] != originals[0]['id'] for item in restricted['items'])
    assert not restricted['unit_pipeline']['dense']['matrix_cache_hit']


def test_changed_original_never_reuses_a_stale_unit_after_warm_search(client, monkeypatch):
    pid, originals, _ = indexed_library(client, monkeypatch)
    service.search(pid, 'credential identity', remote=True)
    db.execute('UPDATE sources SET text=? WHERE file_id=?', ('updated original conditions', originals[0]['id']))
    changed = service.search(pid, 'credential identity', remote=True)
    assert all(item['file_id'] != originals[0]['id'] for item in changed['items'])
    assert not changed['unit_pipeline']['dense']['matrix_cache_hit']
