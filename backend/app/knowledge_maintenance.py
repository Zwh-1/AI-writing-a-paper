"""Durable complete maintenance queue. No arbitrary six-page cutoff."""
import threading
import sqlite3
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from fastapi import HTTPException
from . import db,tasks,provider,knowledge_lineage as lineage
from .config import MAX_PARALLEL_REQUESTS

# Model-side failures (unparsable JSON, quotes that do not match the source) do
# not heal on retry. Without a cap a durable queue keeps paying for the same
# item forever, so it is retired after this many attempts with its last error.
MAX_ITEM_ATTEMPTS=3

def schedule(pid,kind,target,payload):
    stamp=lineage.digest(payload);identity=db.uid()
    db.execute('INSERT INTO knowledge_maintenance_items(id,project_id,kind,target_id,input_hash,payload_json,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,kind,target_id,input_hash) DO NOTHING',
               (identity,pid,kind,target,stamp,db.dumps(payload),db.now()))
    return db.one('SELECT * FROM knowledge_maintenance_items WHERE project_id=? AND kind=? AND target_id=? AND input_hash=?',(pid,kind,target,stamp))

def prepare(pid,namespace='all',support_checks=False,discover_topics=False,reports=True,file_ids=None,summary=True):
    from .knowledge_graph import get_assertion
    if namespace!='all':__import__('app.knowledge_graph',fromlist=['namespace']).namespace(namespace)
    ns_sql=' AND namespace=?' if namespace!='all' else '';args=(pid,namespace) if ns_sql else (pid,)
    claims=[]
    scope=lineage.descendants_of_files(pid,file_ids or [])
    with lineage.validation_scope():
        # get_assertion() rebuilds each assertion with its full reference graph.
        # On a 3,000 claim queue that is minutes of work, and the result is only
        # consumed when support checks or topic discovery actually need it, so
        # a plain refresh of an existing queue must not pay for it.
        if support_checks or discover_topics:
            for r in db.rows("SELECT claim_version_id FROM kg_assertions WHERE project_id=? AND lifecycle NOT IN ('stale','excluded','superseded')"+ns_sql,args):
                if scope is not None and r['claim_version_id'] not in scope.get('assertion',set()):continue
                try:
                    a=get_assertion(pid,r['claim_version_id']);ref=lineage.ref(pid,'assertion',a['claim_version_id'])
                    claims.append((a,ref))
                    if support_checks and not a['assessments']:schedule(pid,'assess',a['claim_version_id'],{'claim_ids':[a['claim_version_id']],'refs':[ref]})
                except HTTPException:continue
        for page in db.rows('SELECT p.*,v.state FROM wiki_pages p LEFT JOIN wiki_versions v ON v.id=p.current_version_id WHERE p.project_id=?'+(' AND p.namespace=?' if namespace!='all' else ''),args):
            if scope is not None and page['current_version_id'] not in scope.get('wiki_version',set()):continue
            if page['state']=='stale' and page['kind'] in ('document','guidance') and page['slug'].startswith('source-'):
                fid=page['slug'][7:39]
                original=db.one('SELECT id FROM files WHERE id=? AND project_id=?',(fid,pid))
                if original:schedule(pid,'document_refresh',fid,{'file_id':fid,'namespace':page['namespace'],'stale_version':page['current_version_id']})
                continue
            if page['kind'] not in ('topic','comparison','disagreement','concept','method') or page['slug']=='source-index':continue
            if page['state']!='stale':continue
            from .knowledge_graph import search
            selected=search(pid,page['title'],page['namespace'],1,60,20,file_ids=file_ids)['assertions']
            refs=[lineage.ref(pid,'assertion',a['claim_version_id']) for a in selected]
            if refs:schedule(pid,'synthesize',page['id'],{'namespace':page['namespace'],'kind':page['kind'],'slug':page['slug'],'title':page['title'],'sources':refs})
        if discover_topics:
            for ns in ('literature','guidance','project'):
                if namespace not in ('all',ns):continue
                pairs=[(a,ref) for a,ref in claims if a['namespace']==ns]
                if not pairs:continue
                templates=[('topic','研究路线与资料覆盖'),('comparison','方法条件与比较维度'),('disagreement','分歧限制与待补依据')]
                from .knowledge_router import balanced_claims
                # Full candidate sets are split into bounded source groups.
                ordered=balanced_claims(pid,[a for a,_ in pairs],len(pairs));reference={a['claim_version_id']:r for a,r in pairs}
                for kind,title in templates:
                    for start in range(0,len(ordered),16):
                        selected=ordered[start:start+16];refs=[reference[a['claim_version_id']] for a in selected]
                        slug='compiled-'+kind+'-'+lineage.digest([ns,[r['id'] for r in refs]])[:16]
                        existing=db.one('SELECT id,current_version_id FROM wiki_pages WHERE project_id=? AND namespace=? AND slug=?',(pid,ns,slug))
                        if existing and existing['current_version_id']:continue
                        schedule(pid,'synthesize',slug,{'namespace':ns,'kind':kind,'slug':slug,'title':title+f'（来源组{start//16+1}）','sources':refs})
        if reports:
            for artifact in db.rows("SELECT * FROM knowledge_artifacts WHERE project_id=? AND kind='graphrag_index' AND state='stale'",(pid,)):
                manifest=db.loads(artifact['manifest_json'],{});packet=manifest.get('packet',{})
                if namespace!='all' and manifest.get('namespace')!=namespace:continue
                if file_ids and not set(manifest.get('source_files',[]))<=set(file_ids):continue
                payload={**manifest.get('build_options',{}),'namespace':manifest.get('namespace','literature'),
                         'reports':packet.get('reports',False),'embeddings':packet.get('embeddings',False),
                         'full_index':manifest.get('scope')=='full_current_scope','replaces_artifact_id':artifact['id']}
                schedule(pid,'graphrag',artifact['id'],payload)
    return status(pid,namespace,file_ids) if summary else None

