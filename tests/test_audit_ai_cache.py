"""Audit only synthetic SQLite ledgers; never import or initialize the application."""
from contextlib import closing
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit_ai_cache.py'
SPEC = importlib.util.spec_from_file_location('audit_ai_cache_under_test', SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
SINCE = '2026-11-03T15:00:00+08:00'
UNTIL = '2026-11-03T16:00:00+08:00'


@pytest.fixture
def ledger(tmp_path):
    database = tmp_path / 'synthetic.sqlite3'
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript('''
            CREATE TABLE tasks(id TEXT PRIMARY KEY,kind TEXT);
            CREATE TABLE generation_runs(task_id TEXT,model TEXT,protocol TEXT,status TEXT,
                                         usage TEXT,manifest TEXT,created_at TEXT);
            INSERT INTO tasks VALUES('private-task-id','write');
        ''')
    def add(usage=None, manifest=None, created=SINCE, received='2026-11-03T15:15:00+08:00',
            status='completed', model='original-model', protocol='openai', task_id='private-task-id'):
        metadata = {'provider': {'base_url': 'https://original.example/v1',
                                'model': model, 'protocol': protocol},
                    'writing_stage': 'semantic_review'}
        metadata.update(manifest or {})
        if received is not None:
            metadata['response_evidence'] = {'received_at': received}
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('INSERT INTO generation_runs VALUES(?,?,?,?,?,?,?)',
                (task_id, model, protocol, status, json.dumps(usage or {}), json.dumps(metadata), created))
            connection.commit()
    return database, add


@pytest.mark.parametrize('usage,protocol,expected', [
    ({'prompt_tokens': 100, 'completion_tokens': 7, 'prompt_cache_hit_tokens': 20,
      'prompt_cache_miss_tokens': 80}, 'openai', (100, 7, 20, 80, None)),
    ({'input_tokens': 100, 'output_tokens': 7, 'raw': {'prompt_tokens': 100,
      'completion_tokens': 7, 'prompt_cache_hit_tokens': 20, 'prompt_cache_miss_tokens': 80}},
     'openai', (100, 7, 20, 80, None)),
    ({'prompt_tokens': 100, 'completion_tokens': 7, 'prompt_tokens_details': {'cached_tokens': 25}},
     'openai', (100, 7, 25, 75, None)),
    ({'input_tokens': 100, 'output_tokens': 7, 'input_tokens_details': {'cached_tokens': 30}},
     'responses', (100, 7, 30, 70, None)),
    ({'usage': {'input_tokens': 100, 'output_tokens': 7,
               'input_tokens_details': {'cached_tokens': 40}}}, 'responses', (100, 7, 40, 60, None)),
    ({'prompt_tokens': 100, 'prompt_cache_miss_tokens': 80}, 'openai', (100, None, 20, 80, None)),
    ({'input_tokens': 0, 'output_tokens': 0, 'input_tokens_details': {'cached_tokens': 0}},
     'responses', (0, 0, 0, 0, None)),
])
def test_supported_usage_normalization(usage, protocol, expected):
    assert audit.normalize_usage(usage, protocol) == expected
    assert audit.normalize_usage(json.dumps(usage), protocol) == expected


@pytest.mark.parametrize('usage,expected', [
    ({'input_tokens': 100, 'output_tokens': 7, 'cache_hit_tokens': 20, 'cache_miss_tokens': 80},
     (100, 7, 20, 80, None)),
    ({'input_tokens': 100, 'output_tokens': 7, 'cache_hit_tokens': 20, 'cache_miss_tokens': 80,
      'raw': {'prompt_tokens': 100, 'completion_tokens': 7, 'prompt_cache_hit_tokens': 20,
              'prompt_cache_miss_tokens': 80}}, (100, 7, 20, 80, None)),
    ({'input_tokens': 100, 'cache_hit_tokens': 20, 'cache_miss_tokens': 80,
      'raw': {'prompt_cache_hit_tokens': 21}}, (100, None, None, None, 'invalid_cache_counts')),
    ({'input_tokens': 100, 'cache_hit_tokens': 20, 'cache_miss_tokens': 80,
      'raw': {'prompt_cache_miss_tokens': 81}}, (100, None, None, None, 'invalid_cache_counts')),
    ({'input_tokens': 100, 'cache_hit_tokens': None, 'cache_miss_tokens': None},
     (100, None, None, None, 'missing_cache_counts')),
    ({'input_tokens': 100, 'cache_hit_tokens': None, 'cache_miss_tokens': None,
      'raw': {'prompt_cache_hit_tokens': 20, 'prompt_cache_miss_tokens': 80}},
     (100, None, 20, 80, None)),
])
def test_unified_cache_fields_match_raw_usage_without_hiding_unknowns_or_conflicts(usage, expected):
    assert audit.normalize_usage(usage, 'openai') == expected


@pytest.mark.parametrize('usage,input_value,reason', [
    ({'prompt_tokens': True, 'prompt_cache_hit_tokens': 0}, None, 'invalid_input'),
    ({'prompt_tokens': -1, 'prompt_cache_hit_tokens': 0}, None, 'invalid_input'),
    ({'prompt_tokens': 10, 'input_tokens': 11, 'prompt_cache_hit_tokens': 0}, None, 'invalid_input'),
    ({'input_tokens': 10, 'raw': {'prompt_tokens': 11}}, None, 'invalid_input'),
    ({'prompt_tokens': 10, 'prompt_cache_hit_tokens': True}, 10, 'invalid_cache_counts'),
    ({'prompt_tokens': 10, 'prompt_cache_hit_tokens': -1}, 10, 'invalid_cache_counts'),
    ({'prompt_tokens': 10, 'prompt_cache_hit_tokens': 3,
      'prompt_tokens_details': {'cached_tokens': 4}}, 10, 'invalid_cache_counts'),
    ({'prompt_tokens': 10, 'prompt_cache_hit_tokens': 3,
      'prompt_cache_miss_tokens': 8}, 10, 'contradictory_cache_counts'),
    ({'input_tokens': 10, 'input_tokens_details': {'cached_tokens': 11}}, 10, 'contradictory_cache_counts'),
    ({'prompt_cache_hit_tokens': 3, 'prompt_cache_miss_tokens': 7}, None, 'missing_input'),
    ({'prompt_tokens_details': {'cached_tokens': 3}}, None, 'missing_input'),
    ({'input_tokens_details': {'cached_tokens': 3}}, None, 'missing_input'),
    ({'prompt_tokens': 10}, 10, 'missing_cache_counts'),
    ({'input_tokens': 10, 'cache_read_input_tokens': 3}, 10, 'missing_cache_counts'),
])
def test_invalid_or_missing_cache_never_becomes_zero_or_total_input(usage, input_value, reason):
    inp, _, hit, miss, problem = audit.normalize_usage(usage, 'openai')
    assert inp == input_value and hit is None and miss is None and problem == reason


@pytest.mark.parametrize('output', [True, False, -1, 1.5, '7'])
def test_invalid_output_tokens_are_unknown(output):
    assert audit.normalize_usage({'prompt_tokens': 10, 'completion_tokens': output,
                                  'prompt_cache_hit_tokens': 3}, 'openai') == (10, None, 3, 7, None)


def test_conflicting_output_tokens_are_unknown_and_unsupported_protocol_is_not_guessed():
    assert audit.normalize_usage({'prompt_tokens': 10, 'completion_tokens': 5, 'output_tokens': 6,
                                  'prompt_cache_hit_tokens': 3}, 'openai')[1] is None
    assert audit.normalize_usage({'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 3},
                                  'anthropic') == (None, None, None, None, 'unsupported_protocol')


def test_received_window_uses_offsets_half_open_bounds_and_counts_fallback(ledger):
    database, add = ledger
    def usage(value):return {'prompt_tokens': value, 'completion_tokens': 1, 'prompt_cache_hit_tokens': 0}
    add(usage(10), created='2026-11-03T14:00:00+08:00', received='2026-11-03T07:00:00Z')
    add(usage(20), created='2026-11-03T17:00:00+08:00', received='2026-11-03T07:59:59+00:00')
    add(usage(1000), received='2026-11-03T08:00:00Z')
    add(usage(2000), received='2026-11-03T06:59:59Z')
    add(usage(30), created='2026-11-03T15:20:00+08:00', received=None)
    add(usage(40), created='2026-11-03T15:30:00+08:00', received='invalid')
    add(usage(50), created='2026-11-03T15:40:00+08:00', received='2026-11-03T15:40:00')
    add(usage(3000), created='2026-11-03T15:50:00', received=None)
    report = audit.audit(database, audit.timestamp(SINCE), audit.timestamp(UNTIL))
    assert report['totals']['runs'] == 5 and report['totals']['input_tokens'] == 150
    assert report['clock_fallback_runs'] == 3 and report['invalid_clock_runs'] == 1
    created = audit.audit(database, audit.timestamp(SINCE), audit.timestamp(UNTIL), 'created')
    assert created['totals']['runs'] == 5 and created['totals']['input_tokens'] == 3120
    assert created['clock_fallback_runs'] == 0 and created['invalid_clock_runs'] == 1


@pytest.mark.parametrize('stamp', ['2026-11-03T15:00:00', 'invalid'])
def test_cli_rejects_invalid_or_timezone_free_boundaries(ledger, stamp):
    database, _ = ledger
    result = subprocess.run([sys.executable, str(SCRIPT), '--database', str(database), '--since', stamp],
                            capture_output=True, text=True)
    assert result.returncode == 2


def test_aggregates_keep_unknowns_separate_and_exclude_private_fields(ledger):
    database, add = ledger
    private = {'provider': {'base_url': 'https://secret-user:secret-password@API.DEEPSEEK.COM./v1?key=secret-key',
                           'model': 'deepseek-flash', 'protocol': 'openai', 'api_key': 'secret-key'},
               'outgoing_context_hash': 'private-context-hash', 'project_id': 'private-project-id',
               'system_prompt': 'private-system-text', 'user_content': 'private-paper-text'}
    add({'prompt_tokens': 100, 'completion_tokens': 7, 'prompt_cache_hit_tokens': 20}, private)
    add({'prompt_tokens': 50, 'completion_tokens': 3}, private)
    add({}, private, status='rejected')
    report = audit.audit(database)
    total = report['totals']
    assert (total['runs'], total['input_tokens'], total['output_tokens']) == (3, 150, 10)
    assert (total['cache_hit_tokens'], total['cache_miss_tokens'], total['weighted_hit_rate']) == (20, 80, .2)
    assert (total['cache_known_runs'], total['cache_unknown_runs']) == (1, 2)
    assert total['status_counts'] == {'completed': 2, 'rejected': 1}
    assert report['groups'][0]['host'] == 'api.deepseek.com'
    assert report['duplicate_context_candidates']['additional_runs'] == 2
    assert 'candidate' in report['duplicate_context_candidates']['interpretation'].lower()
    encoded = json.dumps(report)
    for value in ('secret-user', 'secret-password', 'secret-key', 'private-project-id', 'private-task-id',
                  'private-system-text', 'private-paper-text', 'private-context-hash'):
        assert value not in encoded


def test_all_unknown_tokens_remain_null_and_empty_ledger_is_explicit(ledger):
    database, add = ledger
    empty = audit.audit(database)
    assert empty['totals']['runs'] == 0 and empty['groups'] == []
    add({})
    total = audit.audit(database)['totals']
    assert total['cache_unknown_runs'] == 1 and total['cache_known_runs'] == 0
    assert all(total[key] is None for key in ('input_tokens', 'output_tokens', 'cache_hit_tokens',
                                            'cache_miss_tokens', 'weighted_hit_rate'))


def test_report_metadata_identifies_schema_utc_generation_and_readonly_access(ledger):
    database, _ = ledger
    report = audit.audit(database)
    assert report['schema_version'] == 'ai-cache-audit.v2'
    assert audit.timestamp(report['generated_at']).utcoffset().total_seconds() == 0
    assert report['database_access'] == 'mode=ro; query_only=ON'


def test_nonpool_partial_provider_snapshot_inherits_known_provider_fields(ledger):
    database, add = ledger
    add({'prompt_tokens': 10}, {'provider_snapshot': {'model': 'confirmed-model'}})
    group = audit.audit(database)['groups'][0]
    assert (group['host'], group['model'], group['protocol']) == ('original.example', 'confirmed-model', 'openai')


@pytest.mark.parametrize('snapshot,expected', [
    ({}, ('unknown', 'unknown', 'unknown')),
    ({'base_url': 'https://actual.example/v1'}, ('actual.example', 'unknown', 'unknown')),
    ({'model': 'actual-model'}, ('unknown', 'actual-model', 'unknown')),
    ({'protocol': 'responses'}, ('unknown', 'unknown', 'responses')),
])
def test_pool_snapshot_never_invents_missing_actual_destination_fields(ledger, snapshot, expected):
    database, add = ledger
    add({'prompt_tokens': 10}, {'pool_used': 'pool', 'provider_snapshot': snapshot})
    report = audit.audit(database)
    group = report['groups'][0]
    assert (group['host'], group['model'], group['protocol']) == expected
    assert report['unattributed_pool_runs'] == 1


def test_unknown_actual_pool_protocol_does_not_reuse_original_protocol_semantics(ledger):
    database, add = ledger
    add({'input_tokens': 10, 'output_tokens': 2, 'input_tokens_details': {'cached_tokens': 4}},
        {'pool_used': 'pool', 'provider_snapshot': {'base_url': 'https://actual.example/v1',
                                                 'model': 'actual-model'}}, protocol='openai')
    report = audit.audit(database)
    total = report['totals']
    assert total['cache_known_runs'] == 0 and total['cache_unknown_runs'] == 1
    assert total['input_tokens'] is None and total['output_tokens'] is None
    assert total['cache_unknown_reasons'] == {'unsupported_protocol': 1}


def test_complete_pool_snapshot_is_the_actual_destination(ledger):
    database, add = ledger
    add({'input_tokens': 10, 'output_tokens': 2, 'input_tokens_details': {'cached_tokens': 4}},
        {'pool_used': 'pool', 'provider_snapshot': {'base_url': 'https://actual.example/v1',
                                                 'model': 'actual-model', 'protocol': 'responses'}})
    report = audit.audit(database)
    group = report['groups'][0]
    assert (group['host'], group['model'], group['protocol']) == ('actual.example', 'actual-model', 'responses')
    assert report['unattributed_pool_runs'] == 0 and group['cache_hit_tokens'] == 4


def test_database_is_opened_readonly_and_source_bytes_do_not_change(ledger, monkeypatch):
    database, add = ledger
    add({'prompt_tokens': 10})
    before = sha256(database.read_bytes()).hexdigest()
    original = sqlite3.connect
    observed = []
    def connection(database_uri, *args, **kwargs):
        assert database_uri.endswith('?mode=ro') and kwargs.get('uri') is True
        result = original(database_uri, *args, **kwargs)
        result.set_trace_callback(observed.append)
        return result
    monkeypatch.setattr(audit.sqlite3, 'connect', connection)
    report = audit.audit(database)
    assert observed[0] == 'PRAGMA query_only=ON'
    assert sha256(database.read_bytes()).hexdigest() == before
    assert report['audit_query_plan'] == report['query_plan']
    assert any('generation_runs' in item for item in report['response_cache_lookup_query_plan'])
    assert not any(statement.upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER')) for statement in observed)


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
@pytest.mark.parametrize('hardlink', [False, True])
def test_cli_refuses_database_sidecar_and_hardlink_output_paths(tmp_path, suffix, hardlink):
    database = tmp_path / 'source.sqlite3'
    database.write_bytes(b'original database sentinel')
    target = Path(str(database) + suffix)
    if suffix:target.write_bytes(b'original sidecar sentinel')
    destination = target
    if hardlink:
        destination = tmp_path / 'unrelated-name.json'
        destination.hardlink_to(target)
    before = {path.name: path.read_bytes() for path in (database, target)}
    result = subprocess.run([sys.executable, str(SCRIPT), '--database', str(database),
                             '--output', str(destination)], capture_output=True, text=True)
    assert result.returncode == 2 and 'source database or its sidecars' in result.stderr
    assert all((tmp_path / name).read_bytes() == value for name, value in before.items())


def test_cli_writes_only_the_requested_aggregate_json(ledger, tmp_path):
    database, add = ledger
    add({'prompt_tokens': 10, 'prompt_cache_hit_tokens': 2})
    before = sha256(database.read_bytes()).hexdigest()
    output = tmp_path / 'reports/aggregate.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--database', str(database),
                             '--since', SINCE, '--until', UNTIL, '--output', str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == ''
    assert json.loads(output.read_text(encoding='utf-8'))['totals']['cache_hit_tokens'] == 2
    assert sha256(database.read_bytes()).hexdigest() == before
