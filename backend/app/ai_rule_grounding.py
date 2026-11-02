"""Ground semantic summaries in complete source units and tagged rubric rows."""
import re,unicodedata
from collections import Counter
from . import db,provider,security,source_payload,rule_governance

VERSION='autopilot-semantic-grounding-v1'
CHECKS=('numbers_preserved','negation_preserved','scope_preserved','grade_preserved','strength_preserved')
INSTRUCTION='''补充复核任务 rules_grounding：判断候选的完整语义是否由给定原文支持，可选择多个原文单元，不能仅因不是逐字引用判失败。
返回 {"decisions":[{"id":"给定ID","purpose":"requirement|recommendation|conditional|knowledge|example|counterexample|heading|noise|uncertain","relation":"entailed|contradicted|uncertain","evidence_ids":["给定单元ID"],"numbers_preserved":true,"negation_preserved":true,"scope_preserved":true,"grade_preserved":true,"strength_preserved":true,"reason":"说明候选与原文对应关系以及条件","confidence":0.98}]}。
逐个核验全部主张、数字及单位、否定、范围、等级、强弱；不支持的数字/单位或错误范围必须contradicted或uncertain。OCR错字不得猜测纠正。原文有多条要求时允许忠实摘取其中完整独立的一条，不能遗漏影响该条的限制或例外。条件、等级、对象在其他表格单元时需一并引用；不得跨等级拼接。tagged_table单元是依据原件标签重建的完整等级行，保留跨页单元格关联。若同页同时有A/B，不得把A级标题与B级正文组合。
评分细则通常conditional：项目未指定必须达到某个等级，保留该等级及其标准的条件参考。普通课程规范可recommendation。knowledge仅指知识讲解，heading仅指当前候选是标题；example/counterexample必须确有示例上下文。候选与原文矛盾时不能改判为知识或条件参考来掩盖矛盾。每个ID恰好一次。'''


def grades(text):return {g.translate(str.maketrans('ＡＢＣＤ','ABCD')).upper() for g in re.findall(r'([ABCDＡＢＣＤ])\s*级',text,re.I)}


def numbers(text):
    text=unicodedata.normalize('NFKC',text)
    def chinese(raw):
        digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits.update({'〇':0,'两':2})
        if all(c in digits for c in raw):return str(int(''.join(str(digits[c]) for c in raw)))
        value=section=total=0
        for char in raw:
            if char in digits:value=digits[char]
            else:
                unit={'十':10,'百':100,'千':1000,'万':10000}[char]
                if unit==10000:total+=(section+value)*unit;section=value=0
                else:section+=(value or 1)*unit;value=0
        return str(total+section+value)
    text=re.sub(r'([零〇一二两三四五六七八九十百千万]+)(?=篇|字|个|项|倍|年|页|厘米|毫米|步|号|行)',lambda m:chinese(m[1]),text)
    text=re.sub(r'\s+','',text).replace('％','%')
    text=re.sub(r'^第?\d+[条项、．.：:](?!\d)','',text)
    return Counter((str(float(v)).removesuffix('.0'),u.lower()) for v,u in re.findall(r'(\d+(?:\.\d+)?)(字符|厘米|毫米|篇|字|个|项|倍|%|年|页|cm|mm|pt|磅|步|号|行)?',text,re.I))


def units(source,statement):
    from .ai_autopilot import context,evidence_units
    text,page=context(source,statement)
    result=evidence_units('rules',{'statement':statement,'text':text})
    if text and all(u['text']!=text for u in result):result.append({'id':'C0','text':text,'kind':'source_context'})
    if page and page!=text:result.append({'id':'P0','text':page,'kind':'page_context'})
    file=db.one('SELECT * FROM files WHERE id=?',(source['file_id'],)) if source['file_id'] else None
    if file and file['name'].lower().endswith('.pdf') and grades(statement):
        from . import pdf_rule_contexts
        wanted=grades(statement)
        rows=[r for r in pdf_rule_contexts.extract(file) if r['grade'] in wanted]
        headings={r['heading'] for r in rows if r['heading'] in statement}
        if headings:rows=[r for r in rows if r['heading'] in headings]
        def score(row):return len(set(re.findall(r'[\u3400-\u9fff]{2}',statement))&set(re.findall(r'[\u3400-\u9fff]{2}',row['text'])))
        for row in sorted(rows,key=score,reverse=True)[:2]:
            result.append({'id':row['id'],'text':row['text'],'kind':'tagged_table','grade':row['grade'],'heading':row['heading'],'pages':row['pages'],'file_id':file['id'],'file_sha256':file['sha256'],'extractor':row['version']})
    return result


def build(rule,source,fingerprint,feedback=''):
    evidence=units(source,rule['statement'])
    item={'id':'R0','_identity':rule['id'],'_fingerprint':fingerprint,'statement':rule['statement'],'modality':rule['modality'],'source_file':source['source_file'],'source_role':source.get('role','manual'),'authority':rule_governance.source_policy(rule['project_id']).get(source['file_id'],'course'),'evidence_units':evidence,'file_ids':[source['file_id']] if source['file_id'] else [],'previous_validation_error':feedback,'grounding_version':VERSION}
    from .ai_autopilot import SYSTEM
    if source_payload.token_upper_bound(SYSTEM+'\n'+INSTRUCTION,db.dumps(item))+4096>int(provider.config()['context_limit']):
        raise ValueError('完整原文证据超过接口上下文上限，需分步阅读，保留例外；未删减证据')
    return item


