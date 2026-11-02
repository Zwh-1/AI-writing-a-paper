"""Workflow completion: rule execution, reversible outline, formats and reviews."""
from fastapi import APIRouter,HTTPException
from . import db,security,documents,tasks,rules,rule_engine,exports
import hashlib,difflib,re
router=APIRouter()

def active_sections(pid):return db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,))
def validate_outline(nodes):
    ids={n['id'] for n in nodes};parents={n['id']:n.get('parent_id') for n in nodes}
    deps={n['id']:db.loads(n.get('dependencies'),[]) if isinstance(n.get('dependencies'),str) else n.get('dependencies',[]) for n in nodes}
    for node in nodes:
        sid=node['id'];parent=parents[sid]
        if parent and parent not in ids:raise HTTPException(422,'父章节必须属于当前未归档提纲')
        cursor=sid;seen=set()
        while cursor:
            if cursor in seen:raise HTTPException(422,'章节层级存在循环')
            seen.add(cursor);cursor=parents.get(cursor)
        if len(seen)>7:raise HTTPException(422,'章节层级最多六层')
        if not isinstance(deps[sid],list) or any(d not in ids for d in deps[sid]):raise HTTPException(422,'依赖章节不属于当前提纲')
    visited=set();visiting=set()
    def walk(sid):
        if sid in visiting:raise HTTPException(422,'章节依赖存在循环')
        if sid in visited:return
        visiting.add(sid)
        for d in deps[sid]:walk(d)
        visiting.remove(sid);visited.add(sid)
    for sid in ids:walk(sid)
    return nodes

def flatten_tree(nodes):
    result=[]
    def walk(parent,level):
        for n in sorted((n for n in nodes if n.get('parent_id')==parent),key=lambda n:n['position']):
            row=dict(n);row['level']=level;result.append(row);walk(n['id'],level+1)
    walk(None,1)
    return result

@router.get('/projects/{pid}/outline/tree')
def outline_tree(pid):
    p=security.require_project(pid);nodes=active_sections(pid);validate_outline(nodes)
    for node in nodes:
        rev=documents.current(node);node['word_count']=documents.counts(rev['text'])['total'] if rev else 0
    return {'version':p['outline_revision'],'nodes':flatten_tree(nodes),'archives':db.rows("SELECT id,title,current_revision_id FROM sections WHERE project_id=? AND state='archived'",(pid,))}

@router.put('/projects/{pid}/outline/tree')
def save_tree(pid,body:dict):
    nodes=active_sections(pid);mapping={n['id']:n for n in nodes};incoming=body.get('nodes',[])
    if set(n.get('id') for n in incoming)!=set(mapping) or len(incoming)!=len(nodes):raise HTTPException(422,'提纲更新必须包含所有未归档章节，删除请使用归档')
    merged=[{**mapping[n['id']],**{k:v for k,v in n.items() if k in ('id','parent_id','position','title','kind','target_words','goal','dependencies')}} for n in incoming];validate_outline(merged)
    merged=flatten_tree(merged)
    for index,n in enumerate(merged,1):n['position']=index
    for n in merged:
        if not isinstance(n['title'],str) or not n['title'].strip() or not 0<=int(n['target_words'])<=300000:raise HTTPException(422,'标题或篇幅无效')
    with db.transaction() as c:
        p=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        if not p:raise HTTPException(404,'项目不存在')
        if body.get('version')!=p['outline_revision']:raise HTTPException(409,'提纲已更新，请刷新后重试')
        c.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),pid,'outline_tree',db.dumps(nodes),db.now()))
        for n in merged:c.execute('UPDATE sections SET title=?,kind=?,parent_id=?,position=?,target_words=?,goal=?,dependencies=? WHERE id=?',(n['title'],n['kind'],n.get('parent_id'),n['position'],n['target_words'],n['goal'],db.dumps(n['dependencies']) if isinstance(n['dependencies'],list) else n['dependencies'],n['id']))
        c.execute('UPDATE projects SET outline_revision=outline_revision+1,updated_at=? WHERE id=?',(db.now(),pid))
    for n in merged:
        old=mapping[n['id']]
        if any(n[k]!=old[k] for k in ('title','kind','goal','parent_id')):documents.mark_downstream(pid,n['id'],'提纲结构或写作目标已变更')
    return outline_tree(pid)

