"""Run the integrated release's isolated GraphRAG worker without network calls."""
import argparse
import json
from pathlib import Path
import subprocess


def worker(root: Path, packet: dict) -> dict:
    process = subprocess.run(
        [str(root / '.runtime/python/python.exe'), '-I', '-X', 'utf8',
         str(root / 'scripts/graphrag_worker.py')],
        input=json.dumps(packet), capture_output=True, text=True,
        encoding='utf-8', timeout=240,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    events = [json.loads(line) for line in process.stdout.splitlines() if line.startswith('{')]
    if process.returncode:
        raise RuntimeError(process.stderr[-6000:] or json.dumps(events, ensure_ascii=False))
    if any(event.get('event') in ('completion', 'embedding', 'error') for event in events):
        raise RuntimeError('Local GraphRAG build unexpectedly requested a model or failed: ' + json.dumps(events))
    result = next((event.get('result') for event in events if event.get('event') == 'result'), None)
    if result is None:
        raise RuntimeError('Bundled GraphRAG worker did not return a result')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--qa', type=Path, required=True)
    args = parser.parse_args()
    root, qa = args.app.resolve(), args.qa.resolve()
    metadata = root / '.runtime/graphrag-deps/graphrag-3.3.0.dist-info/METADATA'
    if not metadata.is_file():
        raise RuntimeError('The complete integrated release is missing its GraphRAG runtime')
    diagnosis = worker(root, {'operation': 'diagnose', 'folder': str(qa / 'diagnosis'), 'protocol_test': True})
    if diagnosis.get('version') != '3.3.0' or not diagnosis.get('config_valid') or not diagnosis.get('synthetic_protocol_test'):
        raise RuntimeError('The integrated GraphRAG protocol probe failed: ' + json.dumps(diagnosis))
    entities = ['source-a', 'method-a', 'condition-a', 'result-a']
    edges = [('source-a', 'method-a'), ('method-a', 'condition-a'),
             ('condition-a', 'result-a'), ('result-a', 'source-a')]
    tables = {
        'entities': [
            {'id': value, 'human_readable_id': index, 'title': value, 'type': 'synthetic',
             'description': 'Synthetic acceptance entity', 'text_unit_ids': ['unit-a'],
             'frequency': 1, 'degree': 2}
            for index, value in enumerate(entities)
        ],
        'relationships': [
            {'id': 'relation-' + str(index), 'human_readable_id': index, 'source': left,
             'target': right, 'description': 'Synthetic local relation', 'weight': 1.0,
             'text_unit_ids': ['unit-a']}
            for index, (left, right) in enumerate(edges)
        ],
        'text_units': [{
            'id': 'unit-a', 'human_readable_id': 0,
            'text': 'Synthetic source scope for portable local graph acceptance.',
            'n_tokens': 12, 'document_id': 'document-a', 'entity_ids': entities,
            'relationship_ids': ['relation-' + str(index) for index in range(len(edges))],
            'covariate_ids': [],
        }],
        'documents': [{'id': 'document-a', 'text': 'Synthetic source for local GraphRAG.',
                       'title': 'Integrated synthetic graph'}],
    }
    result = worker(root, {'operation': 'build', 'folder': str(qa / 'local-community'),
                           'tables': tables, 'mapping': {'unit-a': {'document_id': 'document-a'}},
                           'reports': False, 'embeddings': False})
    if result.get('version') != '3.3.0' or 'create_communities' not in result.get('workflows', []):
        raise RuntimeError('The integrated release did not compute GraphRAG communities: ' + json.dumps(result))
    if result.get('tables', {}).get('communities', 0) < 1:
        raise RuntimeError('GraphRAG finished without creating a community: ' + json.dumps(result))
    report = {'passed': True, 'release_root': str(root), 'graph_runtime': '3.3.0',
              'protocol_sync_async_streaming': diagnosis['synthetic_protocol_test'],
              'local_community_tables': result['tables']['communities'],
              'paid_model_calls': 0, 'network_calls': 0}
    qa.mkdir(parents=True, exist_ok=True)
    (qa / 'graphrag-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
