"""Recoverable quality actions with the existing receipt and request ledger."""
from .chapter import run
from ..ai_runtime.policy import frozen


def action(task,section,name,inputs,operation):
    if frozen(task).get('graph_version') not in ('chapter.graph.v2','chapter.graph.v3'):return operation()
    def validate(state):
        from ..tasks import _validate_model_source_inputs,_validate_model_presend
        _validate_model_source_inputs(task['project_id'],state['inputs'].get('manifest',{}))
        _validate_model_presend(task,state['inputs'].get('manifest',{}))
    result=run(task,section,{'inputs':inputs},[(name,lambda state:{**state,'output':operation()})],validate,namespace='quality:'+name)
    return result['output']


def initial(task,section,paragraphs,context,manifest):
    from copy import deepcopy
    from .. import writing_flow as flow
    if frozen(task).get('graph_version') not in ('chapter.graph.v2','chapter.graph.v3'):
        local=flow._local_quality(task,paragraphs,context,manifest)
        from ..ai_runtime.parallel import run as parallel
        checks=parallel(task,{'semantic':lambda:flow._semantic(task,section,paragraphs,deepcopy(context),deepcopy(manifest),local),
                              'discourse':lambda:flow._discourse(task,section,paragraphs,deepcopy(context),deepcopy(manifest))})
        return {'local':local,**checks,'originality':flow._originality(task['project_id'],section,paragraphs,context,task)}
    def validate(state):
        from ..tasks import _validate_model_source_inputs,_validate_model_presend
        _validate_model_source_inputs(task['project_id'],manifest);_validate_model_presend(task,manifest)
    review_enabled=flow.task_settings(task['project_id'],task)['semantic_review']
    def local(state):
        value={**state,'local':flow._local_quality(task,paragraphs,context,manifest)}
        if not review_enabled:value['semantic']=flow._semantic(task,section,paragraphs,context,manifest,value['local'])
        return value
    def semantic(state):return {**state,'semantic':flow._semantic(task,section,paragraphs,deepcopy(context),deepcopy(manifest),state['local'])}
    def discourse(state):return {**state,'discourse':flow._discourse(task,section,paragraphs,deepcopy(context),deepcopy(manifest))}
    def originality(state):return {**state,'originality':flow._originality(task['project_id'],section,paragraphs,context,task)}
    result=run(task,section,{'paragraphs':paragraphs,'context':context,'manifest':manifest},
               [('source_and_numeric_checks',local),('claim_support_review',semantic),('discourse_review',discourse),('originality_review',originality)],
               validate,namespace='quality:initial',routes={'source_and_numeric_checks':
                   (lambda state:'semantic' if review_enabled else 'discourse',
                    {'semantic':'claim_support_review','discourse':'discourse_review'})})
    return {key:result[key] for key in ('local','semantic','discourse','originality')}
