"""Local replay of saved rule judgments; no model calls or fabricated human approval."""
import hashlib,re
from collections import defaultdict
from . import db,security,source_payload,ai_autopilot as auto,rule_governance as governance
from . import rule_rebuild as rebuild,material_policy as policy
from .rule_context import EXAMPLE_CONTEXT
from .rule_pipeline import exact_quote

VERSION='rule-candidate-context-repair-v1'


@db.atomic
def extend_supersessions(pid):
    """Derived extracts of a corrected observation must leave current RAG too."""
    mapping=db.setting('document_observation_supersessions',{});added=[]
    for row in db.rows("SELECT s.id,u.locator AS unit_locator FROM sources s JOIN files f ON f.id=s.file_id JOIN knowledge_units u ON u.id=json_extract(s.locator,'$.unit_id') WHERE f.project_id=? AND s.method='document_rule_context'",(pid,)):
        parent=db.loads(row['unit_locator'],{}).get('source_id')
        if parent in mapping and row['id'] not in mapping:
            mapping[row['id']]={**mapping[parent],'parent_source_id':parent};added.append(row['id'])
    db.set_setting('document_observation_supersessions',mapping)
    if added:db.audit(pid,'derived_observations_superseded',{'source_ids':added,'version':VERSION})
    return added


@db.atomic
def register_visual_correction(pid,fid,page_id,sha,text,reason):
    """Version a transcription after actual image comparison, retaining old sources."""
    from .parsers import tokens
    page=db.one('SELECT p.*,f.sha256 AS file_sha256 FROM pages p JOIN files f ON f.id=p.file_id WHERE p.id=? AND f.id=? AND f.project_id=? AND f.active=1',(page_id,fid,pid))
    if not page or sha not in {v.get('sha256') for v in db.loads(page['images'],[])}:raise ValueError('更正图片不属于当前原页')
    if hashlib.sha256(security.safe_object(sha).read_bytes()).hexdigest()!=sha:raise ValueError('原图片内容已变化')
    if not isinstance(text,str) or not text.strip() or len(text)>12000 or len(reason)<8:raise ValueError('更正转录缺少完整文本或具体依据')
    superseded=[]
    for old in db.rows("SELECT * FROM sources WHERE page_id=? AND method IN ('windows_ocr','visual_transcription')",(page_id,)):
        loc=db.loads(old['locator'],{})
        if (loc.get('evidence_asset_sha256') or loc.get('asset_sha256'))==sha:
            if loc.get('repair_version')==VERSION and old['text']==text:return old
            superseded.append(old['id'])
    sid=db.uid();loc={'kind':'visual_observation_revision','page':page['page_number'],'evidence_asset_sha256':sha,'repair_version':VERSION,
        'verification_method':'assistant_visual_original_comparison','human_approval':False,'supersedes_source_ids':superseded,'reason':reason}
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,fid,page_id,db.dumps(loc),text,text,'rule_candidate','visual_transcription',0))
    db.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(text)))
    mapping=db.setting('document_observation_supersessions',{})
    for identity in superseded:mapping[identity]={'replacement_source_id':sid,'asset_sha256':sha,'reason':reason,'version':VERSION}
    db.set_setting('document_observation_supersessions',mapping)
    old_units=db.setting('verified_rule_image_units:'+sha,[])
    db.set_setting('verified_rule_image_units:'+sha,[u for u in old_units if u.get('source_id') not in set(superseded)]+[{'source_id':sid,'text':text,'conditional':True}])
    db.audit(pid,'assistant_visual_observation_corrected',{'source_id':sid,'superseded_source_ids':superseded,'asset_sha256':sha,'reason':reason,'human_approval':False})
    return db.one('SELECT * FROM sources WHERE id=?',(sid,))


