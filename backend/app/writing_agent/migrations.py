"""Atomic forward-only migrations; never resets a newer user_version."""
import hashlib,sqlite3
from pathlib import Path

CURRENT=19
FILES=((4,'007_writing_foundation.sql'),(5,'008_writing_execution.sql'),(6,'009_writing_evaluation.sql'),(7,'010_writing_completion.sql'),(8,'012_evidence_knowledge.sql'),(9,'013_document_reading.sql'),(10,'014_graph_wiki.sql'),(11,'015_writing_integrity.sql'),(12,'016_writing_validation_indexes.sql'),(13,'017_expense_evidence.sql'),(14,'018_knowledge_operations.sql'),(15,'019_maintenance_retries.sql'),(16,'020_research_studio.sql'),(17,'021_integrated_resources.sql'),(18,'022_request_execution.sql'),(19,'023_ai_runtime.sql'))

def statements(script):
    pending=''
    for line in script.splitlines(keepends=True):
        pending+=line
        if sqlite3.complete_statement(pending):
            yield pending;pending=''
    if pending.strip():raise RuntimeError('迁移SQL未闭合')

def upgrade(connection):
    from .. import db
    version=connection.execute('PRAGMA user_version').fetchone()[0]
    if version>CURRENT:raise RuntimeError('数据库由更高版本创建')
    connection.commit()
    if 4<=version<CURRENT:
        target=Path(db.DB_PATH).parent/f'migration-before-writing-v{CURRENT}.sqlite3'
        if not target.exists():
            backup=sqlite3.connect(target)
            try:connection.backup(backup)
            finally:backup.close()
    if version<4:
        target=Path(db.DB_PATH).parent/'migration-before-writing-v4.sqlite3'
        if not target.exists():
            backup=sqlite3.connect(target)
            try:connection.backup(backup)
            finally:backup.close()
    connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations(number INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)')
    connection.commit()
    directory=Path(__file__).resolve().parents[2]/'migrations'
    for next_version,filename in FILES:
        content=(directory/filename).read_text(encoding='utf-8');sha=hashlib.sha256(content.encode()).hexdigest()
        old=connection.execute('SELECT checksum FROM schema_migrations WHERE number=?',(next_version,)).fetchone()
        if old:
            if old[0]!=sha:raise RuntimeError('已应用迁移内容被修改：'+filename)
            if next_version>version:raise RuntimeError('数据库版本与迁移日志不一致')
            continue
        if next_version<=version:raise RuntimeError('数据库缺少迁移校验记录')
        connection.execute('BEGIN IMMEDIATE')
        try:
            for statement in statements(content):connection.execute(statement)
            if connection.execute('PRAGMA foreign_key_check').fetchone():raise RuntimeError('迁移外键检查失败')
            connection.execute('INSERT INTO schema_migrations VALUES(?,?,?)',(next_version,sha,db.now()))
            connection.execute(f'PRAGMA user_version={next_version}')
            connection.commit();version=next_version
        except Exception:connection.rollback();raise