@router.post('/sections/{sid}/archive')
def archive_section(sid,body:dict):
    s=security.require_section(sid);nodes=active_sections(s['project_id']);children=[n for n in nodes if n.get('parent_id')==sid];dependents=[n for n in nodes if sid in db.loads(n['dependencies'],[])]
    if children or dependents:raise HTTPException(409,{'message':'请先移走子章节并解除依赖，再归档','children':[n['id'] for n in children],'dependents':[n['id'] for n in dependents]})
    if body.get('base_revision_id')!=s['current_revision_id']:raise HTTPException(409,'章节正文已变更，不能按旧状态归档')
    with db.transaction() as c:
        c.execute('INSERT INTO section_archives VALUES(?,?,?) ON CONFLICT(section_id) DO UPDATE SET metadata=excluded.metadata,created_at=excluded.created_at',(sid,db.dumps(s),db.now()))
        c.execute("UPDATE sections SET state='archived' WHERE id=?",(sid,));c.execute('UPDATE projects SET outline_revision=outline_revision+1 WHERE id=?',(s['project_id'],))
    return {'archived':True,'content_preserved':True}

@router.post('/sections/{sid}/unarchive')
def restore_section(sid):
    s=security.require_section(sid);snapshot=db.one('SELECT metadata FROM section_archives WHERE section_id=?',(sid,))
    if not snapshot or s['state']!='archived':raise HTTPException(409,'章节未归档')
    old=db.loads(snapshot['metadata'],{});parent=old.get('parent_id');p=db.one("SELECT id FROM sections WHERE id=? AND state!='archived'",(parent,)) if parent else None
    with db.transaction() as c:
        c.execute('UPDATE sections SET state=?,parent_id=?,dependencies=?,position=? WHERE id=?',('draft' if s['current_revision_id'] else 'empty',parent if p else None,'[]',max([x['position'] for x in active_sections(s['project_id'])],default=0)+1,sid));c.execute('UPDATE projects SET outline_revision=outline_revision+1 WHERE id=?',(s['project_id'],))
    return security.require_section(sid)

@router.get('/projects/{pid}/rule-execution')
def executable_rules(pid):
    security.require_project(pid);items=db.rows('SELECT * FROM rules WHERE project_id=? ORDER BY created_at',(pid,))
    for r in items:
        c=db.one('SELECT expression FROM rule_conditions WHERE rule_id=?',(r['id'],));r['conditions']=db.loads(c['expression'],{}) if c else {};r['operations']=db.loads(r['operations']);r['value']=db.loads(r['value'])
    return {'rules':items,'checkers':sorted(rule_engine.CHECKERS),'format_keys':sorted(rule_engine.FORMAT_KEYS)}


@router.get('/rules/{rid}/evidence')
def rule_evidence(rid):
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    if not rule:raise HTTPException(404,'规则不存在')
    security.require_project(rule['project_id'])
    from . import rule_text_integrity as integrity
    source=integrity.source_for_rule(rule);info=integrity.inspect_rule(rule,source)
    repairs=db.rows("SELECT action,detail,created_at FROM audit WHERE project_id=? AND action IN ('repair_rule_text_integrity','correct_rule_visual_transcription') AND json_extract(detail,'$.rule_id')=? ORDER BY id",(rule['project_id'],rid))
    record=db.loads(repairs[0]['detail'],{}) if repairs else {}
    return {'rule_id':rid,'integrity':integrity.summary(info),'context':info['context'],'source_text':source['text'] if source else '', 'source_file':source['source_file'] if source else None,'page_number':source['page_number'] if source else None,'source_method':source['method'] if source else None,'before_statement':record.get('before',{}).get('statement'),'repaired_at':repairs[-1]['created_at'] if repairs else None,'visual_correction':any(r['action']=='correct_rule_visual_transcription' for r in repairs)}

