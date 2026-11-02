"""Contract checks for the unified v2 API facade."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse

from app import config, db, expense_evidence, retrieval, security, provider, tasks
from app.main import app
import app.main as main


@pytest.fixture
def v2_client(tmp_path, monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(retrieval, "_QUERY_VECTORS", OrderedDict())
    monkeypatch.setattr(retrieval, "_RERANK_SCORES", OrderedDict())
    monkeypatch.setattr(
        expense_evidence,
        "DEFAULT_POLICY",
        {"require_pricing_basis": False, "block_on_unresolved": False},
    )
    for name in ("objects", "previews", "exports", "backups", "tmp", "logs"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workbench.sqlite3")
    for module in (config, security, main):
        monkeypatch.setattr(module, "DATA", tmp_path)
    monkeypatch.setattr(main, "PORT", 9999)
    monkeypatch.setattr(main, "ROOT", tmp_path)
    db.init()
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/system/session", json={"token": security.BOOTSTRAP_TOKEN}
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["data"]["csrf"]
        yield client


def test_v2_wraps_success_and_preserves_request_id(v2_client):
    response = v2_client.get("/api/v2/system/health")
    assert response.status_code == 200
    assert response.headers["X-API-Version"] == "v2"
    assert response.headers["X-Request-Id"] == response.json()["meta"]["request_id"]
    assert response.json()["data"]["status"] == "ok"


def test_v2_project_and_canonical_aliases(v2_client):
    created = v2_client.post("/api/v2/projects", json={"title": "v2 contract"})
    assert created.status_code == 200
    project_id = created.json()["data"]["id"]

    sessions = v2_client.get(f"/api/v2/projects/{project_id}/writing/sessions")
    assert sessions.status_code == 200
    assert sessions.json()["data"] == {"items": [], "next_cursor": None, "total": 0}

    sources = v2_client.get(f"/api/v2/projects/{project_id}/knowledge/sources")
    assert sources.status_code == 200
    assert "data" in sources.json()


def test_v2_errors_use_error_contract(v2_client):
    response = v2_client.get("/api/v2/projects/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["meta"]["api_version"] == "v2"


@pytest.mark.parametrize("path", ["/api/v2/projects", "/api/v2/model-profiles/primary"])
def test_authentication_errors_also_use_contract(v2_client, path):
    with TestClient(app) as raw:
        response = raw.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.json()["meta"]["request_id"] == response.headers["x-request-id"]


def test_csrf_and_origin_errors_are_wrapped(v2_client):
    csrf = v2_client.headers.pop("X-CSRF-Token")
    response = v2_client.post("/api/v2/projects", json={"title": "forbidden"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    v2_client.headers["X-CSRF-Token"] = csrf
    response = v2_client.get("/api/v2/projects", headers={"Origin": "https://evil.invalid"})
    assert response.status_code == 403
    assert "error" in response.json()
    assert not db.rows("SELECT * FROM projects")


def test_unknown_api_does_not_fall_back_to_html(v2_client):
    response = v2_client.get("/api/v2/no-such-interface")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_provider_outcome_never_suggests_retry(v2_client, monkeypatch):
    def unknown():
        raise provider.ProviderError("remote execution unknown", unknown=True)
    monkeypatch.setattr(provider, "account_usage", unknown)
    response = v2_client.post("/api/v2/provider-accounts/primary/usage")
    assert response.status_code == 502
    assert response.json()["error"]["unknown_outcome"] is True
    assert response.json()["error"]["retriable"] is False


def test_v1_contract_remains_compatible(v2_client):
    response = v2_client.get("/api/v1/projects")
    assert response.status_code == 200
    assert response.json() == []
    assert "data" not in v2_client.get("/api/v1/health").json()


def test_task_and_event_stream_keep_semantics(v2_client):
    pid = v2_client.post("/api/v2/projects", json={"title": "task"}).json()["data"]["id"]
    task = tasks.enqueue(pid, "review", {})
    response = v2_client.get("/api/v2/tasks/" + task["id"])
    assert response.json()["data"]["task"]["id"] == task["id"]
    assert response.json()["meta"]["kind"] == "task"
    v2_client.post("/api/v2/tasks/" + task["id"] + "/cancel")
    stream = v2_client.get("/api/v2/tasks/" + task["id"] + "/events")
    assert stream.status_code == 200
    assert "text/event-stream" in stream.headers["content-type"]
    assert "event: done" in stream.text
    assert stream.headers["x-request-id"]


def test_registry_and_openapi_agree_for_every_operation():
    registry = app.state.api_registry
    schema = registry.openapi()
    ids = []
    for binding in registry.bindings:
        if binding.method == "WEBSOCKET":
            continue
        operation = schema["paths"][binding.public_path][binding.method.lower()]
        ids.append(operation["operationId"])
        assert operation["x-legacy-path"] == binding.legacy_path
        import re
        path = re.sub(r"\{[^}]+\}", "fixture-id", binding.public_path)
        found, target = registry.resolve(path, binding.method)
        assert found is not None
        assert target == re.sub(r"\{[^}]+\}", "fixture-id", binding.legacy_path)
    assert len(ids) == len(set(ids))
    assert not any("{path:path}" in path for path in schema["paths"])


def test_generated_frontend_mappings_and_manifest_are_current():
    import json
    import re
    from pathlib import Path
    from app.collaboration import app as collaboration_app
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs/api-contract-v2.json").read_text(encoding="utf-8"))
    assert manifest["workbench"] == app.state.api_registry.manifest()
    assert manifest["collaboration"] == collaboration_app.state.api_registry.manifest()
    grouped = {}
    for binding in app.state.api_registry.bindings:
        old = binding.legacy_path.removeprefix("/api/v1")
        public = binding.public_path.removeprefix("/api/v2")
        if binding.canonical and re.sub(r"\{[^}]+\}", "{}", old) != re.sub(r"\{[^}]+\}", "{}", public):
            grouped.setdefault((old, public), []).append(binding.method)
    source = (root / "frontend/src/api/routes.generated.ts").read_text(encoding="utf-8")
    actual = json.loads(source.split(" = ", 1)[1].rstrip().removesuffix(";"))
    assert actual == [[old, public, methods] for (old, public), methods in grouped.items()]


def test_transport_preserves_downloads_headers_background_and_streams():
    from app.api.v2 import install
    mini = FastAPI()
    completed = []

    @mini.get("/api/v1/cookies")
    def cookies(request: Request):
        response = JSONResponse({"key": request.headers.get("idempotency-key")}, background=BackgroundTask(completed.append, "done"))
        response.set_cookie("first", "1")
        response.set_cookie("second", "2")
        return response

    @mini.get("/api/v1/download")
    def download():
        return Response(b'{"original":"document"}', media_type="application/json", headers={"Content-Disposition": "attachment; filename=source.json", "ETag": '"unchanged"'})

    @mini.get("/api/v1/stream")
    def stream():
        return StreamingResponse(iter([b"data: first\n\n", b"data: last\n\n"]), media_type="text/event-stream")

    @mini.get("/api/v1/fails")
    def fails():
        raise RuntimeError("fake-secret-must-not-leak")

    install(mini)
    with TestClient(mini) as c:
        reply = c.get("/api/v2/cookies", headers={"Idempotency-Key": "original-key", "X-Request-Id": "request-test"})
        assert len(reply.headers.get_list("set-cookie")) == 2
        assert reply.json()["data"]["key"] == "original-key"
        assert reply.json()["meta"]["request_id"] == "request-test"
        assert completed == ["done"]
        reply = c.get("/api/v2/download")
        assert reply.content == b'{"original":"document"}'
        assert reply.headers["etag"] == '"unchanged"'
        assert reply.headers["content-disposition"].endswith("source.json")
        assert c.get("/api/v2/stream").content == b"data: first\n\ndata: last\n\n"
        reply = c.get("/api/v2/fails")
        assert reply.status_code == 500
        assert "fake-secret" not in reply.text
        assert reply.json()["error"]["code"] == "INTERNAL_ERROR"


def test_json_validation_does_not_echo_submitted_secrets(v2_client):
    response = v2_client.post("/api/v2/projects", json="fake-secret-never-echo")
    assert response.status_code == 422
    assert "fake-secret-never-echo" not in response.text
    assert response.json()["error"]["details"]["fields"]


def test_collaboration_uses_separate_auth_for_http_and_websocket(v2_client, monkeypatch):
    from app import collaboration
    monkeypatch.setenv("PAPER_COLLAB_INSECURE", "0")
    collaboration.create_user("contract-admin", "fake-contract-password-1234", True)
    with TestClient(collaboration.app, base_url="https://testserver") as c:
        response = c.get("/api/v2/collaboration/projects")
        assert response.status_code == 401
        assert response.json()["meta"]["service"] == "collaboration"
        login = c.post("/api/v2/collaboration/auth/login", json={"username": "contract-admin", "password": "fake-contract-password-1234"})
        assert login.status_code == 200
        c.headers["X-CSRF-Token"] = login.json()["data"]["csrf"]
        project = c.post("/api/v2/collaboration/projects", json={"title": "isolated collaboration"})
        pid = project.json()["data"]["id"]
        assert c.get(f"/api/v2/projects/{pid}/collaboration/snapshot").status_code == 200
        with c.websocket_connect(f"wss://testserver/api/v2/projects/{pid}/collaboration/live?after=0") as ws:
            assert ws.receive_json()["kind"] == "heartbeat"
        assert c.get("/api/v2/model-profiles/primary").status_code == 404