def claim_namespaces(pid):
    """One query instead of one per assess item; the per-item lookup made
    status() take minutes on a 3,000 item queue."""
    return {r['claim_version_id']:r['namespace'] for r in db.rows('SELECT claim_version_id,namespace FROM kg_assertions WHERE project_id=?',(pid,))}

def belongs(pid,row,namespace='all',file_ids=None,namespaces=None):
    if namespace=='all' and not file_ids:return True
    payload=db.loads(row['payload_json'],{});ns=payload.get('namespace')
    if not ns and row['kind']=='assess':
        if namespaces is None:
            original=db.one('SELECT namespace FROM kg_assertions WHERE project_id=? AND claim_version_id=?',(pid,row['target_id']))
            ns=original['namespace'] if original else None
        else:ns=namespaces.get(row['target_id'])
    if namespace!='all' and ns!=namespace:return False
    if not file_ids:return True
    if payload.get('file_id'):return payload['file_id'] in file_ids
    refs=payload.get('sources') or payload.get('refs')
    if refs:return bool(set(lineage.source_files(pid,refs))&set(file_ids)) and set(lineage.source_files(pid,refs))<=set(file_ids)
    if row['kind']=='graphrag':
        artifact=db.one('SELECT manifest_json FROM knowledge_artifacts WHERE project_id=? AND id=?',(pid,row['target_id']))
        ids=db.loads(artifact['manifest_json'],{}).get('source_files',[]) if artifact else []
        return bool(ids) and set(ids)<=set(file_ids)
    return False

def status(pid,namespace='all',file_ids=None,namespaces=None):
    from .security import require_project
    require_project(pid)
    if namespace!='all':__import__('app.knowledge_graph',fromlist=['namespace']).namespace(namespace)
    lineage.descendants_of_files(pid,file_ids or [])
    if namespace!='all' and namespaces is None:namespaces=claim_namespaces(pid)
    rows=db.rows('SELECT * FROM knowledge_maintenance_items WHERE project_id=? ORDER BY updated_at,id',(pid,))
    rows=[r for r in rows if belongs(pid,r,namespace,file_ids,namespaces)]
    return {'items':[{**r,'payload':db.loads(r['payload_json'],{}),'result':db.loads(r['result_json'],{})} for r in rows],
            'counts':{s:sum(r['status']==s for r in rows) for s in ('queued','running','completed','budget_blocked','outcome_unknown','failed','conflict','stale','excluded')},
            'complete':all(r['status'] in ('completed','excluded') for r in rows),'human_approval_added':False}

