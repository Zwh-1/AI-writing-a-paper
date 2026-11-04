"""One mapping table drives runtime routing, API documentation and audit exports.

Handlers remain in their current modules; canonical paths express their actual
business domains. Legacy paths stay available for existing scripts.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
import re

from fastapi.openapi.utils import get_openapi
from fastapi.routing import iter_route_contexts
from starlette.routing import compile_path


PARAMETERS = {"pid": "project_id", "fid": "file_id", "sid": "section_id", "tid": "task_id",
              "rid": "record_id", "aid": "artifact_id", "cid": "candidate_id", "eid": "entry_id",
              "bid": "backup_id", "uid": "unit_id", "did": "dispute_id", "identity": "resource_id",
              "sha": "sha256"}
WRITING = {"writing-state": "state", "writing-link": "model-link", "writing-bootstrap": "bootstrap",
    "writing-start": "start", "writing-preflight": "preflight", "writing-sessions": "sessions",
    "writing-candidates": "candidates", "writing-adoptions": "adoptions", "writing-profiles": "profiles",
    "writing-joint-reviews": "joint-reviews", "writing-joint-candidates": "joint-candidates",
    "writing-analyses": "analyses", "writing-coverage": "coverage", "writing-reviews": "reviews",
    "writing-export-report": "export-report", "writing-stale-reasons": "stale-reasons",
    "writing-checks": "checks", "writing-billing": "billing", "writing-quality-policies": "quality-policies",
    "writing-operations": "operations", "writing-live": "live", "writing-retrieval": "retrieval",
    "writing-flow": "flow", "writing-plan": "argument-plan", "writing-quality": "quality", "diagnostics": "diagnostics",
    "diagnostic-dataset": "diagnostic-dataset", "diagnostic-rubric": "diagnostic-rubric",
    "diagnostic-comparisons": "diagnostic-comparisons", "paper-writing": "papers"}
PROFILES = {"/settings": "/model-profiles/primary", "/material-summary/settings": "/model-profiles/material-summary",
    "/ai-cache-policy": "/model-providers/cache-policy",
    "/gpt-fallback/settings": "/model-profiles/gpt-fallback", "/image-generation/settings": "/model-profiles/image-generation",
    "/provider/models": "/model-profiles/primary/models", "/material-summary/models": "/model-profiles/material-summary/models",
    "/gpt-fallback/models": "/model-profiles/gpt-fallback/models", "/provider/probe-generation": "/model-profiles/primary/probes/text",
    "/provider/probe-image": "/model-profiles/primary/probes/vision", "/gpt-fallback/probe": "/model-profiles/gpt-fallback/probes/text",
    "/provider/account-usage": "/provider-accounts/primary/usage", "/provider/account-usage/history": "/provider-accounts/primary/usage/history"}


def canonical_suffix(suffix, service):
    if service == "collaboration":
        if suffix in ("/login", "/session", "/logout"):
            return "/collaboration/auth" + suffix
        if suffix.startswith("/users"):
            return "/collaboration" + suffix
        match = re.match(r"^(/projects/\{pid\})(.*)$", suffix)
        return match[1] + "/collaboration" + match[2] if match else "/collaboration" + suffix
    if suffix in ("/health", "/session"):
        return "/system" + suffix
    if suffix in PROFILES:
        return PROFILES[suffix]
    if suffix.startswith("/deepseek/"):
        return "/model-providers/deepseek/" + suffix[len("/deepseek/"):]
    if suffix.startswith("/deepseek-pool/"):
        return "/model-providers/deepseek/pool/" + suffix[len("/deepseek-pool/"):]
    match = re.match(r"^(/projects/\{pid\}/)([^/]+)(.*)$", suffix)
    if match:
        prefix, resource, tail = match.groups()
        if resource == 'studio':
            capability = tail.lstrip('/').split('/',1)[0]
            routes = {'library':'literature/library','imports':'literature/imports','tags':'literature/tags','zotero':'literature/zotero',
                'openalex':'literature/discovery','abstract':'literature/abstract','reader':'reading/parallel','webpages':'reading/webpages',
                'records':'research-records','assets':'asset-library','conversations':'assistant/conversations','notes':'notes',
                'completion':'writing/completion','comments':'writing/comments','proposals':'writing/proposals','canvas':'assets/canvases',
                'exports':'exports/formats','deletion':'literature/deletion','parsing':'parsing/providers','parse-results':'parsing/results',
                'credentials':'integrations/credentials','metadata-graph':'knowledge/metadata-graph','execution':'execution','status':'research/status'}
            if capability in routes:
                return prefix + routes[capability] + tail[len('/'+capability):]
        if resource in WRITING:
            return prefix + "writing/" + WRITING[resource] + tail
        domains = {"evidence-knowledge": "knowledge/sources", "knowledge-base": "knowledge/search-index",
                   "document-reading": "reading", "paper-visuals": "visuals", "expenses": "billing/entries",
                   "ai-autopilot": "automation", "source-workspace": "workspace"}
        if resource in domains:
            return prefix + domains[resource] + tail
    return suffix


def parameter_names(path):
    names = dict(PARAMETERS)
    resources = {"files": "file_id", "pages": "page_id", "sections": "section_id", "rules": "rule_id",
        "references": "reference_id", "tasks": "task_id", "snapshots": "snapshot_id", "revisions": "revision_id",
        "usage": "run_id", "streams": "run_id", "findings": "finding_id", "facts": "fact_id",
        "citations": "citation_id", "conflicts": "conflict_id", "repositories": "repository_id",
        "users": "user_id", "evidence": "evidence_id", "claims": "claim_id", "exports": "export_id"}
    for resource, param in re.findall(r"/([^/]+)/\{(\w+)\}", path):
        if resource in resources:
            names[param] = resources[resource]
    return names


def public_suffix(suffix, service, method):
    result = canonical_suffix(suffix, service)
    prefix, _, action = result.rpartition("/")
    if method == "POST" and action in {"cancel", "resume", "retry", "acknowledge", "activate", "accept", "restore", "publish", "archive", "unarchive", "approve-judgment"}:
        result = prefix + "/actions/" + action
    return result


def rename_parameters(path, names):
    return re.sub(r"\{(\w+)(:[^}]+)?\}", lambda m: "{" + names.get(m[1], m[1]) + (m[2] or "") + "}", path)


def domain(path):
    segments = path.split("/")[3:]
    if "collaboration" in segments:
        return "collaboration"
    if segments[:1] == ["projects"] and len(segments) > 2:
        return segments[2]
    return segments[0] if segments else "system"


@dataclass
class Binding:
    public_path: str
    legacy_path: str
    method: str
    canonical: bool
    module: str
    transport: str = "json"

    def __post_init__(self):
        self.regex, _, _ = compile_path(self.public_path)
        self.names = parameter_names(self.legacy_path)

    def target(self, values):
        return re.sub(r"\{(\w+)(:[^}]+)?\}", lambda m: values.get(self.names.get(m[1], m[1]), values.get(m[1], "")), self.legacy_path)


class ApiRegistry:
    def __init__(self, app, *, service, legacy_prefix):
        self.app, self.service, self.legacy_prefix = app, service, legacy_prefix
        self.legacy_schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        self.bindings = []
        seen = set()
        for ctx in iter_route_contexts(app.routes):
            path = ctx.path or ""
            if not path.startswith(legacy_prefix + "/"):
                continue
            suffix = path[len(legacy_prefix):]
            compat = "/api/v2" + suffix
            for method in sorted(ctx.methods or {"WEBSOCKET"}):
                public = "/api/v2" + rename_parameters(public_suffix(suffix, service, method), parameter_names(path))
                aliases = [(public, True)]
                if re.sub(r"\{[^}]+\}", "{}", public) != re.sub(r"\{[^}]+\}", "{}", compat):
                    aliases.append((compat, False))
                for target, canonical in aliases:
                    key = (method, target)
                    if key in seen:
                        continue
                    seen.add(key)
                    module = getattr(ctx.endpoint, "__module__", "")
                    try:
                        source = inspect.getsource(inspect.unwrap(ctx.endpoint))
                    except (OSError, TypeError):
                        source = ""
                    transport = "events" if "text/event-stream" in source else "file" if any(term in source for term in ("FileResponse(", "StreamingResponse(", "Content-Disposition", "content-disposition")) else "json"
                    self.bindings.append(Binding(target, path, method, canonical, module, transport))
        # Fixed routes must precede placeholders, e.g. graph/search vs {resource_id}.
        self.bindings.sort(key=lambda b: (b.public_path.count("{"), -len(b.public_path)))
        self._schema = None

    def resolve(self, path, method):
        for binding in self.bindings:
            if binding.method != method:
                continue
            match = binding.regex.fullmatch(path)
            if match:
                return binding, binding.target(match.groupdict())
        return None, self.legacy_prefix + path[len("/api/v2"):]

    def public_url(self, value):
        if not isinstance(value, str) or not value.startswith(self.legacy_prefix + "/"):
            return value
        path, sep, query = value.partition("?")
        for binding in self.bindings:
            if not binding.canonical:
                continue
            regex, _, _ = compile_path(binding.legacy_path)
            match = regex.fullmatch(path)
            if match:
                result = re.sub(r"\{(\w+)(:[^}]+)?\}", lambda m: next((v for k, v in match.groupdict().items() if binding.names.get(k, k) == m[1]), ""), binding.public_path)
                return result + (sep + query if sep else "")
        return value

    def rewrite_links(self, value):
        # Only transport link fields are rewritten; manuscript/source strings are untouched.
        if isinstance(value, list):
            return [self.rewrite_links(v) for v in value]
        if isinstance(value, dict):
            return {k: self.public_url(v) if k in ("download", "csv_download", "href", "preview_url", "content_url") and isinstance(v, str)
                    else self.rewrite_links(v) for k, v in value.items()}
        return value

    def openapi(self):
        if self._schema is not None:
            return self._schema
        schema = deepcopy(self.legacy_schema)
        schema["paths"] = {}
        for binding in self.bindings:
            method = binding.method.lower()
            if method == "websocket":
                continue
            operation = deepcopy(self.legacy_schema.get("paths", {}).get(binding.legacy_path, {}).get(method))
            if operation is None:
                continue
            operation["operationId"] = "v2_" + hashlib.sha256((binding.method + binding.public_path).encode()).hexdigest()[:20]
            operation["tags"] = [domain(binding.public_path)]
            operation["x-legacy-path"] = binding.legacy_path
            operation["x-owner-module"] = binding.module
            operation["x-response-transport"] = binding.transport
            operation.setdefault("parameters", []).append({"name": "X-Request-Id", "in": "header", "required": False, "schema": {"type": "string", "maxLength": 128}})
            if not binding.canonical:
                operation["deprecated"] = True
            for param in operation.get("parameters", []):
                if param.get("in") == "path" and binding.canonical:
                    param["name"] = binding.names.get(param["name"], param["name"])
            for status, response in operation.get("responses", {}).items():
                if str(status).startswith("2") and binding.transport != "json":
                    response["content"] = {"text/event-stream" if binding.transport == "events" else "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}
                    continue
                content = response.get("content", {})
                if "application/json" not in content:
                    continue
                original = content["application/json"].get("schema", {})
                if str(status).startswith("2"):
                    if original.get("type") == "array":
                        original = {"type": "object", "properties": {"items": original, "next_cursor": {"type": ["string", "null"]}, "total": {"type": "integer"}}}
                    props = {"data": original, "meta": {"$ref": "#/components/schemas/ApiMeta"}}
                else:
                    props = {"data": {"type": "null"}, "error": {"$ref": "#/components/schemas/ApiError"}, "meta": {"$ref": "#/components/schemas/ApiMeta"}}
                content["application/json"]["schema"] = {"type": "object", "required": list(props), "properties": props}
            for status in ("401", "403", "404", "405", "409", "422", "500", "502"):
                operation["responses"].setdefault(status, {"description": "统一错误响应", "content": {"application/json": {"schema": {
                    "type": "object", "required": ["data", "error", "meta"], "properties": {"data": {"type": "null"}, "error": {"$ref": "#/components/schemas/ApiError"}, "meta": {"$ref": "#/components/schemas/ApiMeta"}}}}}})
            schema["paths"].setdefault(binding.public_path, {})[method] = operation
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas["ApiMeta"] = {"type": "object", "required": ["request_id", "api_version", "service"], "properties": {"request_id": {"type": "string"}, "api_version": {"const": "v2"}, "service": {"type": "string"}, "kind": {"type": "string"}, "generated_at": {"type": "string", "format": "date-time"}}}
        schemas["ApiError"] = {"type": "object", "required": ["code", "message", "retriable", "unknown_outcome"], "properties": {"code": {"type": "string"}, "message": {"type": "string"}, "details": {}, "retriable": {"type": "boolean"}, "unknown_outcome": {"type": "boolean"}}}
        schema["info"]["description"] = "v2 统一契约；deprecated 路径是迁移兼容入口。文件下载和事件流保持原协议。"
        self._schema = schema
        return schema

    def manifest(self):
        return [{"method": b.method, "path": b.public_path, "legacy_path": b.legacy_path,
                 "canonical": b.canonical, "domain": domain(b.public_path), "owner": b.module, "transport": b.transport}
                for b in self.bindings]
