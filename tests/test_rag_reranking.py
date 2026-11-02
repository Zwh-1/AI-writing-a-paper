import math

import pytest

from test_workbench import client, project, file
from test_reference_knowledge import fake_neural
from app import db, retrieval
from app import reference_knowledge as knowledge


def corpus(client, monkeypatch, count=2):
    fake_neural(monkeypatch)
    pid = project(client)
    files = [file(client, pid, f'credential identity academic study unique{i}') for i in range(count)]
    knowledge.build_index(pid)
    return pid, files


def test_rrf_fuses_ranks_without_mixing_bm25_and_cosine_scales():
    keyword = [{'id': 'a', 'rank': -1_000_000}, {'id': 'b', 'rank': -0.000001}]
    dense = [{'id': 'b', 'semantic_score': .1}, {'id': 'c', 'semantic_score': .0999}]
    fused = retrieval.reciprocal_rank_fusion(keyword, dense, 10)
    assert [item['id'] for item in fused] == ['b', 'a', 'c']
    assert fused[0]['fusion_score'] == pytest.approx(1/62 + 1/61)
    assert fused[0]['bm25_rank'] == 2 and fused[0]['dense_rank'] == 1
    scaled = retrieval.reciprocal_rank_fusion([{**item, 'rank': item['rank'] * 1000} for item in keyword], dense, 10)
    assert [item['fusion_score'] for item in scaled] == [item['fusion_score'] for item in fused]


def test_rrf_counts_a_source_once_per_retrieval_channel():
    fused = retrieval.reciprocal_rank_fusion(
        [{'id': 'a', 'rank': -9}, {'id': 'a', 'rank': -8}, {'id': 'b', 'rank': -7}],
        [{'id': 'b', 'semantic_score': .8}, {'id': 'b', 'semantic_score': .7}], 10,
    )
    assert len(fused) == 2
    assert fused[0]['id'] == 'b' and fused[0]['fusion_score'] == pytest.approx(1/62 + 1/61)
    assert fused[1]['fusion_score'] == pytest.approx(1/61)
    assert fused[0]['channels'] == ['keyword', 'neural']


def test_cross_encoder_rescores_query_document_pairs_and_changes_final_order(client, monkeypatch):
    pid, files = corpus(client, monkeypatch)
    db.set_setting('reference_knowledge:' + pid, {'rerank_enabled': False})
    baseline = knowledge.search(pid, 'credential identity')
    target = baseline['candidates'][-1]
    received = {}
    class Scorer:
        def rerank(self, query, documents, batch_size):
            received.update(query=query, documents=list(documents), batch_size=batch_size)
            return [99.0 if target['text'] in document else -5.0 for document in received['documents']]
    monkeypatch.setattr(retrieval, 'model_instance', lambda *args, **kwargs: Scorer())
    db.set_setting('reference_knowledge:' + pid, {'rerank_enabled': True})
    result = knowledge.search(pid, 'credential identity')
    assert received['query'] == 'credential identity' and received['batch_size'] == 8
    assert len(received['documents']) == 2
    assert result['items'][0]['id'] == target['id']
    assert result['items'][0]['fusion_rank'] == 2 and result['items'][0]['rerank_rank'] == 1
    assert result['items'][0]['rerank_score'] == 99.0
    assert result['reranker'] == 'cross_encoder' and not result['degraded']
    assert result['pipeline']['rerank']['status'] == 'completed'
    assert result['pipeline']['rerank']['scored'] == 2
    assert all(value >= 0 for value in result['timings_ms'].values())


def test_cross_encoder_only_receives_the_fused_candidate_pool(client, monkeypatch):
    pid, files = corpus(client, monkeypatch, count=5)
    db.set_setting('retrieval:' + pid, {**retrieval.config(pid), 'candidate_k': 2})
    scored = []
    class Scorer:
        def rerank(self, query, documents, **kwargs):
            scored.extend(documents)
            return list(range(len(documents)))
    monkeypatch.setattr(retrieval, 'model_instance', lambda *args, **kwargs: Scorer())
    result = knowledge.search(pid, 'credential identity', limit=1)
    assert len(scored) == result['pipeline']['rrf']['candidates'] == 2
    assert result['pipeline']['bm25']['limit'] == result['pipeline']['dense']['limit'] == 2
    assert result['pipeline']['rerank']['scored'] == 2 and len(result['items']) == 1


@pytest.mark.parametrize('values', [[1.0], [float('nan'), 1.0], [float('inf'), 1.0], [None, 1.0]])
def test_invalid_reranker_scores_keep_rrf_candidates_and_report_actual_fallback(client, monkeypatch, values):
    pid, files = corpus(client, monkeypatch)
    class Scorer:
        def rerank(self, *args, **kwargs): return values
    monkeypatch.setattr(retrieval, 'model_instance', lambda *args, **kwargs: Scorer())
    result = knowledge.search(pid, 'credential identity')
    assert len(result['items']) == len(result['candidates']) == 2
    assert result['mode'] == 'neural_keyword_rrf' and result['reranker'] == 'fusion_only'
    assert result['degraded'] and result['pipeline']['rerank']['status'] == 'failed'
    assert result['pipeline']['rerank']['scored'] == 0
    assert any('Cross-Encoder重排未完成' in warning for warning in result['warnings'])
    assert all(item['rerank_score'] is None for item in result['items'])
    assert result['final_ids'] == result['fusion_ids']