def _retire_item(pid,row,error,state='failed',permanent=False):
    """Count a failed attempt and retire the item once it stops being worth
    another paid request. The last error stays on the row for review.

    ``permanent`` retires on the first attempt: the failure is a property of the
    stored data (a source too long for the interface budget, an observation that
    cannot support the assertion, a superseded original), so every later pass
    reproduces it exactly while still consuming a turn of the queue.
    """
    with db.transaction() as connection:
        found=connection.execute('SELECT attempts FROM knowledge_maintenance_items WHERE id=?',(row['id'],)).fetchone()
        attempts=int((found[0] if found else 0) or 0)+1
        retired=permanent or attempts>=MAX_ITEM_ATTEMPTS
        status='excluded' if retired else state
        connection.execute('UPDATE knowledge_maintenance_items SET status=?,attempts=?,error=?,updated_at=? WHERE id=?',(status,attempts,error,db.now(),row['id']))
    if retired:
        db.audit(pid,'knowledge_maintenance_item_retired',{'item_id':row['id'],'kind':row['kind'],'target_id':row['target_id'],'attempts':attempts,'permanent':permanent,'last_error':error})
    return status


# Failures that no number of retries can change: the inputs, not the model
# round trip, decide them.
PERMANENT_ERRORS=(
    '上下文超过本次接口快照的预算',
    'SOURCE_SCOPE_UNVERIFIED',
    'SOURCE_STALE',
    'SOURCE_EXCLUDED',
    # The model answered, was billed, and its answer still had no usable
    # structure. The same input reaches the same model on every retry, so a
    # second paid attempt buys the same non-answer: this is the model's ceiling
    # on that assertion, not a transient fault.
    'KNOWLEDGE_OUTPUT_INVALID',
)


def permanent_failure(message):
    return any(marker in (message or '') for marker in PERMANENT_ERRORS)