@db.atomic
def apply_source_review(pid,review):
    """Apply an attributable assistant review of a frozen source, never human confirmation.

    This accepts reference purposes only. Independent source inspection is kept
    separate from, and does not raise, the old model's confidence.
    """
    rule=db.one('SELECT * FROM rules WHERE id=? AND project_id=?',(review['rule_id'],pid))
    if not rule or not editable(rule,('candidate','reference_only') if review.get('replacement_source_id') else ('candidate',)):raise ValueError('候选已修改或不属于可复验范围')
    source=auto.rule_source(rule)
    if (not source['active'] or source['id']!=review['source_id'] or source_payload.source_hash(source)!=review['source_hash'] or source['file_sha256']!=review['file_sha256']
        or hashlib.sha256(rule['statement'].encode()).hexdigest()!=review['statement_hash']):raise ValueError('复验后原文、候选或原件已变化')
    purpose=review['purpose'];method=review['method'];reason=review['reason']
    if purpose not in ('knowledge','heading','noise','conditional','example','counterexample') or len(reason)<8:raise ValueError('仅可采用有具体依据的参考用途复验')
    loc=db.loads(source['locator'],{});unit=db.one('SELECT origin,text FROM knowledge_units WHERE id=?',(loc.get('unit_id'),))
    if method=='assistant_native_text_review':
        if not unit or unit['origin']!='pptx_slide' or unit['text']!=source['text']:raise ValueError('不是已读取的原生幻灯片文字')
    elif method=='assistant_native_pdf_review':
        if not unit or unit['origin']!='pdfium_text_order' or unit['text']!=source['text'] or review.get('reviewed_page')!=source['page_number']:raise ValueError('PDF 复验页码或原文不一致')
    elif method=='assistant_visual_original_comparison':
        sha=review.get('visual_asset_sha256');page=db.one('SELECT images FROM pages WHERE id=?',(source['page_id'],))
        if not sha or not page or sha not in {v.get('sha256') for v in db.loads(page['images'],[])}:raise ValueError('复验图片不属于候选原页')
        if not review.get('compared_original'):raise ValueError('未记录原图比较')
    else:raise ValueError('复验方法无效')
    quote=review['quote']
    if review.get('replacement_source_id'):
        if method!='assistant_visual_original_comparison':raise ValueError('替换转录须对照原图')
        replacement=auto.rule_source({'source_id':review['replacement_source_id'],'project_id':pid})
        rloc=db.loads(replacement['locator'],{}) if replacement else {}
        if not replacement or replacement['file_id']!=source['file_id'] or replacement['page_id']!=source['page_id'] or replacement['method']!='visual_transcription' or rloc.get('evidence_asset_sha256')!=review['visual_asset_sha256'] or rloc.get('repair_version')!=VERSION or replacement['text']!=quote:
            raise ValueError('修正转录与所核对原图片不一致')
        source=replacement
        db.execute('UPDATE rules SET statement=?,source_id=? WHERE id=?',(quote,source['id'],rule['id']))
    elif quote!=rule['statement'] or not (quote in source['text']):raise ValueError('复验引句与候选或原文不一致')
    old=auto.receipt(pid,'rule',rule['id']);modality=purpose if purpose in ('example','counterexample') else 'pending'
    db.execute("UPDATE rules SET state='reference_only',modality=? WHERE id=?",(modality,rule['id']))
    after=update_note(db.one('SELECT * FROM rules WHERE id=?',(rule['id'],)),reason)
    outcome='conditional_reference' if purpose=='conditional' else 'example' if purpose in ('example','counterexample') else 'knowledge'
    governance.record(pid,rule,after,source,outcome,['助手原文复验（非人工确认）：'+reason,'原文依据：'+quote])
    auto.save_receipt(pid,'rule',rule['id'],{'fingerprint':auto.rule_fingerprint(after,source),'state':'completed','completed':True,'ai_covered':True,
        'purpose':purpose,'reason':reason,'quote':quote,'decision_method':method,'confidence':None,'previous_model_review':old,
        'review_evidence':review,'before':rule,'after':after,'human_approval':False,'repair_version':VERSION,'model':'codex_assistant'})
    db.audit(pid,'assistant_rule_source_review',{'before':rule,'after':after,'review':review,'human_approval':False})
    return after


