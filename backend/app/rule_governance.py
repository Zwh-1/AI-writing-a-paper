"""Evidence-based automatic decisions; machine decisions never impersonate a human."""
import re,hashlib
from fastapi import HTTPException
from . import db,rule_engine
from .source_payload import source_hash

VERSION='rule-review-v2'
ACTIVE=('confirmed','machine_verified')
DIRECTIVE=re.compile(r'必须|不得|禁止|应当|建议|需要|不少于|不超过|不得超过|至少|至多|要求|应采用|应包括|应包含|应说明|应使用')
DOMAIN=re.compile(r'论文|摘要|关键词|参考文献|引用|文献综述|研究方法|研究背景|实验|数据|结果|结论|标题|正文|字体|字号|行距|页边距|缩进|图表|学术|抄袭|编造|剽窃')
EXAMPLE=re.compile(r'错误示例|反例|不合格示例|错误写法|以下为错误|正例|范例|例如|举例')
CONDITIONAL=re.compile(r'如果|除非|除.{0,20}外|当.{1,30}时|视情况|按.{1,20}类型|分别|或者|二选一')
GRADE=re.compile(r'[ABCDＡＢＣＤ]\s*级|评分范围|评级|达到.{0,12}等级',re.I)


def source_policy(pid):return db.setting('rule_source_policy:'+pid,{})


def local_candidates(source):
    from .rule_text_integrity import units
    result=[]
    flowing=source.get('method')=='windows_ocr' or str(source.get('source_file','')).lower().endswith('.pdf')
    for _,_,line in units(source['text'],flowing):
        line=line.strip()
        if 8<=len(line)<=12000 and DIRECTIVE.search(line) and DOMAIN.search(line):
            result.append({'source_id':source['id'],'quote':line,'statement':line,'modality':'pending','scope':'global','category':'content'})
    return result


def review_one(rule,source,policy,semantic=False,evidence=None):
    from .rule_ai import exact_quote
    from .rule_text_integrity import inspect_rule
    statement=rule['statement'].strip()
    integrity=inspect_rule(rule,source)
    if integrity['needs_attention']:return 'needs_resolution',{},[integrity['issue'],'条款完整性尚未通过，不按片段自动确认或归档']
    if rule['modality'] in ('example','counterexample'):
        return 'example',{},['保留原有正反例分类，不作为论文约束启用']
    rubric=bool(re.search(r'等级|评分|评价',source.get('source_file','')))
    if GRADE.search(statement):
        return 'conditional_reference',{},['评分等级或评价指标保留为条件参考；未推定本项目必须达到某一等级']
    quote=exact_quote(source['text'],evidence if semantic and evidence is not None else statement)
    if quote is None:return 'needs_resolution',{},['表述并非原文逐字条款，保留自动复核；未以相似度冒充证据']
    position=source['text'].find(quote);context=source['text'][max(0,position-80):position+len(quote)]
    if EXAMPLE.search(context):
        return 'example',{},['保留正反例，不作为论文约束自动启用']
    if not semantic and (not DIRECTIVE.search(statement) or not DOMAIN.search(statement)):
        return 'knowledge',{},['知识说明或缺少明确规范表达，不作为硬性要求']
    if len(statement)>280 or CONDITIONAL.search(statement):
        return 'needs_resolution',{},['复杂条件或多段要求需要局部复核']
    if db.one('SELECT expression FROM rule_conditions WHERE rule_id=?',(rule['id'],)):
        return 'needs_resolution',{},['保留已有适用条件，交由局部复核']
    authority=policy.get(source['file_id'],'course')
    if authority=='reference':return 'knowledge',{},['该来源按项目政策仅作知识参考']
    inferred=rule_engine.infer(statement,rule['scope'])
    modality='recommended' if re.search(r'建议|宜|可以',statement) or authority!='binding' or not DIRECTIVE.search(statement) else 'mandatory'
    reasons=['原文依据可逐字定位，语义审核保留数字、否定与限定条件' if semantic else '条款逐字存在于当前原文，保留数字、否定与限定条件','来源为约束依据' if authority=='binding' else '课程资料按建议启用，未推定为学校强制要求']
    category='format' if inferred['checker']=='format_value' else rule['category']
    return 'machine_verified',{'state':'machine_verified','modality':modality,'scope':inferred['scope'],'checker':inferred['checker'],'rule_key':inferred['rule_key'],'value':db.dumps(inferred['value']),'category':category},reasons


