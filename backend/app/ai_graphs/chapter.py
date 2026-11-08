"""Chapter graph uses immutable references and a separate per-epoch checkpoint."""
from pathlib import Path
from typing import TypedDict
from copy import deepcopy
from .. import db, security
from ..ai_runtime import receipts
from ..ai_runtime.policy import frozen, payload, GRAPH
from ..ai_runtime.telemetry import span, record
from ..ai_request_cache import digest, singleflight


class State(TypedDict):
    state_ref: str


def read(ref):
    return db.loads(security.safe_object(ref).read_text(encoding='utf-8'))


def node_input(value):
    """Ignore only known navigation/observer fields, never measured source data."""
    result=deepcopy(value)
    manifest=result.get('manifest') or {}
    for key in ('evidence_bundle_hash','prompt_preparation_profile','stable_block_cache_hit'):
        manifest.pop(key,None)
    bundles=[result.get('evidence_bundle'),manifest.get('evidence_bundle'),
             (result.get('context') or {}).get('evidence_bundle')]
    for bundle in bundles:
        if isinstance(bundle,dict):
            for key in ('bundle_id','hash','retrieval_trace','timings_ms','created_at'):
                bundle.pop(key,None)
    return result


def run(task, section, initial, stages, validate,*,namespace='chapter',routes=None):
    policy = frozen(task)
    if policy['engine'] != 'langgraph_v1':
        state = initial
        for name, operation in stages:
            state = operation(state)
        return state
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langsmith import tracing_context
    run_id = task['id']+':'+section['id']+('' if namespace=='chapter' else ':'+namespace)
    # Existing resume endpoints explicitly record confirmed, uncheckpointed
    # responses. A confirmed regeneration gets a new namespace, while old
    # receipts and all outstanding budget holds remain intact.
    acknowledged=payload(task).get('acknowledged_run_ids') or []
    if acknowledged:
        run_id+=':'+digest(sorted(set(acknowledged)))[:16]
    graph_version=policy.get('graph_version',GRAPH)
    thread_id = digest([run_id, graph_version, task['attempt']])
    graph = StateGraph(State)

    def node(name, operation):
        def execute(state):
            value = read(state['state_ref'])
            validate(value)
            input_hash = digest(node_input(value))
            # Lock identity never changes with prompt/model/configuration/epoch.
            with singleflight(task, {'section_id': section['id']}, 'graph:'+name, 0):
                output = receipts.load(task, run_id, name, input_hash, validate)
                if output is not None:
                    record(task, name, 'graph_node', 'completed', 0, run_id=run_id, action_id=name, reused=True)
                else:
                    with span(task, name, 'graph_node', run_id=run_id, action_id=name, reused=False):
                        output = operation(value)
                        receipts.commit(task, run_id, name, input_hash, output, validate)
                return {'state_ref': security.put_blob(db.dumps(output).encode('utf-8'))}
        return execute

    previous = START
    for name, operation in stages:
        graph.add_node(name, node(name, operation))
        if previous in (routes or {}):
            choose,targets=routes[previous]
            graph.add_conditional_edges(previous,lambda state,choose=choose:choose(read(state['state_ref'])),targets)
        else:graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    folder = Path(db.DB_PATH).parent
    checkpoint = folder/'ai-graph-checkpoints.sqlite3'
    initial_ref = security.put_blob(db.dumps(initial).encode('utf-8'))
    with tracing_context(enabled=False), SqliteSaver.from_conn_string(str(checkpoint)) as saver:
        compiled = graph.compile(checkpointer=saver)
        result = compiled.invoke({'state_ref': initial_ref},
                                 config={'configurable': {'thread_id': thread_id},
                                         'recursion_limit': len(stages)+5, 'callbacks': []})
    return read(result['state_ref'])
