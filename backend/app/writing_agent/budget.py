"""Scoped conservative costs include every touched closed billing group."""
from .. import db

SCOPES={'writing_session_id','diagnostic_run_id','diagnostic_comparison_id'}

def scope_charge(field,identity):
    if field not in SCOPES:raise ValueError('Unknown writing budget scope')
    where="json_extract(g.manifest,?)=? AND g.status NOT IN ('rejected','failed_before_send')";args=('$.'+field,identity)
    direct=db.one('SELECT COALESCE(SUM(COALESCE(g.cost,g.reserved)),0) n FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE '+where+' AND m.run_id IS NULL',args)['n']
    grouped=db.one('SELECT COALESCE(SUM(amount),0) n FROM generation_billing_groups WHERE id IN (SELECT DISTINCT m.group_id FROM generation_runs g JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE '+where+')',args)['n']
    return direct+grouped