def record(pid,before,after,source,outcome,reasons):
    db.execute('INSERT INTO rule_decisions(project_id,rule_id,version,source_hash,outcome,reasons,before_row,after_row,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(pid,before['id'],VERSION,source_hash(source),outcome,db.dumps(reasons),db.dumps(before),db.dumps(after),db.now()))


@db.atomic
def review_project(pid,source_ids=None):
    policy=source_policy(pid);counts={};changed=[]
    selected=set(source_ids) if source_ids is not None else None
    sources={s['id']:s for s in db.rows("SELECT s.*,f.sha256 AS file_sha256,f.name AS source_file,p.text AS page_text FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.active=1 AND f.role='rules' AND f.status='ready' AND (p.id IS NULL OR p.review_state!='not_applicable')",(pid,))}
    for rule in db.rows("SELECT * FROM rules WHERE project_id=? AND state IN ('candidate','machine_verified','reference_only') ORDER BY created_at,id",(pid,)):
        sid=rule['source_id']
        if selected is not None and sid not in selected:continue
        source=sources.get(sid)
        if not source:continue
        from .rule_text_integrity import inspect_rule
        complete=not inspect_rule(rule,source)['needs_attention']
        prior=db.one('SELECT * FROM rule_decisions WHERE rule_id=? AND restored=0 ORDER BY id DESC LIMIT 1',(rule['id'],))
        if complete and prior and prior['source_hash']==source_hash(source) and db.loads(prior['after_row'])==rule and rule['state']=='candidate' and prior['outcome'] in ('knowledge','example','conditional_reference'):
            db.execute("UPDATE rules SET state='reference_only' WHERE id=?",(rule['id'],))
            after=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))
            record(pid,rule,after,source,prior['outcome'],db.loads(prior['reasons'],[]));counts[prior['outcome']]=counts.get(prior['outcome'],0)+1;continue
        if complete and prior and prior['version']==VERSION and prior['source_hash']==source_hash(source) and db.loads(prior['after_row'])==rule:
            if not (rule['state']=='candidate' and rubric_candidate(rule,source)):
                counts[prior['outcome']]=counts.get(prior['outcome'],0)+1;continue
        outcome,updates,reasons=review_one(rule,source,policy)
        if outcome in ('knowledge','example','conditional_reference'):updates={'state':'reference_only'}
        if rule['state'] in ('machine_verified','reference_only') and not updates:
            db.execute("UPDATE rules SET state='candidate' WHERE id=?",(rule['id'],))
        if updates:
            merged={**rule,**updates}
            db.execute('UPDATE rules SET state=?,modality=?,scope=?,checker=?,rule_key=?,value=?,category=? WHERE id=?',tuple(merged[k] for k in ('state','modality','scope','checker','rule_key','value','category'))+(rule['id'],))
            changed.append(rule['id'])
        after=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))
        record(pid,rule,after,source,outcome,reasons);counts[outcome]=counts.get(outcome,0)+1
    # Same-level incompatible machine constraints are quarantined as a group.
    # Existing human choices retain priority. No arbitrary "newer file wins" rule.
    from . import rules
    for group in rules.conflicts(pid,mandatory_only=True):
        for item in group['rules']:
            before=db.one('SELECT * FROM rules WHERE id=?',(item['id'],))
            if before['state']!='machine_verified':continue
            db.execute("UPDATE rules SET state='candidate' WHERE id=?",(before['id'],))
            after=db.one('SELECT * FROM rules WHERE id=?',(before['id'],))
            record(pid,before,after,sources[before['source_id']],'conflict',['同适用范围存在不相容的强制要求，已按组隔离'])
    from .rule_duplicates import detect,merge
    merged=0
    for group in detect(db.rows("SELECT * FROM rules WHERE project_id=? AND state IN ('confirmed','machine_verified')",(pid,))):
        # Only the existing exact signature groups are eligible, never fuzzy matches.
        canonical=group.get('canonical_id')
        members=group.get('rules',[])
        if canonical and members:
            ids=[r['id'] for r in members if r['id']!=canonical]
            if ids:merge(pid,canonical,ids);merged+=len(ids)
    return {'outcomes':counts,'changed':len(changed),'merged':merged,'machine_verified':db.one("SELECT COUNT(*) n FROM rules WHERE project_id=? AND state='machine_verified'",(pid,))['n'],'requires_individual_confirmation':False}


def rubric_candidate(rule,source):
    return bool(GRADE.search(rule['statement']))


