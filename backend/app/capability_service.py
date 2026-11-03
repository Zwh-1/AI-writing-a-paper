"""Configuration/capability discovery shared by web, desktop and deployment modes."""
import shutil
from . import db, security

def status(pid):
    security.require_project(pid)
    from . import parsing_service, execution_service, exports
    parsing=parsing_service.settings(pid);execution=execution_service.settings(pid)
    from .research_relations import backfill
    migration=backfill(pid)
    provider=parsing['provider'];selected=parsing['providers'].get(provider,{})
    return {'project_id':pid,'schema_version':1,'records_migration':migration,
        'parsing':{'provider':provider,'configured':bool(selected.get('url')) and (provider=='surya-local' or selected.get('key_configured',False)), 'original_preview':True},
        'execution':{'provider':execution['provider'],'configured':execution.get('key_configured',False) if execution['provider']=='remote-http' else execution.get('plot_image_ready',False)},
        'exports':{'formats':['docx','markdown','latex'],'pdf_renderer':bool(exports.renderer()),'tex_compiler':bool(shutil.which('xelatex') or shutil.which('pdflatex'))},
        'sync':{'complete_bundle':True,'research_incremental':True,'legacy_incremental':False},
        'search':{'current_parse':True,'derived_scope':True,'offline_mode':'current_content_keyword'},
        'credential_scope':'device_encrypted_not_exported'}