@router.put('/rules/{rid}/execution')
def save_execution(rid,body:dict):
    old=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    if not old:raise HTTPException(404,'规则不存在')
    from .rule_workspace import validate_rule
    condition=db.one('SELECT expression FROM rule_conditions WHERE rule_id=?',(rid,))
    base={**old,'operations':db.loads(old['operations']),'value':db.loads(old['value']),'conditions':db.loads(condition['expression'],{}) if condition else {},**body}
    validate_rule(base)
    checker,value,operations=rule_engine.validate_execution(base)
    if 'rule_key' not in body:base['rule_key']=rule_engine.execution_key(checker,value)
    if body.get('source_id'):
        source=db.one('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(body['source_id'],old['project_id']))
        if not source:raise HTTPException(422,'规则来源不属于当前项目')
    with db.transaction() as c:
        c.execute('UPDATE rules SET checker=?,value=?,operations=?,scope=?,rule_key=? WHERE id=?',(checker,db.dumps(value),db.dumps(operations),base['scope'],base.get('rule_key',''),rid))
        c.execute('INSERT INTO rule_conditions VALUES(?,?) ON CONFLICT(rule_id) DO UPDATE SET expression=excluded.expression',(rid,db.dumps(base['conditions'])))
        c.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),old['project_id'],'rule_execution',db.dumps({'before':old,'after':base}),db.now()))
    return {'saved':True,'requires_republish':True}

@router.post('/rules/{rid}/infer')
def infer_execution(rid,body:dict={}):
    r=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    if not r:raise HTTPException(404,'规则不存在')
    from .rule_workspace import validate_rule
    validate_rule(body)
    return rule_engine.infer(body.get('statement',r['statement']),body.get('scope',r['scope']))

@router.post('/findings/{fid}/review')
def manual_review(fid,body:dict):
    f=db.one('SELECT * FROM findings WHERE id=?',(fid,))
    if not f or not f['rule_id'] or f['method']!='human':raise HTTPException(422,'仅人工判断规则可以登记人工核对；程序失败项应修正后重查')
    result=body.get('result');note=str(body.get('note','')).strip()
    if result not in ('pass','fail','unknown','not_applicable') or len(note)<3:raise HTTPException(422,'请选择判断结果并填写核对依据')
    p=security.require_project(f['project_id']);fingerprint=rule_engine.project_input_fingerprint(p['id'],[f['section_id']]);rid=db.uid()
    db.execute('INSERT INTO manual_rule_reviews VALUES(?,?,?,?,?,?,?,?)',(rid,p['id'],f['rule_id'],p['rule_package_id'],fingerprint,result,note,db.now()));db.audit(p['id'],'manual_rule_review',{'rule_id':f['rule_id'],'result':result,'note':note,'input_fingerprint':fingerprint})
    from .reviews import run
    return run(p['id'])

@router.post('/sections/{sid}/reviewed')
def mark_reviewed(sid,body:dict):
    s=security.require_section(sid)
    if not s['current_revision_id'] or body.get('revision_id')!=s['current_revision_id']:raise HTTPException(409,'请核对当前正文版本')
    with db.transaction() as c:
        c.execute('INSERT INTO section_reviews VALUES(?,?,?,?,?)',(db.uid(),sid,s['current_revision_id'],str(body.get('note','用户已审阅')),db.now()))
        c.execute("UPDATE sections SET state='user_reviewed',stale=0,stale_reason='' WHERE id=?",(sid,))
    return {'reviewed_revision_id':s['current_revision_id']}

@router.get('/projects/{pid}/format/rules')
def format_rule_preview(pid):
    p=security.require_project(pid);pkg=db.one('SELECT * FROM rule_packages WHERE id=?',(p['rule_package_id'],)) if p['rule_package_id'] else None;updates={};conflicts=[];source=[]
    for rule in db.loads(pkg['rules'],[]) if pkg else []:
        if rule['checker']!='format_value' or rule['modality']!='mandatory':continue
        value=db.loads(rule['value'],{});key=value.get('key')
        if key not in rule_engine.FORMAT_KEYS:continue
        expected=value['expected']
        if key in updates and updates[key]!=expected:conflicts.append(key)
        updates[key]=expected;source.append({'rule_id':rule['id'],'key':key,'expected':expected})
    from .paper_format import resolve
    resolution=resolve(pid)
    for key,value in resolution.get('components',{}).get('body',{}).items():updates.setdefault(key,value)
    return {'package_id':pkg['id'] if pkg else None,'current':resolution['config'],'updates':updates,'conflicts':sorted(set(conflicts))+resolution['conflicts'],'sources':source,
            'components':resolution.get('components',{}),'component_sources':resolution.get('component_sources',{}),'manual_rules':resolution.get('manual_rules',[])}

