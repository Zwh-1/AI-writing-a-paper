"""Auxiliary index installation/removal preserves data and refuses active queues."""
from contextlib import closing
import sqlite3
from pathlib import Path
import subprocess
import sys

import pytest
from app import ai_cache_indexes as indexes


@pytest.fixture
def ledger(tmp_path):
    path=tmp_path/'cache-indexes.sqlite3'
    with closing(sqlite3.connect(path)) as c:
        c.executescript('''PRAGMA user_version=17;
            CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT);
            CREATE TABLE generation_runs(id TEXT PRIMARY KEY,project_id TEXT,task_id TEXT,status TEXT,manifest TEXT,created_at TEXT);
            INSERT INTO tasks VALUES('old-task','succeeded');
            INSERT INTO generation_runs VALUES('old-run','p','old-task','completed','{}','2026-11-03T00:00:00Z');''')
        c.commit()
    return path


def test_indexes_improve_lookup_and_remove_without_changing_history_or_version(ledger):
    with closing(sqlite3.connect(ledger)) as c:
        before=list(c.execute('SELECT * FROM generation_runs'))
        assert 'SCAN generation_runs' in indexes.inspect(c)['response_cache_lookup_query_plan']
        result=indexes.manage(c)
        assert set(indexes.INDEXES).issubset(result['indexes'])
        assert any('ai_cache_task_created' in line for line in result['response_cache_lookup_query_plan'])
        assert not any('TEMP B-TREE' in line for line in result['response_cache_lookup_query_plan'])
        for field,name in [('knowledge_cache_key','ai_cache_knowledge_key'),('outgoing_context_hash','ai_cache_outgoing_hash')]:
            plan=list(c.execute("EXPLAIN QUERY PLAN SELECT * FROM generation_runs WHERE project_id=? AND json_valid(manifest) AND json_extract(manifest,'$."+field+"')=? ORDER BY created_at DESC",('p','key')))
            assert any(name in row[3] for row in plan)
        assert indexes.manage(c)['user_version']==17
        assert list(c.execute('SELECT * FROM generation_runs'))==before
        assert not set(indexes.INDEXES)&set(indexes.manage(c,remove=True)['indexes'])
        assert list(c.execute('SELECT * FROM generation_runs'))==before
        assert c.execute('PRAGMA user_version').fetchone()[0]==17


@pytest.mark.parametrize('where,status',[('tasks','running'),('tasks','queued'),
    ('generation_runs','pending'),('generation_runs','sent')])
def test_active_work_prevents_all_index_mutations(ledger,where,status):
    with closing(sqlite3.connect(ledger)) as c:
        c.execute('UPDATE '+where+' SET status=?',(status,));c.commit()
        with pytest.raises(RuntimeError,match='idle'):
            indexes.manage(c)
        assert not set(indexes.INDEXES)&set(indexes.inspect(c)['indexes'])


def test_foreign_index_with_same_name_is_never_replaced_or_removed(ledger):
    with closing(sqlite3.connect(ledger)) as c:
        c.execute('CREATE INDEX ai_cache_task_created ON tasks(status)');c.commit()
        for remove in (False,True):
            with pytest.raises(RuntimeError,match='another definition'):
                indexes.manage(c,remove=remove)
            assert c.execute("SELECT tbl_name FROM sqlite_master WHERE name='ai_cache_task_created'").fetchone()[0]=='tasks'


def test_cli_check_is_readonly_and_does_not_create_a_missing_database(ledger,tmp_path):
    script=Path(__file__).resolve().parents[1]/'scripts/manage_ai_cache_indexes.py'
    before=ledger.read_bytes()
    result=subprocess.run([sys.executable,'-X','utf8',str(script),'--database',str(ledger),'--check'],capture_output=True,text=True)
    assert result.returncode==0 and 'SCAN generation_runs' in result.stdout
    assert ledger.read_bytes()==before
    missing=tmp_path/'missing.sqlite3'
    result=subprocess.run([sys.executable,'-X','utf8',str(script),'--database',str(missing),'--check'],capture_output=True,text=True)
    assert result.returncode!=0 and not missing.exists()
