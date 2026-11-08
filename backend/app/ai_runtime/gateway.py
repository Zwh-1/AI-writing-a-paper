"""LangChain delegates exactly once to the existing routed, metered transport."""
from .policy import frozen
from .telemetry import span
from functools import lru_cache


@lru_cache(maxsize=1)
def _runnable():
    from langchain_core.runnables import RunnableLambda
    def execute(value):
        return value['execute'](**value['arguments'])
    # The shared wrapper captures no task, transport, credentials or result.
    return RunnableLambda(execute, name='metered_model_execution')


def invoke(task, system, user, manifest, images, execute):
    config = frozen(task)
    from ..skill_runtime import enrich
    system,manifest=enrich(task,system,user,manifest)
    manifest={**manifest,'ai_execution_engine':config['engine'],'ai_quality_protocol':config['quality']}
    stage = manifest.get('writing_stage') or manifest.get('stage') or manifest.get('operation') or task['kind']
    with span(task, stage, 'model_gateway', engine=config['engine'], input_characters=len(system)+len(user)):
        if config['engine'] == 'legacy':
            return execute(task, system, user, manifest, images)
        from langsmith import tracing_context
        # Environment variables must not silently upload private papers or code.
        with tracing_context(enabled=False):
            return _runnable().invoke({'execute': execute, 'arguments': {'task': task, 'system': system, 'user': user,
                                'manifest': manifest, 'images': images}},
                               config={'max_concurrency': 1, 'callbacks': []})