@router.post('/projects/{pid}/format/apply-rules')
def apply_format_rules(pid,body:dict):
    preview=format_rule_preview(pid)
    if not preview['package_id'] or body.get('package_id')!=preview['package_id']:raise HTTPException(409,'规则包已更新或尚未发布')
    if preview['conflicts']:raise HTTPException(422,'排版规则仍存在冲突')
    if not preview['updates'] and not preview.get('components'):raise HTTPException(422,'当前规则包没有可执行排版条款，请先为已确认要求配置检查器')
    config=db.setting('format:'+pid,{}) or {}
    inferred=set(preview.get('components',{}).get('body',{}))
    for key in inferred:config.pop(key,None)
    config.update({key:value for key,value in preview['updates'].items() if key not in inferred})
    db.set_setting('format:'+pid,config);db.audit(pid,'apply_format_rules',preview);return exports.format_config(pid)

@router.get('/projects/{pid}/format-profiles')
def list_profiles(pid):security.require_project(pid);return db.rows('SELECT * FROM project_formats WHERE project_id=? ORDER BY created_at DESC',(pid,))
@router.post('/projects/{pid}/format-profiles')
def save_profile(pid,body:dict):
    security.require_project(pid);name=str(body.get('name','')).strip()
    if not name:raise HTTPException(422,'请输入模板方案名称')
    profile=db.uid();config=exports.format_config(pid);template=db.setting('template:'+pid);db.execute('INSERT INTO project_formats VALUES(?,?,?,?,?,?,?)',(profile,pid,name,db.dumps(config),template,0,db.now()));return {'id':profile,'name':name}
@router.post('/format-profiles/{profile}/activate')
def activate_profile(profile,body:dict):
    row=db.one('SELECT * FROM project_formats WHERE id=? AND project_id=?',(profile,body.get('project_id')))
    if not row:raise HTTPException(404,'排版方案不属于当前项目')
    db.set_setting('format:'+row['project_id'],db.loads(row['config'],{}));db.set_setting('template:'+row['project_id'],row['template_sha256']);db.audit(row['project_id'],'activate_format',{'profile':profile});return {'activated':True}
@router.post('/format-profiles/{profile}/verify')
def verify_profile(profile,body:dict):
    row=db.one('SELECT * FROM project_formats WHERE id=?',(profile,))
    if not row:raise HTTPException(404,'方案不存在')
    note=str(body.get('note','')).strip()
    if len(note)<5:raise HTTPException(422,'请记录目标软件、样本与人工验收情况')
    db.execute('UPDATE project_formats SET verified=1 WHERE id=?',(profile,));db.audit(row['project_id'],'format_human_verification',{'profile':profile,'note':note});return {'human_verified':True}

@router.get('/tasks/{tid}/diff')
def candidate_diff(tid):
    task=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    if not task:raise HTTPException(404,'任务不存在')
    result=db.loads(task['result'],{});candidate=result.get('document');sid=result.get('section_id')
    if not candidate or not sid:raise HTTPException(422,'没有可比较的正文候选')
    base=db.one('SELECT * FROM revisions WHERE id=?',(result.get('base_revision_id'),));old=db.loads(base['document'],{}) if base else {'type':'doc','content':[]};before=old.get('content',[]);after=candidate.get('content',[])
    a=[documents.plain(n) for n in before];b=[documents.plain(n) for n in after];changes=[]
    for tag,i,j,x,y in difflib.SequenceMatcher(a=a,b=b,autojunk=False).get_opcodes():
        changes.append({'kind':tag,'old_node_ids':[n.get('attrs',{}).get('nodeId') for n in before[i:j]],'new_node_ids':[n.get('attrs',{}).get('nodeId') for n in after[x:y]],'old_text':'\n'.join(a[i:j]),'new_text':'\n'.join(b[x:y])})
    return {'base_revision_id':result.get('base_revision_id'),'changes':changes,'current_revision_id':security.require_section(sid)['current_revision_id']}