def context_hash(item):
    from .ai_autopilot import digest
    return digest([VERSION,{key:item[key] for key in ('statement','modality','authority','evidence_units')}])


def restore_literal_context(rule,source):
    """Repair a unique literal truncation, preserving user edits and adoption state."""
    from .rule_text_integrity import inspect_rule
    from .rule_pipeline import exact_quote
    replacement=inspect_rule(rule,source).get('replacement')
    if not replacement or len(replacement)>12000 or not source.get('active'):return rule
    if not exact_quote(source['text']+'\n'+(source.get('page_text') or ''),replacement):return rule
    for event in db.rows("SELECT detail FROM audit WHERE project_id=? AND action='edit_rule' AND instr(detail,?)>0",(rule['project_id'],rule['id'])):
        if 'statement' in db.loads(event['detail'],{}).get('changes',{}):return rule
    with db.transaction():
        if db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))!=rule:return rule
        db.execute('UPDATE rules SET statement=? WHERE id=?',(replacement,rule['id']))
        after=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))
        db.audit(rule['project_id'],'autopilot_restore_literal_context',{'rule_id':rule['id'],'before':rule,'after':after,'source_id':source['id'],'source_hash':source_payload.source_hash(source),'reason':'同一原文中唯一可定位的完整上下文；保留采纳状态，等待AI复核'})
        rule_governance.record(rule['project_id'],rule,after,source,'needs_resolution',['原文唯一完整上下文已恢复，等待AI复核；未推定为审核通过'])
        return after


