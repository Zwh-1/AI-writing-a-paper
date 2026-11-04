"""Scoped conservative costs include every touched closed billing group."""
from .. import db,budget_usage

SCOPES={'writing_session_id','diagnostic_run_id','diagnostic_comparison_id'}

def scope_charge(field,identity):
    if field not in SCOPES:raise ValueError('Unknown writing budget scope')
    projects=db.rows("SELECT DISTINCT project_id FROM generation_runs WHERE json_valid(manifest) AND json_extract(manifest,?)=?",('$.'+field,identity))
    if len(projects)>1:raise ValueError('Writing budget scope spans multiple projects')
    return budget_usage.charge(projects[0]['project_id'],manifest_scope={field:identity}) if projects else 0.0