def context_source(source,text,locator):
    from .parsers import tokens
    locator={**locator,'repair_version':VERSION,'file_sha256':source['file_sha256']}
    key=hashlib.sha256(db.dumps([source['file_id'],text,locator]).encode()).hexdigest()
    prior=db.one("SELECT * FROM sources WHERE file_id=? AND method='document_rule_context' AND json_extract(locator,'$.repair_context_key')=?",(source['file_id'],key))
    if prior:return auto.rule_source({'source_id':prior['id'],'project_id':db.one('SELECT project_id FROM files WHERE id=?',(source['file_id'],))['project_id']})
    sid=db.uid();locator['repair_context_key']=key
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,source['file_id'],source['page_id'],db.dumps(locator),text,text,'rule_candidate','document_rule_context',0))
    db.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(text)))
    return {**source,'id':sid,'text':text,'normalized':text,'locator':db.dumps(locator),'method':'document_rule_context','verified':0}


def editable(rule,states=('candidate',)):
    if rule['state'] not in states:return False
    for event in db.rows("SELECT action,detail FROM audit WHERE project_id=? AND action IN ('edit_rule','resolve_rule_conflict') AND instr(detail,?)>0",(rule['project_id'],rule['id'])):
        value=db.loads(event['detail'],{})
        if event['action']=='resolve_rule_conflict' or any(k in value.get('changes',{}) for k in ('statement','source_id','state','modality')):return False
    return True


def saved_items(pid,job):
    values=defaultdict(list)
    for row in db.rows('SELECT value FROM settings WHERE key LIKE ?',(rebuild.key(job,'batch:')+'%',)):
        batch=db.loads(row['value'],{})
        if not batch.get('complete'):continue
        lookup={u['id']:u for u in batch.get('units',[])}
        for item in batch.get('items',[]):
            if item.get('unit_id') not in lookup:continue
            unit=lookup[item['unit_id']];record=(item,unit,batch)
            signature=db.dumps([item,unit])
            if not any(db.dumps([v[0],v[1]])==signature for v in values[item['unit_id']]):values[item['unit_id']].append(record)
    return values


def original_judgments(item,batch,unit):
    """Older caches predate model_judgment; read the completed response object."""
    if item.get('model_judgment'):return [item['model_judgment']]
    run_id=item.get('_run_id') or batch.get('run_id')
    run=db.one("SELECT * FROM generation_runs WHERE id=? AND status='completed'",(run_id,)) if run_id else None
    if not run:return []
    meta=db.loads(run['manifest'],{})
    if meta.get('purpose')!='document_rule_rebuild':return []
    try:
        from . import provider
        result=provider.parse_json(security.safe_object(meta['output_blob']).read_text(encoding='utf-8'))
        return [i for i in result.get('items',[]) if i.get('unit_id')==unit['id']]
    except (OSError,KeyError,TypeError,ValueError):return []


def update_note(rule,message):
    note=rule['decision_note']+'；原页复验：'+message
    db.execute('UPDATE rules SET decision_note=? WHERE id=?',(note,rule['id']))
    return db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))


def grade_reference(rule,source,rows,item,job,unit):
    matches=[row for row in rows if source.get('page_number') in row['pages'] and exact_quote(row['text'],rule['statement'])]
    if not matches:
        # Native text order concatenates adjacent grade/score/body columns.
        # Match each original field against its tagged cell, without deleting
        # arbitrary text or guessing a grade from the body.
        prefix=re.match(r'^\s*([ABCD])\s*级\s*(?:[（(][^）)]*[)）])?\s*(\d+)\s*[-–—~～]\s*(\d+)\s*分\s*',rule['statement'])
        if prefix:
            body=rule['statement'][prefix.end():]
            score=f'{prefix[2]}-{prefix[3]}分'
            matches=[row for row in rows if source.get('page_number') in row['pages'] and row['grade']==prefix[1]
                and ''.join(row['score'].split())==score and exact_quote(row['text'],body)]
    if len(matches)!=1:
        # Whole PDF pages can contain two grade rows; retain both with their
        # conditions rather than attributing an isolated cell to one grade.
        if rule['statement'].strip()!=unit['text'].strip() or unit['origin'] not in ('pdfium_text_order','pdf_tagged_table','tagged_table'):return False
        matches=[row for row in rows if source.get('page_number') in row['pages']]
        if not matches:return False
    row=matches[0];text='\n\n'.join(r['text'] for r in matches)
    # The complete tagged row, including the grade and score, is the decision evidence.
    grades=[{'table_row':r['id'],'grade':r['grade'],'score':r['score'],'pages':r['pages'],'cells':r['cells']} for r in matches]
    new_source=context_source(source,text,{'kind':'tagged_grade_context','table_row':row['id'],'grade':row['grade'],'score':row['score'],
         'pages':sorted({p for r in matches for p in r['pages']}),'grade_rows':grades,'parent_source_id':source['id'],'generation_id':job,'read_scope':'complete_tagged_grade_rows'})
    before=rule
    db.execute("UPDATE rules SET statement=?,source_id=?,state='reference_only',modality='pending' WHERE id=?",(text,new_source['id'],rule['id']))
    after=update_note(db.one('SELECT * FROM rules WHERE id=?',(rule['id'],)),'按原件表格结构补齐等级、分数范围和跨页内容，保留条件参考')
    reason='结构化评分表中的完整等级行；等级与分数适用条件已保留，不作为当前论文的无条件要求'
    governance.record(rule['project_id'],before,after,new_source,'conditional_reference',[reason,'原文依据：'+text])
    auto.save_receipt(rule['project_id'],'rule',rule['id'],{'fingerprint':auto.rule_fingerprint(after,new_source),'completed':True,'state':'completed',
        'purpose':'conditional','decision_method':'tagged_source_structure','confidence':None,'original_model_judgment':item,
        'ai_covered':bool(item.get('_run_id')),'run_id':item.get('_run_id'),'task_id':job,'quote':text,'reason':reason,
        'repair_version':VERSION,'human_approval':False,'before':before,'after':after,'grade_rows':grades})
    return True


