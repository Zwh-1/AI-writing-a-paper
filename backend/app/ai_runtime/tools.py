"""LangChain tools bind server-owned scope; domain validation stays authoritative."""
from contextvars import ContextVar
from .. import db
from .policy import frozen
from .telemetry import span

_ACTIVE=ContextVar('ai_structured_tool_active',default=False)


def active():
    return _ACTIVE.get()


def invoke(pid,step_id,name,arguments,call_id,snapshot_id,execute):
    if not isinstance(arguments,dict):
        return execute(pid,step_id,name,arguments,call_id,snapshot_id)
    task=db.one('SELECT t.* FROM tasks t JOIN writing_steps st ON st.task_id=t.id '
                'JOIN writing_sessions s ON s.id=st.session_id WHERE st.id=? AND s.project_id=?',
                (step_id,pid))
    if not task or frozen(task)['engine']=='legacy':
        return execute(pid,step_id,name,arguments,call_id,snapshot_id)
    from ..writing_agent.tools import schemas
    definition=next((item['function'] for item in schemas(include_knowledge=True,include_code=True)
                     if item['function']['name']==name),None)
    if definition is None:
        return execute(pid,step_id,name,arguments,call_id,snapshot_id)
    from langchain_core.tools import StructuredTool
    from langsmith import tracing_context
    def operation(**values):
        return execute(pid,step_id,name,values,call_id,snapshot_id)
    tool=StructuredTool.from_function(func=operation,name=name,
        description=definition['description'],args_schema=definition['parameters'],infer_schema=False)
    token=_ACTIVE.set(True)
    try:
        with tracing_context(enabled=False),span(task,name,'tool'):
            return tool.invoke(arguments,config={'callbacks':[]})
    finally:_ACTIVE.reset(token)
