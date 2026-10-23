"""Replay stored review decisions locally; optionally undo unsupported exclusions."""
import argparse,collections,datetime,json,pathlib,sqlite3,sys
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db,rule_governance,source_payload
from app.rule_pipeline import exact_quote
from app.rule_review_receipts import backfill_closed_receipts
from app.rule_verification import legacy_fingerprint,guards

parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
before_calls=db.one('SELECT COUNT(*) n FROM generation_runs')['n']
backup=None
if args.apply:
    tag=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    backup=root/'data/backups'/('review-integrity-repair-'+tag+'.sqlite3')
    a=sqlite3.connect(str(db.DB_PATH));b=sqlite3.connect(backup)
    try:a.backup(b)
    finally:a.close();b.close()
reports=[]
for project in db.rows('SELECT id FROM projects WHERE archived=0'):
    pid=project['id'];findings=[];restored=[];checked=0;positive_findings=[];positive_ids=set()
    human=db.rows("SELECT * FROM rules WHERE project_id=? AND state='confirmed' ORDER BY id",(pid,))
    for attempt in db.rows('SELECT * FROM rule_verification_attempts WHERE project_id=? ORDER BY created_at',(pid,)):
        value=db.loads(attempt['result'],{});rid=value.get('rule_id')
        if not rid or value.get('relation') not in ('not_a_rule','example','counterexample'):continue
        checked+=1
        rule=db.one('SELECT * FROM rules WHERE id=? AND project_id=?',(rid,pid))
        if not rule:continue
        source=db.one('SELECT s.*,f.sha256 AS file_sha256,f.name AS source_file,p.page_number FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.id=?',(rule['source_id'],))
        if not source:continue
        if exact_quote(source['text'],value.get('quote')) is not None:continue
        decision=db.one('SELECT * FROM rule_decisions WHERE rule_id=? AND restored=0 ORDER BY id DESC LIMIT 1',(rid,))
        manual=db.one("SELECT id FROM audit WHERE project_id=? AND action IN ('edit_rule','batch_review_rules','resolve_rule_conflict') AND created_at>? AND instr(detail,?)>0 LIMIT 1",(pid,attempt['created_at'],rid))
        safe=bool(rule['state']=='reference_only' and decision and db.loads(decision['after_row'])==rule and decision['source_hash']==source_payload.source_hash(source) and not manual)
        item={'rule_id':rid,'task_id':attempt['task_id'],'statement':rule['statement'],'source_file':source['source_file'],'page':source['page_number'],'issue':'negative_decision_without_locatable_evidence','safe_to_restore':safe,'restored':False}
        if args.apply and safe:
            with db.transaction():
                current=db.one('SELECT * FROM rules WHERE id=?',(rid,))
                if current==rule:
                    db.execute("UPDATE rules SET state='candidate' WHERE id=?",(rid,))
                    after=db.one('SELECT * FROM rules WHERE id=?',(rid,))
                    rule_governance.record(pid,rule,after,source,'needs_resolution',['旧版排除结论没有可定位原文，已撤回自动归档；本次只回放已有记录，没有新增模型调用'])
                    db.audit(pid,'repair_unsupported_review_exclusion',{'rule_id':rid,'before':rule,'after':after,'attempt_fingerprint':attempt['fingerprint']})
                    restored.append(rid);item['restored']=True
        findings.append(item)
    # Preserve affirmative legacy evidence as one review set. Do not infer missing
    # grade context or activate rules in a published package from an old verdict.
    for attempt in db.rows('SELECT * FROM rule_verification_attempts WHERE project_id=? ORDER BY created_at DESC',(pid,)):
        value=db.loads(attempt['result'],{});rid=value.get('rule_id')
        if value.get('relation')!='equivalent' or not rid or rid in positive_ids:continue
        rule=db.one("SELECT * FROM rules WHERE id=? AND project_id=? AND state='reference_only'",(rid,pid))
        if not rule:continue
        source=db.one('SELECT s.*,f.sha256 AS file_sha256,f.name AS source_file,p.page_number FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.id=?',(rule['source_id'],))
        if not source:continue
        decision=db.one('SELECT * FROM rule_decisions WHERE rule_id=? AND restored=0 ORDER BY id DESC LIMIT 1',(rid,))
        manual=db.one("SELECT id FROM audit WHERE project_id=? AND action IN ('edit_rule','batch_review_rules','resolve_rule_conflict') AND created_at>? AND instr(detail,?)>0 LIMIT 1",(pid,attempt['created_at'],rid))
        if not decision or decision['outcome']!='knowledge' or db.loads(decision['after_row'])!=rule or decision['source_hash']!=source_payload.source_hash(source) or manual:continue
        if legacy_fingerprint(rule,source)!=attempt['fingerprint']:continue
        quote=exact_quote(source['text'],value.get('quote'))
        if not quote or len(quote)>280 or not guards(rule['statement'],quote):continue
        outcome,_,_=rule_governance.review_one(rule,source,rule_governance.source_policy(pid),semantic=True,evidence=quote)
        positive_ids.add(rid)
        positive_findings.append({'rule_id':rid,'task_id':attempt['task_id'],'statement':rule['statement'],'source_file':source['source_file'],'page':source['page_number'],'quote':quote,'source_hash':decision['source_hash'],'attempt_fingerprint':attempt['fingerprint'],'issue':'affirmative_model_verdict_discarded_by_legacy_keyword_gate','semantic_replay_outcome':outcome,'status':'evidence_staged_without_activation'})
    summary={'project_id':pid,'negative_decisions_replayed':checked,'findings':findings,'restored_rule_ids':restored,'legacy_positive_findings':positive_findings,'legacy_positive_replay_outcomes':dict(collections.Counter(i['semantic_replay_outcome'] for i in positive_findings)),'human_confirmations_preserved':all(db.one('SELECT * FROM rules WHERE id=?',(r['id'],))==r for r in human),'review_call_models':[dict(r) for r in db.rows("SELECT model,COUNT(*) calls FROM generation_runs WHERE project_id=? AND json_extract(manifest,'$.purpose')='rule_verification' GROUP BY model",(pid,))]}
    if args.apply:
        previous=db.setting('rule_review_integrity:'+pid,{})
        prior_ids=previous.get('restored_rule_ids',[])
        summary['restored_rule_ids']=sorted(set(prior_ids)|set(restored))
        for finding in summary['findings']:
            if finding['rule_id'] in summary['restored_rule_ids']:finding['restored']=True
        summary['backfilled_task_receipts']=backfill_closed_receipts(pid)
        db.set_setting('rule_review_integrity:'+pid,summary)
    reports.append(summary)
after_calls=db.one('SELECT COUNT(*) n FROM generation_runs')['n']
report={'apply':args.apply,'backup':str(backup) if backup else None,'generated_at':db.now(),'new_generation_calls':after_calls-before_calls,'projects':reports}
if report['new_generation_calls']!=0:raise RuntimeError('Generation changed concurrently; inspect before claiming a zero-call audit')
(root/'docs/rule-review-integrity-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
