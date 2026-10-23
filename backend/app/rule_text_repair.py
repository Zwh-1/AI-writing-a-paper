"""Restore only locally provable text; preserve human edits and immutable packages."""
from . import db,rule_text_integrity as integrity,rule_governance


def affected_snapshot(pid,snapshot):
    quarantine=set(db.setting('rule_text_integrity:'+pid,{}).get('quarantined_rule_ids',[]))
    result=[]
    for rule in snapshot:
        if rule['id'] not in quarantine:continue
        live=db.one('SELECT * FROM rules WHERE id=? AND project_id=?',(rule['id'],pid))
        if not live or live['state'] not in ('confirmed','machine_verified') or live['statement']!=rule['statement']:result.append(rule['id'])
    return result


def current_published_affected(pid):
    package=db.one('SELECT rules FROM rule_packages WHERE id=(SELECT rule_package_id FROM projects WHERE id=?)',(pid,))
    return affected_snapshot(pid,db.loads(package['rules'],[]) if package else [])


@db.atomic
def apply_visual_corrections(pid,proofs):
    """Apply explicit image transcriptions recorded by this audit, never OCR guesses."""
    from fastapi import HTTPException
    from .parsers import tokens
    corrected=[]
    for proof in proofs:
        rule=db.one('SELECT * FROM rules WHERE id=? AND project_id=?',(proof['rule_id'],pid))
        if not rule or rule['statement']!=proof['before_statement']:raise HTTPException(409,'条款已变化，未覆盖图像转录')
        if rule['state'] in ('confirmed','rejected') or db.one("SELECT id FROM audit WHERE project_id=? AND action IN ('edit_rule','batch_review_rules','resolve_rule_conflict') AND instr(detail,?)>0",(pid,rule['id'])):raise HTTPException(409,'保留人工或归并记录，未覆盖图像转录')
        source=integrity.source_for_rule(rule)
        asset=db.one("SELECT id FROM sources WHERE page_id=? AND json_extract(locator,'$.asset_sha256')=? LIMIT 1",(source['page_id'],proof['asset_sha256']))
        if not asset:raise HTTPException(409,'图像依据不属于此条款的原始页面')
        text=proof['after_statement']
        if not text.strip() or len(text)>12000:raise HTTPException(422,'图像转录内容无效')
        sid=db.uid();location={'kind':'rule_excerpt','page':source['page_number'],'parent_source_ids':[rule['source_id']],'repair':'complete_rule_context','evidence_asset_sha256':proof['asset_sha256']}
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,source['file_id'],source['page_id'],db.dumps(location),text,text,'rule_candidate','visual_transcription',0))
        db.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(text)))
        db.execute("UPDATE rules SET statement=?,source_id=?,state='candidate',decision_note=? WHERE id=?",(text,sid,(rule['decision_note']+'\n图像核对转录：'+proof['reason']).strip(),rule['id']))
        after=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))
        rule_governance.record(pid,rule,after,integrity.source_for_rule(after),'needs_resolution',['按原始图片校正 OCR 符号，候选未冒充人工确认'])
        db.audit(pid,'correct_rule_visual_transcription',{'rule_id':rule['id'],'before':rule,'after':after,'asset_sha256':proof['asset_sha256'],'reason':proof['reason']})
        corrected.append(rule['id'])
    record=db.setting('rule_text_integrity:'+pid,{})
    record['visual_corrected_rule_ids']=sorted(set(record.get('visual_corrected_rule_ids',[]))|set(corrected))
    db.set_setting('rule_text_integrity:'+pid,record)
    return corrected


def scan(pid):
    result=[]
    for rule in db.rows('SELECT * FROM rules WHERE project_id=? ORDER BY created_at,id',(pid,)):
        source=integrity.source_for_rule(rule)
        if not source:continue
        info=integrity.inspect_rule(rule,source)
        if not info['needs_attention']:continue
        manual=db.one("SELECT id FROM audit WHERE project_id=? AND action IN ('edit_rule','batch_review_rules','resolve_rule_conflict') AND instr(detail,?)>0 LIMIT 1",(pid,rule['id']))
        editable=rule['state'] not in ('confirmed','rejected') and not manual
        result.append({'rule':rule,'source':source,'info':info,'safe_to_repair':bool(info['replacement'] and editable),'safe_to_quarantine':bool(rule['state']=='machine_verified' and editable)})
    return result


@db.atomic
def apply(pid,findings=None):
    findings=scan(pid) if findings is None else findings
    repaired=[];quarantined=[]
    for finding in findings:
        before=finding['rule'];source=finding['source'];info=finding['info']
        if not finding['safe_to_repair'] and not finding['safe_to_quarantine']:continue
        if db.one('SELECT * FROM rules WHERE id=?',(before['id'],))!=before:continue
        live=integrity.source_for_rule(before)
        if live!=source:continue
        replacement=info['replacement'] if finding['safe_to_repair'] else None
        target=source
        if replacement:
            # A recovered span may cross old 1000-character search-source boundaries.
            candidates=db.rows('SELECT * FROM sources WHERE page_id=?',(source['page_id'],))
            target=next((s for s in candidates if integrity.spans(s['text'],replacement)),None)
            if target is None:
                from .parsers import tokens
                sid=db.uid();location={'kind':'rule_excerpt','page':source['page_number'],'parent_source_ids':[before['source_id']],'repair':'complete_rule_context'}
                db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,source['file_id'],source['page_id'],db.dumps(location),replacement,replacement,'rule_candidate',source['method'],0))
                db.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(replacement)))
                target=integrity.source_for_rule({**before,'source_id':sid})
            else:target=integrity.source_for_rule({**before,'source_id':target['id']})
            note=(before['decision_note']+'\n完整性修复：从同页原文补回遗漏内容；原片段及旧判定已留档，未新增模型请求。').strip()
            db.execute("UPDATE rules SET statement=?,source_id=?,state='candidate',decision_note=? WHERE id=?",(replacement,target['id'],note,before['id']))
            repaired.append(before['id'])
        else:db.execute("UPDATE rules SET state='candidate' WHERE id=?",(before['id'],))
        if before['state']=='machine_verified':quarantined.append(before['id'])
        after=db.one('SELECT * FROM rules WHERE id=?',(before['id'],))
        rule_governance.record(pid,before,after,target,'needs_resolution',[info['issue'],'旧版片段审核结论已撤回；补全内容仍作为候选保留'])
        db.audit(pid,'repair_rule_text_integrity',{'rule_id':before['id'],'before':before,'after':after,'context':info['context'],'source_id':source['id'],'repaired':bool(replacement),'quarantined':before['state']=='machine_verified'})
    previous=db.setting('rule_text_integrity:'+pid,{})
    repaired=sorted(set(previous.get('repaired_rule_ids',[]))|set(repaired));quarantined=sorted(set(previous.get('quarantined_rule_ids',[]))|set(quarantined))
    package=db.one('SELECT rules FROM rule_packages WHERE id=(SELECT rule_package_id FROM projects WHERE id=?)',(pid,))
    published={r['id'] for r in db.loads(package['rules'],[])} if package else set()
    result={'checked_rules':db.one('SELECT COUNT(*) n FROM rules WHERE project_id=?',(pid,))['n'],'findings_count':max(len(findings),previous.get('findings_count',0)),'repaired_rule_ids':repaired,'quarantined_rule_ids':quarantined,'published_affected_rule_ids':sorted(published&set(quarantined)),'remaining_findings_count':len(scan(pid)),'updated_at':db.now()}
    db.set_setting('rule_text_integrity:'+pid,result)
    return result