def _process_item(task,body,row,pid,stop,BudgetExceeded):
    """Claim and process one queue item. Safe to call from several threads: the
    claim is a conditional UPDATE, so two workers can never own the same row."""
    if stop.is_set():return ('skipped',None)
    payload={**db.loads(row['payload_json'],{}),'budget_limit':body.get('budget_limit',0)}
    with db.transaction() as connection:
        claimed=connection.execute("UPDATE knowledge_maintenance_items SET status='running',task_id=?,updated_at=? WHERE id=? AND status IN ('queued','budget_blocked','failed')",(task['id'],db.now(),row['id'])).rowcount
    if not claimed:return ('skipped',None)
    try:
        # Earlier support checks may change assertion versions. Re-read
        # current references before synthesis, never send a stale snapshot.
        if row['kind']=='synthesize' and payload.get('sources'):
            payload['sources']=[lineage.ref(pid,r['type'],r['id'],True) for r in payload['sources']]
        if row['kind']=='assess':
            from .knowledge_wiki_checks import assess_with_model
            result=assess_with_model(task,payload)
        elif row['kind']=='document_refresh':
            from .knowledge_compiler import document_page
            from .knowledge_graph import get_entity
            original=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(payload['file_id'],pid))
            entity=db.one('SELECT id FROM kg_entities WHERE project_id=? AND namespace=? AND entity_key=?',(pid,payload['namespace'],'file:'+payload['file_id']))
            if not original or not entity:lineage.fail('SOURCE_MISSING','文献页原件或实体不存在')
            ids=db.rows("SELECT DISTINCT a.claim_version_id,a.method FROM kg_assertions a JOIN knowledge_dependencies d ON d.downstream_type='assertion' AND d.downstream_id=a.claim_version_id AND d.upstream_type='observation' JOIN knowledge_observations o ON o.id=d.upstream_id WHERE a.project_id=? AND o.file_id=? AND a.namespace=? AND a.lifecycle NOT IN ('stale','superseded','excluded') ORDER BY a.created_at,a.claim_version_id",(pid,payload['file_id'],payload['namespace']))
            mode='llm' if any(r['method']=='llm' for r in ids) else 'literal'
            selected=[r['claim_version_id'] for r in ids if mode!='llm' or r['method']=='llm']
            result={'page_ids':document_page(pid,original,get_entity(pid,entity['id']),selected,mode),'model_requests':0}
        elif row['kind']=='synthesize':
            from .knowledge_compiler import synthesize
            result=synthesize(task,payload)
        elif row['kind']=='graphrag':
            from .graphrag_adapter import build
            result=build(task,payload)
        else:lineage.fail('UNKNOWN_MAINTENANCE','维护类型无效',422)
        state='conflict' if result.get('conflict_id') else 'completed'
        db.execute('UPDATE knowledge_maintenance_items SET status=?,result_json=?,error=NULL,attempts=0,updated_at=? WHERE id=?',(state,db.dumps(result),db.now(),row['id']))
        return ('done',row['id'])
    except BudgetExceeded as error:
        db.execute("UPDATE knowledge_maintenance_items SET status='budget_blocked',error=?,updated_at=? WHERE id=?",(str(error),db.now(),row['id']))
        stop.set();return ('stopped',None)
    except ValueError as error:
        # tasks.json_call refuses a prompt that the interface budget cannot hold.
        # The assertion and its sources are what they are, so every pass fails
        # identically: retire on the first attempt instead of burning turns.
        message=str(error)
        _retire_item(pid,row,message,permanent=permanent_failure(message));return ('failed',None)
    except provider.ProviderError as error:
        if error.unknown:
            db.execute("UPDATE knowledge_maintenance_items SET status='outcome_unknown',error=?,updated_at=? WHERE id=?",(provider.redact(error),db.now(),row['id']))
            stop.set();return ('stopped',None)
        if getattr(error,'failure_phase',None)=='connect_before_http':
            # The request never left this machine, so nothing was charged and the
            # item itself did nothing wrong. Requeue it without spending an
            # attempt: the gateway drops a large share of handshakes, and counting
            # those as item failures would retire healthy work.
            db.execute("UPDATE knowledge_maintenance_items SET status='queued',error=?,updated_at=? WHERE id=?",(provider.redact(error),db.now(),row['id']))
            return ('failed',None)
        _retire_item(pid,row,provider.redact(error));return ('failed',None)
    except HTTPException as error:
        message=str(error.detail)
        _retire_item(pid,row,message,state='stale',permanent=permanent_failure(message));return ('failed',None)
    except InterruptedError:
        db.execute("UPDATE knowledge_maintenance_items SET status='queued',updated_at=? WHERE id=?",(db.now(),row['id']))
        stop.set();return ('stopped',None)
    except Exception as error:
        if isinstance(error,sqlite3.OperationalError) and 'locked' in str(error):
            # A write collision is this process's problem, not the item's: keep the
            # attempt count and let a later pass pick it up again.
            db.execute("UPDATE knowledge_maintenance_items SET status='queued',error=?,updated_at=? WHERE id=?",(str(error),db.now(),row['id']))
            return ('failed',None)
        message=provider.redact(error)
        _retire_item(pid,row,message,permanent=permanent_failure(message));return ('failed',None)

