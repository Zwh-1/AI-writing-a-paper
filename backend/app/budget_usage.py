"""One conservative scope charge: direct runs plus each touched bill group once."""
from decimal import Decimal, InvalidOperation
import math
import re

from . import db

EXCLUDED=('rejected','failed_before_send')


def _scope(value):
    if value is None:return {}
    if isinstance(value,tuple) and len(value)==2:value={value[0]:value[1]}
    if not isinstance(value,dict):raise ValueError('Budget manifest scope must be a field/value mapping')
    if any(not isinstance(name,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name) for name in value):
        raise ValueError('Invalid budget manifest scope field')
    return value


def _amount(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('Budget amount is unknown or invalid; retained charges cannot be discarded')
    return Decimal(str(value))


def _charge(pid,task_ids=None,manifest_scope=None,task_or_scope=False,exclude_run_ids=()):
    scope=_scope(manifest_scope)
    where=["g.project_id=?","g.status NOT IN ('rejected','failed_before_send')"];args=[pid]
    excluded=list(dict.fromkeys(exclude_run_ids))
    if excluded:
        where.append('g.id NOT IN ('+','.join('?' for _ in excluded)+')');args.extend(excluded)
    criteria=[];scope_criteria=[]
    if task_ids is not None:
        task_ids=list(dict.fromkeys(task_ids))
        if not task_ids and not scope:return 0.0
        if task_ids:
            criteria.append('g.task_id IN ('+','.join('?' for _ in task_ids)+')');args.extend(task_ids)
    for name,value in scope.items():
        scope_criteria.append("CASE WHEN json_valid(g.manifest) THEN json_extract(g.manifest,?) END IS ?")
        args.extend(('$.'+name,value))
    if scope_criteria:criteria.append('('+' AND '.join(scope_criteria)+')')
    if criteria:where.append('(' + (' OR ' if task_or_scope else ' AND ').join(criteria)+')')
    selected='SELECT g.id,g.cost,g.reserved,m.group_id FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE '+' AND '.join(where)
    rows=db.rows(selected,args);total=Decimal(0);groups=set()
    for row in rows:
        if row['group_id']:groups.add(row['group_id'])
        else:total+=_amount(row['cost'] if row['cost'] is not None else row['reserved'])
    if groups:
        found=db.rows('SELECT id,amount FROM generation_billing_groups WHERE project_id=? AND id IN ('+','.join('?' for _ in groups)+')',[pid,*sorted(groups)])
        if {row['id'] for row in found}!=groups:raise ValueError('Budget billing-group membership is inconsistent')
        for row in found:total+=_amount(row['amount'])
    try:result=float(total)
    except (OverflowError,InvalidOperation) as error:raise ValueError('Budget charge exceeds supported range') from error
    if not math.isfinite(result):raise ValueError('Budget charge exceeds supported range')
    return result


def charge(pid,task_id=None,manifest_scope=None,exclude_run_ids=()):
    """A shared group is a whole-group upper bound for every scope it touches."""
    return _charge(pid,[task_id] if task_id is not None else None,manifest_scope,exclude_run_ids=exclude_run_ids)


def charge_tasks(pid,task_ids,manifest_scope=None,exclude_run_ids=()):
    """Union task IDs with a shared manifest scope, deduplicating touched groups."""
    return _charge(pid,task_ids,manifest_scope,task_or_scope=True,exclude_run_ids=exclude_run_ids)
