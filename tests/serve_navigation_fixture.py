"""Local navigation acceptance fixture; never opens the user's project database."""
import os
import runpy
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".runtime" / "sidebar-workspaces-20261102"
OUTPUT.mkdir(parents=True, exist_ok=True)
os.environ["PAPER_DATA_DIR"] = tempfile.mkdtemp(
    prefix="sidebar-navigation-data-", dir=ROOT / ".runtime"
)
os.environ.setdefault("PAPER_PORT", "8898")
os.environ["PAPER_SESSION_DIR"] = str(OUTPUT)
runpy.run_path(str(ROOT / "runner.py"), run_name="runtime_setup")

from app import config, db, documents, parsers, security, tasks  # noqa: E402

db.init()
projects = []
for index, name in enumerate(("导航回归 · 项目甲", "导航回归 · 项目乙"), 1):
    pid, sid, stamp = db.uid(), db.uid(), db.now()
    db.execute(
        "INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)",
        (pid, name, stamp, stamp),
    )
    db.execute(
        "INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)",
        (sid, pid, f"项目{index}的方法与讨论", 0),
    )
    text = f"这是导航验收项目{index}的独立正文，仅用于本地页面切换验证。"
    documents.save(security.require_section(sid), documents.text_document(text), None)
    original, _ = parsers.register_file(
        pid,
        f"navigation-source-{index}.txt",
        f"Navigation fixture {index}. Synthetic source for local routing checks.".encode(),
        "research",
    )
    parsers.parse_file(original["id"])
    projects.append(
        {"id": pid, "title": name, "section_id": sid, "file_id": original["id"], "text": text}
    )


def reject_model_call(*args, **kwargs):
    raise AssertionError("Navigation acceptance must not call a model")


tasks.model_call = reject_model_call
(OUTPUT / "fixture.json").write_text(
    db.dumps({"projects": projects, "port": config.PORT, "data_directory": str(config.DATA)}),
    encoding="utf-8",
)

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=config.PORT, log_level="warning", access_log=False)
