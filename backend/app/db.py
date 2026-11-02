import sqlite3, json, uuid, threading, contextvars, functools
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import DB_PATH

def uid(): return uuid.uuid4().hex

def now(): return datetime.now(timezone.utc).isoformat()

def dumps(v): return json.dumps(v, ensure_ascii=False, separators=(',', ':'))

def loads(v, default=None):
    try: return json.loads(v) if v is not None else default
    except (ValueError, TypeError): return default

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT NOT NULL,paper_type TEXT NOT NULL DEFAULT 'software_system',brief TEXT NOT NULL DEFAULT '{}',target_words INTEGER NOT NULL DEFAULT 15000,budget REAL NOT NULL DEFAULT 20,stage TEXT NOT NULL DEFAULT 'preparing',rule_package_id TEXT,outline_revision INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS project_revisions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,sha256 TEXT NOT NULL,extension TEXT NOT NULL,size INTEGER NOT NULL,role TEXT NOT NULL DEFAULT 'research',egress TEXT NOT NULL DEFAULT 'selected_content',status TEXT NOT NULL DEFAULT 'queued',error TEXT,pages INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,UNIQUE(project_id,sha256,role));
CREATE TABLE IF NOT EXISTS pages(id TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id),page_number INTEGER NOT NULL,title TEXT NOT NULL DEFAULT '',text TEXT NOT NULL DEFAULT '',images TEXT NOT NULL DEFAULT '[]',notes TEXT NOT NULL DEFAULT '',review_state TEXT NOT NULL DEFAULT 'unreviewed',review_note TEXT NOT NULL DEFAULT '',has_visual INTEGER NOT NULL DEFAULT 0,UNIQUE(file_id,page_number));
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id),page_id TEXT REFERENCES pages(id),locator TEXT NOT NULL,text TEXT NOT NULL,normalized TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'evidence',method TEXT NOT NULL DEFAULT 'native',verified INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_sources_file ON sources(file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(source_id UNINDEXED,tokens, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),source_id TEXT REFERENCES sources(id),category TEXT NOT NULL DEFAULT 'content',scope TEXT NOT NULL DEFAULT 'global',operations TEXT NOT NULL DEFAULT '["generate","revise","expand","compress","translate"]',statement TEXT NOT NULL,modality TEXT NOT NULL DEFAULT 'pending',state TEXT NOT NULL DEFAULT 'candidate',checker TEXT NOT NULL DEFAULT 'manual',rule_key TEXT NOT NULL DEFAULT '',value TEXT,decision_note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_packages(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),version INTEGER NOT NULL,rules TEXT NOT NULL,source_manifest TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_id,version));
CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),label TEXT NOT NULL,statement TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'planned',verification TEXT NOT NULL DEFAULT 'unverified',source_ids TEXT NOT NULL DEFAULT '[]',artifact_id TEXT,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fact_revisions(id TEXT PRIMARY KEY,fact_id TEXT NOT NULL REFERENCES facts(id),content TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sections(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),parent_id TEXT,title TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'body',position INTEGER NOT NULL,target_words INTEGER NOT NULL DEFAULT 1500,goal TEXT NOT NULL DEFAULT '',dependencies TEXT NOT NULL DEFAULT '[]',current_revision_id TEXT,stale INTEGER NOT NULL DEFAULT 0,stale_reason TEXT NOT NULL DEFAULT '',state TEXT NOT NULL DEFAULT 'empty');
CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,section_id TEXT NOT NULL REFERENCES sections(id),parent_id TEXT,document TEXT NOT NULL,text TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'manual',task_id TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_revisions_section ON revisions(section_id,created_at);
CREATE TABLE IF NOT EXISTS locks(section_id TEXT NOT NULL REFERENCES sections(id),node_id TEXT NOT NULL,PRIMARY KEY(section_id,node_id));
CREATE TABLE IF NOT EXISTS bibliography(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),metadata TEXT NOT NULL,file_id TEXT REFERENCES files(id),verification TEXT NOT NULL DEFAULT 'unverified',revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bibliography_revisions(id TEXT PRIMARY KEY,reference_id TEXT NOT NULL REFERENCES bibliography(id),content TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS citations(id TEXT PRIMARY KEY,revision_id TEXT NOT NULL REFERENCES revisions(id),node_id TEXT NOT NULL,reference_id TEXT NOT NULL REFERENCES bibliography(id),reference_snapshot TEXT NOT NULL,source_ids TEXT NOT NULL,support TEXT NOT NULL DEFAULT 'unknown',kind TEXT NOT NULL DEFAULT 'paraphrase',excerpt TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS dependencies(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),upstream_type TEXT NOT NULL,upstream_id TEXT NOT NULL,upstream_version TEXT NOT NULL,section_id TEXT NOT NULL REFERENCES sections(id),revision_id TEXT NOT NULL REFERENCES revisions(id));
CREATE TABLE IF NOT EXISTS mentor_tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),section_id TEXT REFERENCES sections(id),comment TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',resolution TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,kind TEXT NOT NULL,sha256 TEXT,metadata TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS findings(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),section_id TEXT,revision_id TEXT,rule_id TEXT,severity TEXT NOT NULL,result TEXT NOT NULL,method TEXT NOT NULL,message TEXT NOT NULL,evidence TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',phase TEXT NOT NULL DEFAULT '',progress INTEGER NOT NULL DEFAULT 0,result TEXT,error TEXT,cancel_requested INTEGER NOT NULL DEFAULT 0,lease_until REAL,heartbeat REAL,attempt INTEGER NOT NULL DEFAULT 0,idempotency_key TEXT,input_hash TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(project_id,idempotency_key));
CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL REFERENCES tasks(id),event_type TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id,id);
CREATE TABLE IF NOT EXISTS generation_runs(id TEXT PRIMARY KEY,task_id TEXT REFERENCES tasks(id),project_id TEXT REFERENCES projects(id),status TEXT NOT NULL,model TEXT NOT NULL,protocol TEXT NOT NULL,manifest TEXT NOT NULL,usage TEXT NOT NULL DEFAULT '{}',cost REAL,reserved REAL NOT NULL DEFAULT 0,remote_id TEXT,error TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),manifest TEXT NOT NULL,filename TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backups(id TEXT PRIMARY KEY,filename TEXT NOT NULL,manifest TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS format_profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL,template_sha256 TEXT,verified INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT,action TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS terms(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),zh TEXT NOT NULL,en TEXT NOT NULL,UNIQUE(project_id,zh));
CREATE TABLE IF NOT EXISTS rule_conditions(rule_id TEXT PRIMARY KEY REFERENCES rules(id),expression TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS manual_rule_reviews(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),rule_id TEXT NOT NULL,package_id TEXT NOT NULL,input_fingerprint TEXT NOT NULL,result TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS section_reviews(id TEXT PRIMARY KEY,section_id TEXT NOT NULL REFERENCES sections(id),revision_id TEXT NOT NULL REFERENCES revisions(id),note TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_formats(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,config TEXT NOT NULL,template_sha256 TEXT,verified INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS section_archives(section_id TEXT PRIMARY KEY REFERENCES sections(id),metadata TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS retrieval_evaluations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),result TEXT NOT NULL,created_at TEXT NOT NULL);
PRAGMA user_version=2;
"""

_ACTIVE=contextvars.ContextVar("paper_transaction",default=None)
_READ_SESSION=contextvars.ContextVar('paper_read_session',default=None)

def connect():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=20000')
    return c

@contextmanager
def read_session():
    """Reuse a connection for a synchronous read operation, without a read lock.

    SELECTs remain autocommit. Lazy index writes use their own transaction and
    subsequent reads see the commit. This is not a long-lived data snapshot.
    """
    existing=_READ_SESSION.get()
    if _ACTIVE.get() is not None or existing is not None and existing[0]==DB_PATH:
        yield
        return
    connection=connect();token=_READ_SESSION.set((DB_PATH,connection))
    try:yield
    finally:
        _READ_SESSION.reset(token)
        connection.close()

@contextmanager
def transaction():
    existing=_ACTIVE.get()
    if existing is not None:
        yield existing
        return
    c=connect();token=None
    try:
        c.execute('BEGIN IMMEDIATE');token=_ACTIVE.set(c)
        yield c
        c.commit()
    except Exception:
        c.rollback();raise
    finally:
        if token is not None:_ACTIVE.reset(token)
        c.close()

def init():
    with connect() as c:
        version=c.execute('PRAGMA user_version').fetchone()[0]
        from .writing_agent.migrations import CURRENT
        if version>CURRENT:raise RuntimeError('数据库由更高版本程序创建，请先升级程序或恢复兼容备份')
        if version>=4:
            from .writing_agent.migrations import upgrade
            upgrade(c)
            from pathlib import Path
            c.executescript((Path(__file__).parents[1]/'migrations/006_rule_automation.sql').read_text(encoding='utf-8'))
            c.executescript((Path(__file__).parents[1]/'migrations/011_reference_knowledge.sql').read_text(encoding='utf-8'))
            return
        if version==1:
            from pathlib import Path
            snapshot=Path(DB_PATH).parent/'migration-v1-before-v2.sqlite3'
            if not snapshot.exists():
                backup=sqlite3.connect(snapshot)
                try:c.backup(backup)
                finally:backup.close()
        if version==2:
            from pathlib import Path
            snapshot=Path(DB_PATH).parent/'migration-v2-before-v3.sqlite3'
            if not snapshot.exists():
                target=sqlite3.connect(snapshot)
                try:c.backup(target)
                finally:target.close()
        from pathlib import Path
        extra=(Path(__file__).parents[1]/'migrations/003_advanced.sql').read_text(encoding='utf-8')
        c.executescript(SCHEMA.replace('PRAGMA user_version=2;',extra))
        c.executescript((Path(__file__).parents[1]/'migrations/004_material_summary.sql').read_text(encoding='utf-8'))
        c.executescript((Path(__file__).parents[1]/'migrations/005_billing_groups.sql').read_text(encoding='utf-8'))
        c.executescript((Path(__file__).parents[1]/'migrations/006_rule_automation.sql').read_text(encoding='utf-8'))
        from .writing_agent.migrations import upgrade
        upgrade(c)
        c.executescript((Path(__file__).parents[1]/'migrations/011_reference_knowledge.sql').read_text(encoding='utf-8'))

def rows(sql,args=()):
    active=_ACTIVE.get()
    if active is not None:return [dict(r) for r in active.execute(sql,args).fetchall()]
    session=_READ_SESSION.get()
    if session is not None and session[0]==DB_PATH:return [dict(r) for r in session[1].execute(sql,args).fetchall()]
    c=connect()
    try:return [dict(r) for r in c.execute(sql,args).fetchall()]
    finally:c.close()

def one(sql,args=()):
    result=rows(sql,args); return result[0] if result else None

def execute(sql,args=()):
    active=_ACTIVE.get()
    if active is not None:
        active.execute(sql,args);return
    c=connect()
    try:
        c.execute(sql,args); c.commit()
    finally:c.close()

def setting(key,default=None):
    r=one('SELECT value FROM settings WHERE key=?',(key,)); return loads(r['value'],default) if r else default

def set_setting(key,value): execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,dumps(value)))

def audit(project_id,action,detail): execute('INSERT INTO audit(project_id,action,detail,created_at) VALUES(?,?,?,?)',(project_id,action,dumps(detail),now()))


def atomic(function):
    @functools.wraps(function)
    def wrapped(*args,**kwargs):
        with transaction():return function(*args,**kwargs)
    return wrapped
