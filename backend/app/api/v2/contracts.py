"""JSON contracts shared by the desktop and the independent collaboration app."""
from datetime import datetime, timezone
import re
import uuid


STATUS_CODES = {
    400: "INVALID_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN",
    404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED",
    500: "INTERNAL_ERROR", 502: "UPSTREAM_ERROR", 503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def request_id(headers):
    candidate = next((v.decode("latin-1") for k, v in headers if k.lower() == b"x-request-id"), "")
    return candidate if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate) else "req_" + uuid.uuid4().hex


def meta(rid, service):
    return {"request_id": rid, "api_version": "v2", "service": service,
            "generated_at": datetime.now(timezone.utc).isoformat()}


def failure(body, status, rid, service):
    detail = body.get("detail", body) if isinstance(body, dict) else body
    code = STATUS_CODES.get(status, "REQUEST_FAILED")
    unknown = bool(body.get("outcome_unknown", False)) if isinstance(body, dict) else False
    if isinstance(detail, dict):
        code = detail.get("code") or code
        message = detail.get("message") or detail.get("detail") or code
        details = {k: v for k, v in detail.items() if k not in ("message", "detail")}
        unknown = unknown or bool(detail.get("outcome_unknown", detail.get("unknown_outcome", False)))
    elif isinstance(detail, list):
        message = "请求参数不符合接口约定"
        # FastAPI validation errors may include the submitted password/API key.
        details = {"fields": [{k: v for k, v in item.items() if k in ("loc", "msg", "type")}
                              for item in detail if isinstance(item, dict)]}
    else:
        message = str(detail) if detail is not None else code
        details = None
    if isinstance(body, dict):
        extras = {k: v for k, v in body.items() if k not in ("detail", "outcome_unknown")}
        if extras:
            details = {**(details or {}), **extras}
    # Unknown execution is never safe to resend merely because the HTTP status is 5xx.
    return {"data": None, "error": {"code": code, "message": str(message), "details": details,
            "retriable": not unknown and status in (429, 503), "unknown_outcome": unknown},
            "meta": meta(rid, service)}


def success(body, rid, service, registry):
    metadata = meta(rid, service)
    body = registry.rewrite_links(body)
    if isinstance(body, list):
        metadata["kind"] = "collection"
        body = {"items": body, "next_cursor": None, "total": len(body)}
    elif isinstance(body, dict) and all(k in body for k in ("id", "kind", "status", "progress")):
        metadata["kind"] = "task"
        task_id = body["id"]
        body = {"task": body, "links": {"self": f"/api/v2/tasks/{task_id}",
                "events": f"/api/v2/tasks/{task_id}/events"}}
    return {"data": body, "meta": metadata}
