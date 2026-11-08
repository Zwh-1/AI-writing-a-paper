"""Read-only runtime readiness; dependency failures are reported without private paths."""
from functools import lru_cache
from contextlib import closing
from importlib import import_module
from pathlib import Path
import sqlite3
from .. import db


@lru_cache(maxsize=16)
def dependency(module, attribute):
    try:
        value = getattr(import_module(module), attribute)
        return {'available': value is not None}
    except Exception as error:
        return {'available': False, 'error_type': type(error).__name__}


def status():
    dependencies = {name: dependency(*spec) for name, spec in {
        'runnable': ('langchain_core.runnables', 'RunnableLambda'),
        'tools': ('langchain_core.tools', 'StructuredTool'),
        'retriever': ('langchain_core.retrievers', 'BaseRetriever'),
        'graph': ('langgraph.graph', 'StateGraph'),
        'sqlite_saver': ('langgraph.checkpoint.sqlite', 'SqliteSaver'),
        'project_memory':('app.project_memory','validate_context'),
        'hierarchical_context':('app.writing_continuity','collect'),
        'skill_loading':('app.skill_runtime','load'),
        'tracing_control': ('langsmith', 'tracing_context')}.items()}
    checkpoint = Path(db.DB_PATH).parent / 'ai-graph-checkpoints.sqlite3'
    checkpoint_status = {'status': 'not_created', 'write_verified': False}
    if checkpoint.exists():
        try:
            with closing(sqlite3.connect(checkpoint.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as connection:
                connection.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
            checkpoint_status['status'] = 'readable'
        except (sqlite3.Error, OSError) as error:
            checkpoint_status.update(status='unreadable', error_type=type(error).__name__)
    elif not checkpoint.parent.is_dir():
        checkpoint_status['status'] = 'directory_missing'
    available = lambda name: dependencies[name]['available']
    tracing = available('tracing_control')
    capabilities = {'gateway': available('runnable') and tracing,
        'project_memory':available('project_memory'),'hierarchical_context':available('hierarchical_context'),
        'skill_loading':available('skill_loading'),
        'chapter_graph': available('graph') and available('sqlite_saver') and tracing and
                         checkpoint_status['status'] in ('readable', 'not_created'),
        'obligations': True, 'structured_tools': available('tools') and tracing,
        'source_preserving_retriever': available('retriever') and tracing,
        'independent_checks_parallel': True, 'automatic_paid_evaluation': False}
    return {'ready': all(value for key, value in capabilities.items() if key != 'automatic_paid_evaluation'),
            'dependencies': dependencies, 'checkpoint': checkpoint_status, 'capabilities': capabilities,
            'integrations': {'mcp': {'available': False, 'status': 'not_implemented',
                'registered_servers': 0, 'external_tools_enabled': False,
                'reason': '现有工具使用宿主注册表；尚无MCP协议传输或客户端适配器'}},
            'note': '依赖按当前进程实际加载结果检查；检查点只读检查，不代表已验证写入。'}
