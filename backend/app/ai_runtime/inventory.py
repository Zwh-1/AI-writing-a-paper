"""Public source-only generation entry inventory; never examines user content."""
import ast
from pathlib import Path

ENTRIES = (
    ('chapter', '普通章节、扩写和修改', 'tasks.py', 'writing_flow.py'),
    ('agent', '有据写作', 'writing_agent/coordinator.py', 'writing_agent/provider_adapter.py'),
    ('paper', '整篇与全篇修订', 'paper_writing.py', 'writing_agent/paper_pipeline.py'),
    ('rules', '规则抽取与复核', 'rule_ai.py', 'rule_pipeline.py'),
    ('summary', '资料总结', 'material_summary.py'),
    ('research', '研究、阅读与问答', 'knowledge_router.py', 'assistant_service.py'),
    ('visuals', '翻译、润色与图表', 'studio_ai.py', 'writing_visuals.py', 'abstract_translation.py','image_generation.py'),
)


def scan(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    calls = []
    for path in sorted(root.rglob('*.py')):
        if path.is_symlink():
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name.endswith(('tasks.model_call', 'provider_adapter.call', 'provider.generate', 'provider.pool_generate')) or name == 'model_call':
                calls.append({'file': path.relative_to(root).as_posix(), 'line': node.lineno, 'call': name,
                              'transport_boundary': name.endswith(('provider.generate', 'provider.pool_generate'))})
    return {'schema': 'ai.entry.inventory.v1',
            'tool_protocols': {'host_registry': 'writing_agent.tools.SPECS',
                'mcp': {'implemented': False, 'registered_servers': 0,
                        'note': '宿主工具和LangChain StructuredTool封装不等于MCP协议支持'}},
            'dedicated_adapters':[{'module':'image_generation.py','protocol':'images',
                'execution':'existing_dedicated_images_transport_and_budget_ledger'}],
            'entries': [{'id': item[0], 'label': item[1], 'modules': list(item[2:])} for item in ENTRIES],
            'call_sites': calls, 'note': '静态调用清单；条件分支与运行覆盖仍需契约测试验证。'}
