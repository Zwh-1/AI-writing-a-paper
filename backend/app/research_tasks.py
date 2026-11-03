"""Worker entry points. This module must never import an HTTP router."""
from . import literature_connectors, assistant_service, parsing_service, research_delivery


def process(task, payload):
    action = payload.get('action')
    if action == 'import_url':
        return literature_connectors.import_url(task, payload)
    if action == 'zotero':
        return literature_connectors.zotero_sync(task, payload)
    if action == 'parse':
        return parsing_service.parse(task, payload)
    if action == 'latex':
        from .export_service import generate
        return generate(task['project_id'], {**payload, 'format': 'latex'}, task['id'])
    if action == 'metadata_graph':
        from .knowledge_metadata import enrich_project
        return enrich_project(task['project_id'])
    if action in ('execute', 'install_execution'):
        from . import execution_service
        return execution_service.execute(task, payload) if action == 'execute' else execution_service.install(task)
    return assistant_service.process(task, payload)
