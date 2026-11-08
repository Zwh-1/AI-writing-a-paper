"""Router ownership and mount order for the legacy handlers behind API v2."""
from importlib import import_module

DOMAIN_ROUTERS = (
    "academic_standards",
    "skill_runtime",
    "project_memory",
    "statistical_charts",
    "context_maintenance",
    "ai_runtime.api",
    "deepseek_profiles", "ai_cache_policy", "runtime_api", "request_receipts", "execution_policy", "research_api", "source_workspace", "expense_evidence", "writing_agent.api",
    "paper_writing", "paper_study", "writing_ops", "writing_live", "writing_retrieval_api", "writing_flow_api", "ai_autopilot_api",
    "reference_library", "reference_knowledge", "knowledge_units", "knowledge_imports",
    "knowledge_evaluation", "knowledge_api", "document_reading", "rule_rebuild",
    "literature_refresh", "paper_visuals", "image_generation", "figure_standards",
)
SUPPLEMENT_ROUTERS = ("completion", "extras", "advanced")


def mount(app, *, supplements=False):
    for name in SUPPLEMENT_ROUTERS if supplements else DOMAIN_ROUTERS:
        module = import_module("app." + name)
        app.include_router(module.router, prefix="/api/v1")
