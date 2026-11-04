"""Optional non-unique performance indexes; no schema-version or ledger changes."""
import re

INDEXES = {
    'ai_cache_task_created': 'CREATE INDEX IF NOT EXISTS ai_cache_task_created ON generation_runs(task_id,created_at DESC)',
    'ai_cache_knowledge_key': "CREATE INDEX IF NOT EXISTS ai_cache_knowledge_key ON generation_runs(project_id,json_extract(manifest,'$.knowledge_cache_key'),created_at DESC) WHERE json_valid(manifest)",
    'ai_cache_outgoing_hash': "CREATE INDEX IF NOT EXISTS ai_cache_outgoing_hash ON generation_runs(project_id,json_extract(manifest,'$.outgoing_context_hash'),created_at DESC) WHERE json_valid(manifest)",
}


def _normalized(sql):
    return re.sub(r'\s+', '', sql.replace('IF NOT EXISTS', '').rstrip(';')).casefold()


def inspect(connection):
    return {'indexes': [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='generation_runs' ORDER BY name")],
            'response_cache_lookup_query_plan': [row[3] for row in connection.execute(
                'EXPLAIN QUERY PLAN SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at DESC', ('cache-index-check',))],
            'user_version': connection.execute('PRAGMA user_version').fetchone()[0]}


def manage(connection, remove=False):
    connection.execute('BEGIN IMMEDIATE')
    try:
        if (connection.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM generation_runs WHERE status IN ('pending','sent') LIMIT 1").fetchone()):
            raise RuntimeError('Cache index maintenance requires an idle task queue and no in-flight requests')
        for name, sql in INDEXES.items():
            existing = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
            if existing and _normalized(existing[0]) != _normalized(sql):
                raise RuntimeError('Refusing to modify an index owned by another definition: ' + name)
            connection.execute('DROP INDEX IF EXISTS ' + name if remove else sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return inspect(connection)
