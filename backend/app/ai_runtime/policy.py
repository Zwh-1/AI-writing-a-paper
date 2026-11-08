"""Policies are frozen at enqueue; recovery never upgrades old tasks implicitly."""
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import sqlite3
from fastapi import HTTPException
from .. import db

VERSION = 'ai.runtime.v1'
GRAPH = 'chapter.graph.v3'
QUALITY = 'evidence.obligations.v1'
WRITING_KINDS=frozenset({'write','write_paper','agent_step'})
DEFAULTS = {'new_task_engine': 'langgraph_v1', 'new_task_quality': QUALITY,
            'max_parallel_checks': 1, 'telemetry_enabled': True}
_INHERITED=ContextVar('ai_protocol_inheritance',default=None)


@contextmanager
def inherit_protocol(task):
    """Internal enqueue scope; load the authoritative source, never a client blob.

    This does not claim a lease or authorize executing the source task. It only
    lets a resumed session create a step with its original frozen policy.
    """
    if task is None:
        yield
        return
    source=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(task['id'],task['project_id']))
    if not source:raise HTTPException(404,'运行协议继承来源不存在')
    token=_INHERITED.set((source['project_id'],deepcopy(frozen(source))))
    try:yield
    finally:_INHERITED.reset(token)


def settings():
    saved = db.setting('ai_runtime_policy', {})
    if not isinstance(saved,dict):saved={}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def payload(task):
    value = task.get('payload', {})
    return db.loads(value, {}) if isinstance(value, str) else value


def new_protocol(project_id=None):
    current=settings()
    result={'version':VERSION,'engine':current['new_task_engine'],'quality':current['new_task_quality'],
            'graph_version':GRAPH,'max_parallel_checks':current['max_parallel_checks'],
            'writing_flow_policy':None,'skills':None,'memory':None}
    if project_id is not None:
        inherited=_INHERITED.get()
        if inherited and inherited[0]==project_id:
            result.update(deepcopy(inherited[1]))
            for name in ('writing_flow_policy','skills','memory'):result[name]=deepcopy(inherited[1].get(name))
            return result
        from ..task_transitions import active_owner
        owner=active_owner()
        if owner and owner['project_id']==project_id:
            parent=db.one('SELECT * FROM tasks WHERE id=?',(owner['id'],))
            inherited=frozen(parent)
            result.update(deepcopy(inherited))
            for name in ('writing_flow_policy','skills','memory'):result[name]=deepcopy(inherited.get(name))
            return result
        from ..writing_flow import settings as writing_settings
        result['writing_flow_policy']=deepcopy(writing_settings(project_id))
        from ..skill_runtime import snapshot as skill_snapshot
        result['skills']=skill_snapshot(project_id)
        from ..project_memory import snapshot as memory_snapshot
        result['memory']=memory_snapshot(project_id)
    return result


def freeze(kind, body, project_id=None):
    result = deepcopy(body)
    supplied = result.get('ai_runtime')
    if supplied is not None and not isinstance(supplied, dict):
        raise HTTPException(422, 'AI运行协议必须为对象')
    if kind not in WRITING_KINDS and supplied is None:return result
    frozen = new_protocol(project_id)
    if supplied:
        if set(supplied) - set(frozen):
            raise HTTPException(422, 'AI运行协议包含未知字段')
        if 'skills' in supplied and supplied['skills']!=frozen['skills']:
            raise HTTPException(422,'技能快照只能由服务端生成')
        if 'memory' in supplied and supplied['memory']!=frozen['memory']:
            raise HTTPException(422,'记忆快照只能由服务端生成')
        frozen.update(supplied)
    validate(frozen)
    if kind in WRITING_KINDS or supplied is not None:
        result['ai_runtime'] = frozen
    return result


def bind_task(task_id,kind,body):
    """Internal metadata must not change legacy nonwriting payload contracts."""
    protocol=body.get('ai_runtime')
    if protocol is None:
        row=db.one('SELECT project_id FROM tasks WHERE id=?',(task_id,))
        protocol=new_protocol(row['project_id'])
    validate(protocol)
    db.execute('INSERT INTO ai_task_protocols(task_id,protocol) VALUES(?,?)',(task_id,db.dumps(protocol)))


def validate(value):
    if value.get('version') != VERSION or value.get('graph_version') not in ('chapter.graph.v1','chapter.graph.v2',GRAPH):
        raise HTTPException(422, '未知的AI运行或工作流版本')
    if value.get('engine') not in ('legacy', 'langgraph_v1'):
        raise HTTPException(422, '未知的AI执行引擎')
    if value.get('quality') not in ('legacy', QUALITY):
        raise HTTPException(422, '未知的AI质量协议')
    if type(value.get('max_parallel_checks')) is not int or not 1 <= value['max_parallel_checks'] <= 3:
        raise HTTPException(422, '并行检查数须为1至3')
    if value.get('writing_flow_policy') is not None:
        from ..writing_flow import validate_settings
        validate_settings(value['writing_flow_policy'], complete=True)
    if value.get('skills') is not None:
        from ..skill_runtime import validate_policy
        skill=value['skills']
        if not isinstance(skill,dict) or set(skill)!={'policy','catalogue'} or not isinstance(skill['catalogue'],list):
            raise HTTPException(422,'技能快照格式无效')
        validate_policy(skill['policy'],skill['catalogue'])
    if value.get('memory') is not None:
        from ..project_memory import validate_policy as validate_memory_policy
        memory=value['memory']
        if not isinstance(memory,dict) or set(memory)!={'policy','entries'} or not isinstance(memory['entries'],list):
            raise HTTPException(422,'记忆快照格式无效')
        validate_memory_policy(memory['policy'])


def frozen(task):
    value = payload(task).get('ai_runtime')
    if value is None and task.get('id'):
        try:saved=db.one('SELECT protocol FROM ai_task_protocols WHERE task_id=?',(task['id'],))
        except sqlite3.OperationalError as error:
            if 'no such table' not in str(error):raise
            saved=None
        value=db.loads(saved['protocol'],None) if saved else None
    if value is None:
        return {'engine': 'legacy', 'quality': 'legacy', 'max_parallel_checks': 1}
    validate(value)
    return value


def quality_enabled(task):
    return frozen(task)['quality'] == QUALITY


def save(body):
    if not body or set(body) - set(DEFAULTS):
        raise HTTPException(422, 'AI运行设置包含未知字段')
    proposed = {**settings(), **body}
    if type(proposed['telemetry_enabled']) is not bool:
        raise HTTPException(422, '运行观察开关须为布尔值')
    validate({'version': VERSION, 'graph_version': GRAPH,
              'engine': proposed['new_task_engine'], 'quality': proposed['new_task_quality'],
              'max_parallel_checks': proposed['max_parallel_checks']})
    with db.transaction():
        db.set_setting('ai_runtime_policy', proposed)
        db.audit(None, 'ai_runtime_policy_changed', proposed)
    return proposed