def test_missing_reranker_does_not_discard_dense_retrieval(client, monkeypatch):
    pid, files = corpus(client, monkeypatch)
    def unavailable(*args, **kwargs): raise ValueError('本地重排模型未安装')
    monkeypatch.setattr(retrieval, 'model_instance', unavailable)
    result = knowledge.search(pid, 'credential identity')
    assert result['reranker'] == 'fusion_only' and result['degraded']
    assert result['pipeline']['dense']['candidates'] == 2
    assert result['pipeline']['rrf']['candidates'] == 2
    assert all('neural' in item['channels'] for item in result['items'])


def test_explicit_fusion_mode_skips_cross_encoder_without_claiming_a_failure(client, monkeypatch):
    pid, files = corpus(client, monkeypatch)
    db.set_setting('reference_knowledge:' + pid, {'rerank_enabled': False})
    monkeypatch.setattr(retrieval, 'model_instance', lambda *args, **kwargs: pytest.fail('Reranker was explicitly disabled'))
    result = knowledge.search(pid, 'credential identity')
    assert result['reranker'] == 'fusion_only' and not result['degraded']
    assert result['pipeline']['rerank']['status'] == 'skipped'


def test_dense_candidates_can_be_reranked_when_bm25_has_no_literal_match(client, monkeypatch):
    pid, files = corpus(client, monkeypatch)
    result = knowledge.search(pid, 'nonliteralquery')
    assert result['pipeline']['bm25']['candidates'] == 0
    assert result['pipeline']['dense']['candidates'] == result['pipeline']['rerank']['scored'] == 2
    assert len(result['items']) == 2 and result['reranker'] == 'cross_encoder'


def test_permissions_are_filtered_before_cross_encoder_and_rechecked_after_inference(client, monkeypatch):
    pid, files = corpus(client, monkeypatch)
    private = file(client, pid, 'credential forbidden private document')
    client.patch('/api/v1/files/' + private['id'], json={'egress': 'local_only'})
    other = project(client, '其他项目')
    foreign = file(client, other, 'credential forbidden foreign project document')
    knowledge.build_index(pid)
    observed = []
    class Scorer:
        def rerank(self, query, documents, **kwargs):
            observed.extend(documents)
            assert all('forbidden' not in document for document in documents)
            db.execute("UPDATE files SET egress='local_only' WHERE id=?", (files[0]['id'],))
            return list(range(len(documents)))
    monkeypatch.setattr(retrieval, 'model_instance', lambda *args, **kwargs: Scorer())
    result = knowledge.search(pid, 'credential', remote=True)
    assert len(observed) == 2
    assert {item['file_id'] for item in result['items']} == {files[1]['id']}
    allowed_ids = {row['id'] for row in db.rows('SELECT id FROM sources WHERE file_id=?', (files[1]['id'],))}
    assert set(result['bm25_ids'] + result['dense_ids'] + result['final_ids']) <= allowed_ids


def test_evaluation_compares_bm25_dense_rrf_and_actual_final_reranking(client, monkeypatch):
    pid, files = corpus(client, monkeypatch)
    db.set_setting('reference_knowledge:' + pid, {'rerank_enabled': False})
    baseline = knowledge.search(pid, 'credential identity')
    target = baseline['candidates'][-1]
    class Scorer:
        def rerank(self, query, documents, **kwargs):
            return [9.0 if target['text'] in document else -9.0 for document in documents]
    monkeypatch.setattr(retrieval, 'model_instance', lambda *args, **kwargs: Scorer())
    db.set_setting('retrieval:' + pid, {**retrieval.config(pid), 'enabled': True, 'rerank': 'cross_encoder'})
    report = retrieval.evaluate(pid, [{'query': 'credential identity', 'kind': 'research', 'expected_source_ids': [target['id']]}], k=2)
    assert report['summary']['fusion']['mrr'] == .5
    assert report['summary']['reranked']['mrr'] == 1.0
    assert 'bm25' in report['summary'] and 'dense' in report['summary']
    assert report['cases'][0]['reranker'] == 'cross_encoder'
    assert report['annotation_status'] == 'submitted_unverified' and not report['human_acceptance']


def test_rerank_ties_preserve_rrf_order(monkeypatch):
    monkeypatch.setattr(retrieval,'model_key',lambda model:'synthetic-tie-model:v1')
    class Scorer:
        def rerank(self, *args, **kwargs): return [2.0, 2.0]
    monkeypatch.setattr(retrieval, 'model_instance', lambda *args, **kwargs: Scorer())
    candidates = [{'id': 'z', 'text': 'first', 'fusion_rank': 1}, {'id': 'a', 'text': 'second', 'fusion_rank': 2}]
    assert [item['id'] for item in retrieval.cross_encoder_rerank('query', candidates, 'local-model')] == ['z', 'a']