def current_decisions(pid):
    result={}
    for row in latest_decision_rows(pid):
        result[row['rule_id']]={'id':row['id'],'outcome':row['outcome'],'reasons':db.loads(row['reasons'],[]),'version':row['version'],'source_hash':row['source_hash']}
    return result


def latest_decision_rows(pid):
    return db.rows('''SELECT d.* FROM rule_decisions d JOIN
        (SELECT rule_id,MAX(id) AS id FROM rule_decisions WHERE project_id=? AND restored=0 GROUP BY rule_id) latest
        ON latest.id=d.id''',(pid,))


@db.atomic
def restore(pid,decision_id):
    row=db.one('SELECT * FROM rule_decisions WHERE id=? AND project_id=? AND restored=0',(decision_id,pid))
    if not row:raise HTTPException(404,'自动审核记录不存在或已撤回')
    current=db.one('SELECT * FROM rules WHERE id=?',(row['rule_id'],));after=db.loads(row['after_row'])
    if current!=after:raise HTTPException(409,'条款已有后续修改，不能覆盖')
    before=db.loads(row['before_row'])
    for key in ('statement','state','modality','scope','checker','rule_key','value','category'):
        db.execute('UPDATE rules SET '+key+'=? WHERE id=?',(before[key],row['rule_id']))
    db.execute('UPDATE rule_decisions SET restored=1 WHERE id=?',(decision_id,))
    db.audit(pid,'restore_machine_review',{'decision_id':decision_id,'rule_id':row['rule_id']})
    return {'restored':row['rule_id']}


def machine_valid(rule):
    row=db.one('SELECT * FROM rule_decisions WHERE rule_id=? AND restored=0 ORDER BY id DESC LIMIT 1',(rule['id'],))
    source=db.one('SELECT s.*,f.sha256 AS file_sha256,f.name AS source_file,p.text AS page_text FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.id=?',(rule['source_id'],))
    return machine_matches(rule,row,source)


def machine_matches(rule,row,source):
    if not row or row['outcome']!='machine_verified':return False
    if not source or row['source_hash']!=source_hash(source):return False
    from .rule_text_integrity import inspect_rule
    if inspect_rule(rule,source)['needs_attention']:return False
    after=db.loads(row['after_row'],{})
    return all(rule.get(k)==after.get(k) for k in ('statement','state','modality','scope','checker','rule_key','value','category','operations','source_id'))


def machine_validity(pid,items):
    """Apply the same evidence checks using one current decision/source snapshot."""
    if not items:return {}
    decisions={row['rule_id']:row for row in latest_decision_rows(pid)}
    sources={row['id']:row for row in db.rows('''SELECT s.*,f.sha256 AS file_sha256,f.name AS source_file,p.text AS page_text
        FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id
        WHERE f.project_id=? AND s.id IN (SELECT source_id FROM rules WHERE project_id=? AND state='machine_verified')''',(pid,pid))}
    return {rule['id']:machine_matches(rule,decisions.get(rule['id']),sources.get(rule['source_id'])) for rule in items}


def page_hash(page):return hashlib.sha256(db.dumps([page['text'],[(s['id'],source_hash(s)) for s in db.rows('SELECT s.*,f.sha256 AS file_sha256 FROM sources s JOIN files f ON f.id=s.file_id WHERE s.page_id=? ORDER BY s.id',(page['id'],))]]).encode()).hexdigest()


def page_covered(page):
    row=db.one('SELECT * FROM rule_page_checks WHERE page_id=?',(page['id'],))
    return bool(row and row['outcome']=='machine_covered' and row['content_hash']==page_hash(page))


def update_page_coverage(pid):
    from .rule_pipeline import source_scope
    grouped={}
    for source in source_scope(pid):grouped.setdefault(source['page_id'],[]).append(source)
    for page_id,sources in grouped.items():
        if not page_id:continue
        complete=True
        for source in sources:
            rows=db.rows("SELECT part_start,part_end FROM rule_work_items WHERE project_id=? AND source_id=? AND source_hash=? AND state IN ('completed_empty','completed_with_rules') ORDER BY part_start",(pid,source['id'],source_hash(source)))
            end=0
            for row in rows:
                if row['part_start']>end:break
                end=max(end,row['part_end'])
            if end<len(source['text']):complete=False;break
        if complete:
            page=db.one('SELECT * FROM pages WHERE id=?',(page_id,))
            if page and page['text'].strip():db.execute('INSERT OR REPLACE INTO rule_page_checks VALUES(?,?,?,?,?)',(page_id,page_hash(page),'complete_source_work_items','machine_covered',db.now()))