@router.patch('/source-units/{source_id}/usage')
def classify_source(source_id,body:dict):
    src=db.one('SELECT s.*,f.project_id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=?',(source_id,))
    if not src:raise HTTPException(404,'来源不存在')
    kind=body.get('kind')
    if kind not in ('evidence','example','counterexample','rule_candidate','commentary'):raise HTTPException(422,'用途分类无效')
    db.execute('UPDATE sources SET kind=? WHERE id=?',(kind,source_id));db.audit(src['project_id'],'classify_source',{'source_id':source_id,'before':src['kind'],'after':kind});return {'kind':kind}

@router.get('/ocr/local/capabilities')
def local_ocr_capabilities():
    from .local_ocr import capabilities
    return capabilities()
@router.post('/pages/{page_id}/ocr-local')
def local_page_ocr(page_id,body:dict):
    p=db.one('SELECT f.project_id FROM pages p JOIN files f ON f.id=p.file_id WHERE p.id=?',(page_id,))
    if not p:raise HTTPException(404,'页面不存在')
    return tasks.enqueue(p['project_id'],'ocr_local',{'page_id':page_id,'language':body.get('language','zh-Hans-CN')})
@router.post('/files/{file_id}/ocr-local')
def local_file_ocr(file_id,body:dict):
    f=db.one('SELECT * FROM files WHERE id=? AND active=1',(file_id,))
    if not f:raise HTTPException(404,'资料不存在')
    if f['status']!='ready':raise HTTPException(422,'请先完成解析')
    return tasks.enqueue(f['project_id'],'ocr_local_batch',{'file_id':file_id,'language':body.get('language','zh-Hans-CN')})
@router.get('/projects/{pid}/rules/ai-sources')
def ai_source_plan(pid,file_id:str=''):
    from .rule_ai import source_scope
    security.require_project(pid)
    if file_id and not db.one("SELECT id FROM files WHERE id=? AND project_id=? AND role='rules' AND active=1",(file_id,pid)):raise HTTPException(422,'选定规则文件不属于当前项目')
    sources=source_scope(pid,[file_id] if file_id else [])
    from .summary_billing import budget
    return {'total':len(sources),'local_only':sum(s['egress']=='local_only' for s in sources),'budget':budget(pid)}

