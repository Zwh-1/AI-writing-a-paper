"""Agent step routing uses existing writing_steps as authoritative checkpoints."""
from typing import TypedDict
from functools import lru_cache
from ..ai_runtime.policy import frozen

STAGES = ('investigate','judgment','judgment_check','draft','annotation','semantic','coherence','regression')


def decision(stage, output, operation):
    if stage=='judgment' and output['judgment']['blocking_gaps']:
        return {'status':'needs_evidence','next_stage':None}
    if stage=='judgment_check' and (not output['result']['coverage_complete'] or
            any(item['severity']=='blocker' for item in output['result']['findings'])):
        return {'status':'needs_review','next_stage':None}
    if stage=='regression' or stage=='coherence' and operation!='revise_unit':
        return {'status':'needs_review','next_stage':None}
    return {'status':'queued','next_stage':STAGES[STAGES.index(stage)+1]}


class Transition(TypedDict):
    stage: str
    output: dict
    operation: str
    status: str
    next_stage: str | None


@lru_cache(maxsize=1)
def compiled_router():
    from langgraph.graph import StateGraph, START, END
    graph=StateGraph(Transition)
    # The claimed writing_step executes the domain operation before routing.
    # No second queue or checkpoint database can submit that business result.
    graph.add_node('route_completed_step',lambda state:decision(state['stage'],state['output'],state['operation']))
    graph.add_edge(START,'route_completed_step');graph.add_edge('route_completed_step',END)
    return graph.compile()


def route(task, stage, output, operation):
    if frozen(task)['engine']=='legacy':
        return decision(stage,output,operation)
    from langsmith import tracing_context
    with tracing_context(enabled=False):
        result=compiled_router().invoke({'stage':stage,'output':output,'operation':operation},config={'callbacks':[]})
    return {name:result[name] for name in ('status','next_stage')}