def run(task,body):
    from .summary_billing import BudgetExceeded
    pid=task['project_id']
    namespace=body.get('namespace','all');file_ids=body.get('file_ids')
    lineage.descendants_of_files(pid,file_ids or [])
    namespaces=claim_namespaces(pid) if namespace!='all' else None
    for abandoned in db.rows("SELECT m.* FROM knowledge_maintenance_items m LEFT JOIN tasks t ON t.id=m.task_id WHERE m.project_id=? AND m.status='running' AND (t.id IS NULL OR t.status NOT IN ('running','queued'))",(pid,)):
        if not belongs(pid,abandoned,namespace,file_ids,namespaces):continue
        unknown=db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('sent','pending','outcome_unknown') LIMIT 1",(abandoned['task_id'],))
        db.execute('UPDATE knowledge_maintenance_items SET status=?,updated_at=? WHERE id=?',('outcome_unknown' if unknown else 'queued',db.now(),abandoned['id']))
    # refresh_queue=False is for a caller that already built the queue: rebuilding
    # it rescans every assertion through get_assertion() and takes minutes.
    refresh=body.get('refresh_queue',True)
    if refresh:prepare(pid,namespace,body.get('support_checks',False),body.get('discover_topics',False),body.get('reports',True),file_ids,summary=False)
    rows=db.rows("SELECT * FROM knowledge_maintenance_items WHERE project_id=? AND status IN ('queued','budget_blocked','failed') ORDER BY CASE kind WHEN 'assess' THEN 0 WHEN 'document_refresh' THEN 1 WHEN 'synthesize' THEN 2 ELSE 3 END,updated_at,id",(pid,))
    rows=[r for r in rows if belongs(pid,r,namespace,file_ids,namespaces)]
    if body.get('item_ids'):rows=[r for r in rows if r['id'] in body['item_ids']]
    done=[]
    index=0;refreshed=not refresh
    parallel=body.get('knowledge_parallel_requests',1)
    if type(parallel) is not int or not 1<=parallel<=MAX_PARALLEL_REQUESTS:parallel=1
    stop=threading.Event()
    def run_one(row):return _process_item(task,body,row,pid,stop,BudgetExceeded)
    def next_row():
        """Return the next queued item, refreshing the queue once on the way in.

        Assessments can invalidate additional Wiki pages and community
        artifacts. Include these in this same durable run.
        """
        nonlocal index,refreshed
        if index>=len(rows):return None
        row=rows[index]
        if row['kind']!='assess' and not refreshed:
            refreshed=True
            prepare(pid,namespace,False,False,body.get('reports',True),file_ids,summary=False)
            known={r['id'] for r in rows}
            additional=[r for r in db.rows("SELECT * FROM knowledge_maintenance_items WHERE project_id=? AND status IN ('queued','budget_blocked','failed') AND kind!='assess' ORDER BY CASE kind WHEN 'document_refresh' THEN 0 WHEN 'synthesize' THEN 1 ELSE 2 END,updated_at,id",(pid,)) if r['id'] not in known and belongs(pid,r,namespace,file_ids,namespaces) and (not body.get('item_ids') or r['id'] in body['item_ids'])]
            rows[index:index]=additional
            row=rows[index]
        return row
    def report():
        tasks.event(task['id'],f'维护知识：{index}/{len(rows)}',int(5+90*index/max(1,len(rows))))
    if parallel<2:
        while not stop.is_set():
            row=next_row()
            if row is None:break
            index+=1
            report()
            state,identity=run_one(row)
            if state=='done':done.append(identity)
    else:
        # A fixed batch waits for its slowest request before starting the next
        # one, so one 40s outlier idles every other worker. Keep the window full
        # instead: a slot is refilled the moment any request returns.
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            pending={};kind=None
            while not stop.is_set():
                while len(pending)<parallel and not stop.is_set():
                    row=next_row()
                    if row is None:break
                    # One window never mixes kinds: the queue order is a
                    # dependency order, so synthesis must not overtake the
                    # support checks that change its inputs.
                    if pending and row['kind']!=kind:break
                    kind=row['kind']
                    index+=1
                    pending[pool.submit(copy_context().run,run_one,row)]=row
                if not pending:break
                finished,_=wait(list(pending),return_when=FIRST_COMPLETED)
                for future in finished:
                    pending.pop(future,None)
                    state,identity=future.result()
                    if state=='done':done.append(identity)
                report()
    if rows and index>=len(rows) and not refreshed and done and not body.get('_assessment_stage_drained'):
        extra=run(task,{**body,'support_checks':False,'discover_topics':False,'_assessment_stage_drained':True})
        return {**extra,'completed_item_ids':done+extra['completed_item_ids']}
    if any(r['kind']=='document_refresh' and r['id'] in done for r in rows):
        from .knowledge_compiler import update_indexes
        with lineage.validation_scope():update_indexes(pid)
    return {'completed_item_ids':done,**status(pid,namespace,file_ids,namespaces)}