@router.post('/projects/{pid}/rules/ai-extract')
def ai_rules(pid,body:dict):
    from .rule_ai import source_scope
    security.require_project(pid);files=body.get('file_ids',[])
    if not isinstance(files,list) or len(files)>50 or any(not isinstance(fid,str) for fid in files):raise HTTPException(422,'文件范围无效')
    for fid in files:
        if not db.one("SELECT id FROM files WHERE id=? AND project_id=? AND role='rules' AND active=1 AND status='ready'",(fid,pid)):raise HTTPException(422,'选定规则文件不属于当前项目或未完成解析')
    automatic=body.get('automatic',False)
    if not isinstance(automatic,bool):raise HTTPException(422,'自动整理开关格式无效')
    limit=body.get('source_limit',40);offset=body.get('source_offset',0)
    if not automatic and (type(limit) is not int or not 1<=limit<=120):raise HTTPException(422,'每次AI整理最多120个来源片段；可使用整份材料自动整理')
    if type(offset) is not int or offset<0:raise HTTPException(422,'起始片段位置应为非负整数')
    sources=source_scope(pid,files);batch=sources if automatic else sources[offset:offset+limit]
    if not batch:raise HTTPException(422,'当前范围没有可整理的来源片段')
    if any(s['egress']=='local_only' for s in batch):raise HTTPException(422,'当前批次含仅限本地材料，请使用本地整理或调整文件范围')
    payload={'file_ids':files,'source_limit':limit,'source_offset':offset,'source_ids':[s['id'] for s in batch]}
    if automatic:
        import math
        project=security.require_project(pid);maximum=body.get('budget_limit',project['budget']);gpt=body.get('gpt_budget',0);allow=body.get('allow_gpt',False)
        if not isinstance(allow,bool):raise HTTPException(422,'GPT 复核开关格式无效')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in (maximum,gpt)) or maximum>project['budget'] or gpt>maximum:raise HTTPException(422,'整理预算和GPT子预算须为项目预算范围内的有效金额')
        payload.update(automatic=True,source_limit=len(batch),source_offset=0,budget_limit=maximum,gpt_budget=gpt,allow_gpt=allow,auto_publish=bool(body.get('auto_publish',False)))
        payload['review_existing_only']=bool(body.get('review_existing_only',False))
    if 'provider_profile' in body:
        if body['provider_profile'] not in ('primary','gpt'):raise HTTPException(422,'规则整理接口选择无效')
        if body['provider_profile']=='gpt':
            from .model_routing import snapshot
            snapshot()
        payload['provider_profile']=body['provider_profile']
        if automatic and body['provider_profile']=='gpt':
            if 'allow_gpt' in body and not body['allow_gpt']:raise HTTPException(422,'已选择GPT，但GPT调用被明确关闭')
            payload['allow_gpt']=True
            if 'gpt_budget' not in body:payload['gpt_budget']=payload['budget_limit']
            if payload['gpt_budget']<=0:raise HTTPException(422,'已选择GPT，请设置大于零的GPT子预算')
    return tasks.enqueue(pid,'rules_ai',payload)


@router.post('/projects/{pid}/rules/auto-review')
def auto_review_rules(pid,body:dict):
    from .rule_governance import review_project
    security.require_project(pid)
    result=review_project(pid)
    if body.get('publish'):
        from . import rules
        from .rule_workspace import diagnostics
        status=diagnostics(pid)
        if status['can_publish']:result['rule_package']=rules.publish(pid)
        else:result['publication_blockers']=status['blockers']
    return result


@router.post('/projects/{pid}/rules/budget-reconcile')
def reconcile_rule_budget(pid):
    from . import summary_billing,model_routing,provider
    security.require_project(pid);results=[]
    for profile in ('material-summary','gpt-fallback'):
        if profile=='gpt-fallback' and not model_routing.ready():continue
        try:results.append({'profile':profile,**summary_billing.reconcile(pid,profile=profile)})
        except (ValueError,provider.ProviderError) as error:results.append({'profile':profile,'reconciled':False,'reason':provider.redact(error)})
    return {'profiles':results,'budget':summary_billing.budget(pid)}


@router.post('/projects/{pid}/rules/decisions/{decision_id}/restore')
def restore_rule_decision(pid,decision_id:int):
    from .rule_governance import restore
    security.require_project(pid);return restore(pid,decision_id)


@router.get('/projects/{pid}/rules/source-policy')
def get_rule_policy(pid):
    from .rule_governance import source_policy
    security.require_project(pid);return {'files':source_policy(pid),'default':'course'}


@router.get('/projects/{pid}/rules/review-integrity')
def get_rule_review_integrity(pid):
    security.require_project(pid)
    from .rule_text_repair import current_published_affected
    text_integrity=db.setting('rule_text_integrity:'+pid,{})
    if text_integrity:text_integrity={**text_integrity,'published_affected_rule_ids':current_published_affected(pid)}
    return {**db.setting('rule_review_integrity:'+pid,{'findings':[],'restored_rule_ids':[]}),'text_integrity':text_integrity}


