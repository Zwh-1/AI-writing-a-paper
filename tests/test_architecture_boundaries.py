"""Small dependency checks prevent the architectural breaks fixed in this change."""
import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_worker_does_not_import_http_routes_or_studio_compatibility_modules():
    for name in ('tasks.py','research_tasks.py','knowledge_compiler.py','parsers.py','content_view.py'):
        path=ROOT/'backend/app'/name
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                assert node.module not in ('studio_api','research_api'),(name,node.lineno)
                assert not (node.module or '').startswith('studio_'),(name,node.lineno)
            if isinstance(node,ast.Import):
                assert not any(alias.name.startswith('app.studio_') for alias in node.names)

def test_domain_components_are_independent_from_studio_pages():
    for domain in ('assistant','assets','editor','reading','settings'):
        for path in (ROOT/'frontend/src'/domain).glob('*.*'):
            assert "from '../studio/" not in path.read_text(encoding='utf-8-sig'),path.name

def test_all_research_record_kinds_have_contracts_and_resource_adapters():
    from app.research_contracts import MODELS
    from app.research_records import KINDS
    from app.resource_refs import RECORD_TYPES
    assert set(MODELS)==KINDS==RECORD_TYPES

def test_portable_and_docker_include_shared_cli_projection():
    for name in ('scripts/package.py','deployment/workbench.Dockerfile'):
        assert 'scripts/yantai_query_projection.py' in (ROOT/name).read_text(encoding='utf-8-sig')
