"""Isolated browser fixture. Never uses the production data directory or a worker."""
import os
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
expected = (ROOT / ".runtime/api-integration/browser-data").resolve()
if Path(os.environ.get("PAPER_DATA_DIR", "")).resolve() != expected:
    raise RuntimeError("This fixture requires .runtime/api-integration/browser-data")
runpy.run_path(str(ROOT / "runner.py"), run_name="runtime_setup")
from app import db, config, parsers, documents

db.init()
if not db.one("SELECT id FROM projects LIMIT 1"):
    pid = db.uid()
    db.execute("INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)",
               (pid, "接口整合 · 隔离验收", db.now(), db.now()))
    file, _ = parsers.register_file(pid, "测试原件.txt", "测试原件内容：接口需要统一返回格式。".encode("utf-8"), "research")
    parsers.parse_file(file["id"])
    sid = db.uid()
    db.execute("INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)", (sid, pid, "第一章 绪论", 0))
    documents.save(db.one("SELECT * FROM sections WHERE id=?", (sid,)), documents.text_document("隔离验收正文。"), None)
    db.execute("UPDATE tasks SET status='succeeded' WHERE project_id=?", (pid,))

import uvicorn
uvicorn.run("app.main:app", host="127.0.0.1", port=config.PORT, log_level="warning", access_log=False)
