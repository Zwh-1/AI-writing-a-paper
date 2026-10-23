"""Local extraction diagnostics; extraction and human approval are independent."""
from . import db
PARSER_VERSION = "0.3.1"

def key(fid): return "material_parse:" + fid

def save(fid, report):
    report["parser_version"] = PARSER_VERSION
    report["updated_at"] = db.now()
    db.set_setting(key(fid), report)

def summary(file, include_pages=False):
    stored = db.setting(key(file["id"]), {})
    if stored.get("sha256") != file["sha256"]: stored = {}
    states = stored.get("page_states", {})
    pages = db.rows("SELECT id,page_number,has_visual,review_state,length(trim(text)) AS chars FROM pages WHERE file_id=? ORDER BY page_number", (file["id"],))
    ocr_pages = {r["page_id"] for r in db.rows("SELECT DISTINCT page_id FROM sources WHERE file_id=? AND method='windows_ocr'", (file["id"],))}
    details = []
    for page in pages:
        state = dict(states.get(page["id"], {}))
        state.update({k:page[k] for k in ("id", "page_number", "review_state", "chars")})
        if "ocr_state" not in state:
            state["ocr_state"] = "legacy_ocr" if page["id"] in ocr_pages else "pending" if page["has_visual"] else "not_needed"
        state.setdefault("errors", [])
        state["machine_ocr_state"] = state["ocr_state"]
        if page["review_state"] in ("reviewed", "not_applicable") and state["ocr_state"] not in ("complete", "no_text", "not_needed"):
            state["ocr_state"] = "reviewed" if page["review_state"] == "reviewed" else "excluded"
        details.append(state)
    pending = sum(p["review_state"] == "unreviewed" and p["ocr_state"] in ("pending", "running", "legacy_ocr") for p in details)
    failed = sum(p["review_state"] == "unreviewed" and (p["ocr_state"] in ("failed", "partial", "unavailable") or bool(p["errors"])) for p in details)
    completed = sum(p["machine_ocr_state"] in ("complete", "no_text") for p in details)
    excluded = sum(p["review_state"] == "not_applicable" for p in details)
    reviewed = sum(p["review_state"] == "reviewed" for p in details)
    if file["status"] in ("queued", "parsing", "failed"):
        state = file["status"]
    elif pending: state = "ocr_pending"
    elif failed: state = "partial"
    else: state = "extracted"
    labels = {"queued":"等待解析", "parsing":"正在解析", "failed":"解析失败", "ocr_pending":"待补图片识别", "partial":"解析有缺项", "extracted":"内容已提取"}
    result = {"state":state, "label":labels[state], "pages":len(pages), "ocr_pending_pages":pending, "ocr_failed_pages":failed, "ocr_completed_pages":completed, "reviewed_pages":reviewed, "excluded_pages":excluded, "unreviewed_pages":len(pages)-reviewed-excluded, "sparse_pages":sum(p["chars"] < 40 for p in details), "parser_version":stored.get("parser_version", "legacy"), "repair_needed":state in ("failed", "ocr_pending", "partial") or (file["extension"] in (".pptx", ".pdf", ".docx") and stored.get("native_version") != PARSER_VERSION), "external_requests":False, "updated_at":stored.get("updated_at")}
    if include_pages: result["page_states"] = details
    return result