@router.put('/projects/{pid}/rules/source-policy')
def set_rule_policy(pid,body:dict):
    security.require_project(pid);policy=body.get('files',{})
    if not isinstance(policy,dict) or len(policy)>500:raise HTTPException(422,'来源政策格式无效')
    for fid,value in policy.items():
        if value not in ('binding','course','reference') or not db.one("SELECT id FROM files WHERE id=? AND project_id=? AND role='rules'",(fid,pid)):raise HTTPException(422,'来源或权威类型无效')
    with db.transaction():
        previous=db.setting('rule_source_policy:'+pid,{})
        changed={fid for fid in set(previous)|set(policy) if previous.get(fid,'course')!=policy.get(fid,'course')}
        db.set_setting('rule_source_policy:'+pid,policy)
        if changed:
            marks=','.join('?' for _ in changed);args=(pid,*sorted(changed))
            affected=[r['id'] for r in db.rows('SELECT r.id FROM rules r JOIN sources s ON s.id=r.source_id WHERE r.project_id=? AND s.file_id IN ('+marks+") AND r.state IN ('machine_verified','reference_only','candidate')",args)]
            for rid in affected:
                db.execute("UPDATE rules SET state='candidate' WHERE id=? AND state IN ('machine_verified','reference_only')",(rid,))
                db.execute('UPDATE rule_decisions SET restored=1 WHERE rule_id=?',(rid,))
        db.audit(pid,'rule_source_policy',policy)
    return get_rule_policy(pid)


@router.post('/projects/{pid}/rules/resolve-conflict')
def resolve_rule_conflict(pid,body:dict):
    from . import rules
    security.require_project(pid);rid=body.get('rule_id')
    with db.transaction():
        group=next((g for g in rules.conflicts(pid) if any(r['id']==rid for r in g['rules'])),None)
        if not group:raise HTTPException(409,'冲突组已变化，请刷新后选择')
        before=[db.one('SELECT * FROM rules WHERE id=?',(r['id'],)) for r in group['rules']]
        for rule in before:
            if rule['id']==rid:db.execute("UPDATE rules SET state='confirmed',decision_note=? WHERE id=?",('用户在冲突组中选择采用此条款',rid))
            else:db.execute("UPDATE rules SET state='rejected',decision_note=? WHERE id=?",('用户在冲突组中选择其他条款：'+rid,rule['id']))
        db.audit(pid,'resolve_rule_conflict',{'selected_rule_id':rid,'before':before})
    return {'selected':rid,'resolved':len(before)}

@router.get('/projects/{pid}/readiness')
def readiness(pid):
    p=security.require_project(pid);sections=active_sections(pid)
    files=db.rows('SELECT * FROM files WHERE project_id=? AND active=1',(pid,));pages=db.rows("SELECT p.*,f.name FROM pages p JOIN files f ON f.id=p.file_id WHERE f.project_id=? AND f.active=1 AND f.role='rules'",(pid,));findings=db.rows("SELECT result,method,COUNT(*) AS n FROM findings WHERE project_id=? GROUP BY result,method",(pid,))
    pending=sum(x['review_state']=='unreviewed' for x in pages);failed=[f['name'] for f in files if f['status']!='ready'];drafted=[s for s in sections if s['current_revision_id']];stale=[s for s in sections if s['stale']];reviewed=[s for s in sections if s['state']=='user_reviewed'];blockers=[]
    if failed:blockers.append({'kind':'parse','message':str(len(failed))+'份资料解析未完成','objects':failed})
    if pending:blockers.append({'kind':'rule_pages','message':str(pending)+'页规则材料尚未核对'})
    if not p['rule_package_id']:blockers.append({'kind':'rule_package','message':'尚未发布本项目规则包'})
    if not sections:blockers.append({'kind':'outline','message':'尚未建立提纲'})
    if len(drafted)<len(sections):blockers.append({'kind':'draft','message':str(len(sections)-len(drafted))+'个章节没有正文'})
    if stale:blockers.append({'kind':'stale','message':str(len(stale))+'个章节需复查'})
    if len(reviewed)<len(drafted):blockers.append({'kind':'human_review','message':str(len(drafted)-len(reviewed))+'个草稿章节尚未记录人工审阅'})
    return {'counts':{'files':len(files),'rule_pages':len(pages),'pending_rule_pages':pending,'sections':len(sections),'drafted_sections':len(drafted),'reviewed_sections':len(reviewed),'stale_sections':len(stale)},'blockers':blockers,'checks':findings,'message':'准备状态只反映项目数据与检查记录，不代表研究内容或学校版式已合格'}

