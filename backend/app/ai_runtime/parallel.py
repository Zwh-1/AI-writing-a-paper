"""Bounded independent checks, with isolated SQL sessions and provider scopes."""
from concurrent.futures import ThreadPoolExecutor, CancelledError, as_completed
from contextvars import copy_context
from threading import Event
from .. import db, provider
from .policy import frozen


def run(task, operations):
    limit = min(len(operations), frozen(task)['max_parallel_checks'])
    # A shared attempt allowance or transaction must never be split across threads.
    if limit <= 1 or db._ACTIVE.get() is not None or provider._ATTEMPT_BUDGET.get() is not None:
        return {name: operation() for name, operation in operations.items()}

    stopped = Event()
    def invoke(operation):
        if stopped.is_set():
            raise CancelledError()
        token = db._READ_SESSION.set(None)
        try:
            with db.read_session():
                return operation()
        except BaseException:
            # Stop queued checks before the executor can start another request.
            stopped.set()
            raise
        finally:
            db._READ_SESSION.reset(token)

    with ThreadPoolExecutor(max_workers=limit, thread_name_prefix='ai-check') as executor:
        futures = {name: executor.submit(copy_context().run, invoke, operation)
                   for name, operation in operations.items()}
        results={};errors=[]
        names = {future: name for name, future in futures.items()}
        for future in as_completed(names):
            name = names[future]
            try:results[name]=future.result()
            except CancelledError:continue
            except BaseException as error:
                errors.append(error)
                for pending in futures.values():pending.cancel()
        if errors:
            # A schema failure must not hide another already-started request
            # whose remote outcome is unknown. All active futures are observed.
            unknown=next((error for error in errors if isinstance(error,provider.ProviderError) and error.unknown),None)
            if unknown:raise unknown
            if db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('pending','sent','outcome_unknown') LIMIT 1",(task['id'],)):
                raise provider.ProviderError('并行核验仍有未确认的远端请求，已保留原结果和费用预留',unknown=True) from errors[0]
            raise errors[0]
        return {name: results[name] for name in operations if name in results}
