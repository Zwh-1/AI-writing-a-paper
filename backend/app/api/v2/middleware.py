"""ASGI response adapter: preserve raw headers, downloads and streaming frames."""
import json
import logging

from starlette.responses import JSONResponse
from .contracts import failure, request_id, success

logger = logging.getLogger(__name__)


class ApiContractMiddleware:
    def __init__(self, app, registry):
        self.app, self.registry = app, registry

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        v2 = path == "/api/v2" or path.startswith("/api/v2/")
        if scope["type"] not in ("http", "websocket") or not v2:
            return await self.app(scope, receive, send)
        original_scope = scope
        scope = dict(scope)
        scope["state"] = dict(scope.get("state", {}))
        rid = request_id(scope.get("headers", []))
        scope["state"].update(request_id=rid, api_version="v2", public_path=path)
        binding, target = self.registry.resolve(path, scope.get("method", "WEBSOCKET"))
        scope["path"], scope["raw_path"] = target, target.encode("utf-8")
        if scope["type"] == "websocket":
            return await self.app(scope, receive, send)

        started = None
        chunks = []
        wrapped = False
        sent = False

        async def adapted_send(message):
            nonlocal started, wrapped, sent
            if message["type"] == "http.response.start":
                started = dict(message)
                headers = [(k, v) for k, v in message["headers"] if k.lower() not in (b"x-request-id", b"x-api-version")]
                headers += [(b"x-request-id", rid.encode("ascii")), (b"x-api-version", b"v2")]
                started["headers"] = headers
                ctype = next((v.lower() for k, v in headers if k.lower() == b"content-type"), b"")
                attachment = any(k.lower() == b"content-disposition" for k, _ in headers)
                wrapped = (b"application/json" in ctype or b"+json" in ctype) and not attachment and scope.get("method") != "HEAD" and message["status"] not in (204, 304)
                if not wrapped:
                    sent = True
                    await send(started)
                return
            if message["type"] != "http.response.body" or not wrapped:
                return await send(message)
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            raw = b"".join(chunks)
            try:
                data = json.loads(raw)
                payload = failure(data, started["status"], rid, self.registry.service) if started["status"] >= 400 else success(data, rid, self.registry.service, self.registry)
                raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                headers = [(k, v) for k, v in started["headers"] if k.lower() not in (b"content-type", b"content-length", b"etag", b"content-md5")]
                headers += [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(raw)).encode())]
                started["headers"] = headers
            except (ValueError, UnicodeDecodeError):
                pass
            sent = True
            await send(started)
            await send({"type": "http.response.body", "body": raw})

        try:
            await self.app(scope, receive, adapted_send)
        except Exception:
            if sent:
                raise
            # Log traceback locally; never include passwords, source text or keys in the response.
            logger.exception("API request failed request_id=%s", rid)
            response = JSONResponse(failure({"detail": "本机服务处理失败，请凭请求编号查看日志", "outcome_unknown": scope.get("method") not in ("GET", "HEAD", "OPTIONS")}, 500, rid, self.registry.service), status_code=500,
                                    headers={"X-Request-Id": rid, "X-API-Version": "v2"})
            await response(original_scope, receive, send)