@router.get('/projects/{pid}/impacts')
def impacts(pid):
    security.require_project(pid);out=[]
    for s in active_sections(pid):
        if not s['stale']:continue
        out.append({'section_id':s['id'],'title':s['title'],'reason':s['stale_reason'],'current_revision_id':s['current_revision_id'],'dependencies':db.rows('SELECT upstream_type,upstream_id,upstream_version FROM dependencies WHERE revision_id=?',(s['current_revision_id'],))})
    return out

@router.post('/projects/{pid}/retrieval/evaluate')
def retrieval_evaluate(pid,body:dict):
    from .parsers import search
    security.require_project(pid);cases=body.get('cases',[])
    if not isinstance(cases,list) or not 1<=len(cases)<=200:raise HTTPException(422,'请提交1至200条带正确来源标注的问题')
    prepared=[]
    for index,case in enumerate(cases,1):
        prefix=f'第 {index} 个问题：'
        if not isinstance(case,dict):raise HTTPException(422,prefix+'必须包含真实问题 query 和标准来源 expected_source_ids')
        query=case.get('query','');expected=case.get('expected_source_ids',[]);kind=case.get('kind','all')
        if not isinstance(query,str) or not query.strip():raise HTTPException(422,prefix+'请填写要检验的真实问题')
        if query.strip() in ('待检验的问题','请输入要验证的真实问题'):raise HTTPException(422,prefix+'仍是示例问题，请替换为真实问题，并选择能够回答它的原文')
        if not isinstance(expected,list) or not expected or any(not isinstance(sid,str) or not sid.strip() for sid in expected):raise HTTPException(422,prefix+'请至少选择一个原文片段作为标准来源')
        if not isinstance(kind,str) or kind not in ('all','research','example'):raise HTTPException(422,prefix+'检索范围须为 all、research 或 example')
        expected=list(dict.fromkeys(sid.strip() for sid in expected))
        for source_index,sid in enumerate(expected,1):
            if sid in ('人工确认的正确来源ID','实际来源ID','真实来源ID','来源ID'):raise HTTPException(422,prefix+'仍使用示例来源 ID，请在表单中选择当前项目的真实原文，或填写真实来源 ID')
            source=db.one('SELECT s.id,s.kind,f.active FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(sid,pid))
            if not source:raise HTTPException(422,prefix+f'第 {source_index} 个标准来源在当前项目中不存在，请从当前项目重新选择资料和原文页码')
            if not source['active']:raise HTTPException(422,prefix+f'第 {source_index} 个标准来源的资料已停用，请重新选择可用资料')
            if kind=='research' and source['kind']!='evidence' or kind=='example' and source['kind'] not in ('example','counterexample'):raise HTTPException(422,prefix+'标准来源不在所选检索范围，请改为“全部资料”或重新选择来源')
        prepared.append({'query':query.strip(),'expected_source_ids':expected,'kind':kind})
    results=[];hits=0;recalls=[]
    for case in prepared:
        query=case['query'];expected=case['expected_source_ids'];kind=case['kind']
        found=search(pid,query,kind=kind,limit=10);retrieved=[s['id'] for s in found];matched=set(expected)&set(retrieved);ok=bool(matched);recall=len(matched)/len(expected);hits+=int(ok);recalls.append(recall);results.append({'query':query,'kind':kind,'expected_source_ids':expected,'retrieved_ids':retrieved,'hit':ok,'recall_at_10':recall})
    result={'cases':results,'hits':hits,'total':len(prepared),'recall_at_10':sum(recalls)/len(recalls),'hit_rate_at_10':hits/len(prepared),'label_source':'请求中提交的来源标注，尚未记录人工确认','human_confirmation':'not_recorded','formal_human_acceptance':False,'created_at':db.now()};db.execute('INSERT INTO retrieval_evaluations VALUES(?,?,?,?)',(db.uid(),pid,db.dumps(result),db.now()));return result
@router.get('/projects/{pid}/retrieval/evaluations')
def retrieval_evaluations(pid):security.require_project(pid);return db.rows('SELECT * FROM retrieval_evaluations WHERE project_id=? ORDER BY created_at DESC',(pid,))