def replay(pid,job):
    """Revalidate only unchanged, unconfirmed records from one adopted generation."""
    from .rule_generations import visible
    from .pdf_rule_contexts import extract
    from .rule_text_integrity import inspect_rule
    security.require_project(pid);saved=saved_items(pid,job);fixed=[];remaining=[];tables={};native_pages=defaultdict(set)
    for options in saved.values():
        for _,unit,batch in options:
            if unit['origin']=='pptx_slide':native_pages[(batch['file_id'],unit['locator'].get('page'))].add(unit['text'])
    candidates=visible(pid,db.rows("SELECT * FROM rules WHERE project_id=? AND state='candidate' ORDER BY created_at,id",(pid,)))
    for before in candidates:
        source=auto.rule_source(before);loc=db.loads(source.get('locator'),{}) if source else {}
        if loc.get('generation_id')!=job or not editable(before):continue
        options=[v for v in saved.get(loc.get('unit_id'),[]) if v[0]['quote']==before['statement']]
        adopted=[v for v in options if before['decision_note'].startswith('按完整原件重新生成；'+v[0]['reason'])]
        if adopted:options=adopted
        # Conflicting cached judgments are not resolved by choosing the highest score.
        signatures={db.dumps({k:v[0].get(k) for k in ('purpose','quote','confidence','reason')}) for v in options}
        if len(signatures)!=1:
            remaining.append({'id':before['id'],'reason':'已保存判断缺失或不一致，保留待核对'});continue
        item,unit,batch=options[0];item={**item};file=db.one('SELECT * FROM files WHERE id=?',(source['file_id'],))
        parse=db.one('SELECT file_hash FROM knowledge_parses WHERE id=?',(unit.get('parse_id'),))
        if not file or not file['active'] or source['text']!=unit['text'] or parse and parse['file_hash']!=file['sha256']:
            remaining.append({'id':before['id'],'reason':'原文或原件已变化，未重放旧判断'});continue
        if policy.source_kind(file)=='grading_criteria':
            if file['id'] not in tables:tables[file['id']]=extract(file)
            with db.transaction():
                if db.one('SELECT * FROM rules WHERE id=?',(before['id'],))!=before:continue
                if grade_reference(before,source,tables[file['id']],item,job,unit):fixed.append({'id':before['id'],'method':'complete_grade_context'});continue
        choice=item
        if item['purpose']=='uncertain' and item.get('confidence')==0:
            raw=original_judgments(item,batch,unit);recovered=[]
            for original in raw:
                try:
                    values,_=rebuild.validate({'covered_unit_ids':batch['target_unit_ids'],'items':[dict(original,unit_id=unit['id'])]},batch)
                    if values and values[0]['purpose']!='uncertain':recovered.append(values[0])
                except (ValueError,TypeError):pass
            if len(recovered)==1:choice={**recovered[0],'_run_id':item.get('_run_id') or batch.get('run_id')}
        if choice['purpose'] in ('requirement','recommendation') and (choice.get('condition_notes') or unit['origin'] not in ('pptx_slide','pdfium_text_order','ooxml_structure','native_extraction','tagged_table')):
            choice={**choice,'purpose':'conditional'}
        evidence=unit['text']
        if choice['purpose'] in ('example','counterexample'):
            peers=native_pages.get((file['id'],unit['locator'].get('page')),set())
            if peers:evidence+='\n[同页原生文字]\n'+'\n'.join(sorted(peers))
        try:
            with db.transaction():
                live=db.one('SELECT * FROM rules WHERE id=?',(before['id'],))
                if live!=before:raise ValueError('候选在修复期间已被修改')
                after=before
                # Replace only a fallback unit with a newly literal validated selection.
                if choice['quote']!=before['statement']:
                    if item['purpose']!='uncertain' or not exact_quote(unit['text'],choice['quote']):raise ValueError('修复引文未逐字定位')
                    db.execute('UPDATE rules SET statement=? WHERE id=?',(choice['quote'],before['id']))
                    after=db.one('SELECT * FROM rules WHERE id=?',(before['id'],))
                if choice['purpose'] in ('requirement','recommendation') and inspect_rule(after,source)['needs_attention'] and unit['origin']=='pptx_slide' and exact_quote(unit['text'],after['statement']):
                    db.execute('UPDATE rules SET statement=? WHERE id=?',(unit['text'],before['id']))
                    after=db.one('SELECT * FROM rules WHERE id=?',(before['id'],));choice={**choice,'quote':unit['text'],'purpose':'conditional','reason':'原条款在冒号处截断，已保留完整原生页的对象、选择数量及后续列表；'+choice['reason']}
                if choice['purpose']=='uncertain':raise ValueError(choice['reason'])
                auto.apply_rule(pid,after,source,choice,evidence,job,choice.get('_run_id') or batch.get('run_id'),'saved-model-response')
                updated=db.one('SELECT * FROM rules WHERE id=?',(before['id'],))
                updated=update_note(updated,'已复用保存的判断并通过原文定位与用途校验')
                rec=auto.receipt(pid,'rule',before['id'])
                outcome='machine_verified' if updated['state']=='machine_verified' else 'conditional_reference' if rec['purpose']=='conditional' else 'example' if rec['purpose'] in ('example','counterexample') else 'knowledge'
                governance.record(pid,before,updated,source,outcome,['保存的模型响应经原文复验：'+choice['reason'],'原文依据：'+choice['quote']])
                auto.save_receipt(pid,'rule',before['id'],{**rec,'repair_version':VERSION,'human_approval':False,'original_model_judgment':item,'original_before':before,'after':updated})
                db.audit(pid,'rule_candidate_context_repaired',{'version':VERSION,'before':before,'after':updated,'source_hash':source_payload.source_hash(source),'method':'saved_response_revalidation'})
                fixed.append({'id':before['id'],'method':'saved_response_revalidation'})
        except ValueError as error:
            reason=str(error)
            if unit['origin']=='windows_ocr':reason='图片转录仍需对照原图；'+reason
            with db.transaction():
                live=db.one('SELECT * FROM rules WHERE id=?',(before['id'],))
                if live==before:
                    governance.record(pid,before,before,source,'needs_resolution',[reason])
                    auto.save_receipt(pid,'rule',before['id'],{'fingerprint':auto.rule_fingerprint(before,source),'completed':False,'state':'blocked','purpose':'uncertain',
                        'reason':reason,'ai_covered':bool(item.get('_run_id')),'original_model_judgment':item,'run_id':item.get('_run_id'),'task_id':job,'repair_version':VERSION,'human_approval':False})
            remaining.append({'id':before['id'],'reason':reason})
    result={'version':VERSION,'generation_id':job,'fixed':fixed,'remaining':remaining,'fixed_count':len(fixed),'remaining_count':len(remaining)}
    db.set_setting('rule_candidate_repair:'+pid,result);db.audit(pid,'rule_candidate_context_repair_completed',result)
    return result