def validate(rule,source,item,decision):
    from .ai_autopilot import confidence,PURPOSES
    from .rule_pipeline import exact_quote
    from .rule_text_integrity import inspect_rule
    if decision.get('purpose') not in PURPOSES or len(str(decision.get('reason','')).strip())<8:raise ValueError('补充复核缺少用途或具体依据')
    if decision.get('relation')!='entailed' or decision.get('purpose')=='uncertain':raise ValueError('补充原文仍未证明候选的完整含义：'+str(decision.get('reason','')))
    if not all(decision.get(key) is True for key in CHECKS):raise ValueError('补充复核未通过数字、否定、范围、等级或强弱检查')
    formal=rule_governance.source_policy(rule['project_id']).get(source['file_id'])=='binding' or rule['state']=='confirmed' and rule['modality']=='mandatory'
    minimum=.95 if formal else .9 if decision['purpose'] in ('requirement','recommendation') else .8
    if not confidence(decision,minimum):raise ValueError('补充语义复核置信度不足')
    ids=decision.get('evidence_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=4 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise ValueError('补充证据编号无效')
    lookup={u['id']:u for u in item['evidence_units']}
    if any(i not in lookup for i in ids):raise ValueError('补充证据编号不在本次原文范围内')
    selected=[lookup[i] for i in ids];text='\n'.join(u['text'] for u in selected)
    for unit in selected:
        if unit.get('kind')=='tagged_table':
            from . import pdf_rule_contexts
            file=db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1',(unit['file_id'],rule['project_id']))
            if not file or file['id']!=source['file_id'] or file['sha256']!=unit['file_sha256'] or not security.file_allows_remote(file['id'],rule['project_id']):raise ValueError('补充表格原件或权限已变化')
            actual=next((r for r in pdf_rule_contexts.extract(file) if r['id']==unit['id']),None)
            if not actual or actual['text']!=unit['text'] or actual['grade']!=unit['grade']:raise ValueError('补充表格内容与原件不一致')
        elif not exact_quote(source['text']+'\n'+(source.get('page_text') or ''),unit['text']):raise ValueError('补充依据不能在当前来源定位')
    claimed=numbers(rule['statement']);available=numbers(text)
    for (value,unit),count in claimed.items():
        candidates=sum(n for (v,u),n in available.items() if v==value and (u==unit or not unit))
        if candidates<count:raise ValueError('候选数值或单位没有对应原文依据：'+value+unit)
    wanted=grades(rule['statement']);found=grades(text)
    if wanted and found!=wanted:raise ValueError('候选与引用跨越不同评分等级，不能合并')
    grade_context=any(u.get('kind')=='tagged_table' or re.match(r'\s*[ABCDＡＢＣＤ]\s*级\s*(?:标准|[（(])',u['text'],re.I) for u in selected)
    grade_reference=bool(wanted or grade_context and decision['purpose'] in ('requirement','recommendation'))
    if decision['purpose'] in ('requirement','recommendation','conditional') and re.search(r'(?:违反|应当|必须|不少于|不超过|属于|固定行距)\s*[，,：:]?$',rule['statement']):raise ValueError('候选规范缺少句末对象或数值，不能按残片通过')
    if decision['purpose'] in ('requirement','recommendation') and re.match(r'\s*(?:段前|段后|固定行距)',rule['statement']) and not re.search(r'摘要|正文|标题|目录|参考文献|图|表|全篇|全文|所有',rule['statement']):raise ValueError('格式数值缺少适用对象，不能作为全局格式要求')
    if inspect_rule(rule,source)['needs_attention']:raise ValueError('候选本身仍有截断或不完整条件，保留原文待修复')
    return {'version':VERSION,'relation':'entailed','checks':{key:True for key in CHECKS},'evidence_units':selected,'quote':text,'evidence_ids':ids,'resolved_purpose':'conditional' if grade_reference else decision['purpose'],'purpose_policy':'评分语义已经AI核实；项目未选择目标等级，按程序政策保留条件参考' if grade_reference and decision['purpose']!='conditional' else None}


def review_pending(task,job_id):
    from .rule_generations import visible
    from . import tasks
    from . import ai_autopilot as a
    from .rule_pipeline import finalize_billing
    pid=task['project_id'];pending=[];notes=[];completed=0
    for rule in visible(pid,db.rows('SELECT * FROM rules WHERE project_id=? ORDER BY created_at,id',(pid,))):
        source=a.rule_source(rule);fp=a.rule_fingerprint(rule,source);old=a.receipt(pid,'rule',rule['id'])
        if old.get('state') in ('unknown','conflict'):continue
        if old.get('fingerprint')==fp and old.get('completed'):continue
        if not source or not source.get('active') or a.unknown_request(pid,fp):continue
        if source['file_id'] and not security.file_allows_remote(source['file_id'],pid):continue
        rule=restore_literal_context(rule,source);fp=a.rule_fingerprint(rule,source)
        try:
            item=build(rule,source,fp,old.get('reason',''));restored=False
            for choice,run_id,model in a.saved_decisions(pid,'rules_grounding',rule['id'],fp):
                try:
                    proof=validate(rule,source,item,choice)
                    a.apply_rule(pid,rule,source,{**choice,'quote':proof['quote']},proof['quote'],task['id'],run_id,model,grounding=proof)
                    completed+=1;restored=True;break
                except ValueError:continue
            if restored:continue
            if old.get('fingerprint')==fp and old.get('grounding_attempt',{}).get('context_hash')==context_hash(item):continue
            pending.append((rule,source,item))
        except (ValueError,OSError) as error:notes.append({'id':rule['id'],'reason':str(error)})
    chunks=[];chunk=[]
    for entry in pending:
        proposed=chunk+[entry]
        if chunk and (len(proposed)>4 or source_payload.token_upper_bound(a.SYSTEM+'\n'+INSTRUCTION,db.dumps({'stage':'rules_grounding','items':[v[2] for v in proposed]}))+4096>int(provider.config()['context_limit'])):chunks.append(chunk);chunk=[]
        chunk.append(entry)
    if chunk:chunks.append(chunk)
    for index,chunk in enumerate(chunks):
        tasks.event(task['id'],f'AI托管：完整原文补充复核 {index+1}/{len(chunks)}',92)
        items=[{**entry[2],'id':'R'+str(i)} for i,entry in enumerate(chunk)]
        try:
            value,run_id,model=a.request(task,job_id,'rules_grounding',items);choices=a.decisions(value,items)
            for i,(rule,source,_) in enumerate(chunk):
                choice=choices.get('R'+str(i),{})
                try:
                    proof=validate(rule,source,items[i],choice)
                    a.apply_rule(pid,rule,source,{**choice,'quote':proof['quote']},proof['quote'],task['id'],run_id,model,grounding=proof)
                    completed+=1
                except ValueError as error:
                    old=a.receipt(pid,'rule',rule['id'])
                    chosen=choice.get('evidence_ids',[])
                    evidence=[u for u in items[i]['evidence_units'] if isinstance(chosen,list) and u['id'] in chosen]
                    covered=bool(choice.get('purpose') in a.PURPOSES and evidence and len(str(choice.get('reason','')))>=4)
                    a.save_receipt(pid,'rule',rule['id'],{**old,'fingerprint':a.rule_fingerprint(rule,source),'completed':False,'ai_covered':covered or old.get('fingerprint')==a.rule_fingerprint(rule,source) and old.get('ai_covered',False),'state':'uncertain' if choice.get('relation') in ('uncertain','contradicted') else 'blocked','reason':str(error),'grounding_attempt':{'decision':choice,'evidence_units':evidence,'context_hash':context_hash(items[i]),'task_id':task['id'],'run_id':run_id,'model':model}})
                    notes.append({'id':rule['id'],'reason':str(error)})
        except Exception as error:
            if isinstance(error,InterruptedError):raise
            for rule,source,_ in chunk:
                old=a.receipt(pid,'rule',rule['id']);a.save_receipt(pid,'rule',rule['id'],{**old,'fingerprint':a.rule_fingerprint(rule,source),'completed':False,'state':'unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'blocked','reason':provider.redact(error),'task_id':task['id']})
            from .summary_billing import BudgetExceeded
            if isinstance(error,BudgetExceeded):break
        finally:finalize_billing(task)
    return {'selected':len(pending),'completed':completed,'notes':notes}
