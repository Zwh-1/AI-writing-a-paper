import re
from fastapi import HTTPException
from . import db
NUM={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'二十':20,'三十':30}
def extract(pid):
    added=0
    for p in db.rows("SELECT p.* FROM pages p JOIN files f ON f.id=p.file_id WHERE f.project_id=? AND f.role='rules' AND f.active=1 AND p.review_state!='not_applicable'",(pid,)):
        src=db.one('SELECT id FROM sources WHERE page_id=? LIMIT 1',(p['id'],))
        if not src: continue
        from .rule_text_integrity import units
        flowing=any(x['method']=='windows_ocr' for x in db.rows('SELECT method FROM sources WHERE page_id=?',(p['id'],))) or db.one('SELECT extension FROM files WHERE id=?',(p['file_id'],))['extension']=='.pdf'
        lines=[text for _,_,text in units(p['text'],flowing)]
        for i,line in enumerate(lines):
            if not 8<=len(line)<=12000 or not re.search(r'必须|不得|不少于|不超过|要求|应当|禁止|需要|字号|行距|页边距|关键词|参考文献|右上标|首行缩进|字以内',line): continue
            matching=next((x for x in db.rows('SELECT * FROM sources WHERE page_id=?',(p['id'],)) if line in x['text']),None)
            if matching: src=matching
            else:
                source_id=db.uid();position=p['text'].find(line);prefix=p['text'][:position];ocr_at=prefix.rfind('[本地OCR候选');embedded_at=prefix.rfind('[嵌入DOCX');native_at=prefix.rfind('[补充原生内容]');method='embedded_native' if embedded_at>max(ocr_at,native_at) else 'windows_ocr' if ocr_at>max(embedded_at,native_at) else 'native';locator=db.dumps({'kind':'rule_excerpt','page':p['page_number'],'char_start':position,'source_method':method,'parent_source_ids':[s['id'] for s in db.rows('SELECT id FROM sources WHERE page_id=? AND method=?',(p['id'],method))]})
                db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(source_id,p['file_id'],p['id'],locator,line,line,'rule_candidate',method,int(p['review_state']=='reviewed')))
                from .parsers import tokens
                db.execute('INSERT INTO source_fts VALUES(?,?)',(source_id,tokens(line)))
                src={'id':source_id}
            from .rule_duplicates import source_duplicate
            if source_duplicate(pid,src['id'],line):continue
            context=' '.join(lines[max(0,i-1):i+2]);mod='recommended' if '建议' in context else ('counterexample' if re.search(r'错误示例|不合格|误区',context) else 'pending')
            scope='abstract' if '摘要' in p['title'] else 'global';cat='reference' if '参考文献' in line else ('format' if re.search(r'字号|字体|行距|页边距|缩进|右上标',line) else 'content')
            checker='manual';key='';value=None;m=re.search(r'不少于\s*([\d一二三四五六七八九十]+)\s*篇',line)
            if '参考文献' in line and m:
                raw=m.group(1);value=int(raw) if raw.isdigit() else NUM.get(raw)
                if value: key='reference_min';checker=key
            from .rule_engine import infer
            inferred=infer(line,scope);checker=inferred['checker'];value=inferred['value'];key=inferred['rule_key'];scope=inferred['scope']
            if checker=='format_value':cat='format'
            db.execute('INSERT INTO rules(id,project_id,source_id,category,scope,statement,modality,checker,rule_key,value,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(db.uid(),pid,src['id'],cat,scope,line,mod,checker,key,db.dumps(value),db.now()));added+=1
    return {'added':added}
def conflicts(pid,mandatory_only=False):
    groups={}
    where=" AND state IN ('confirmed','machine_verified') AND modality='mandatory'" if mandatory_only else " AND state NOT IN ('rejected','reference_only') AND modality NOT IN ('example','counterexample')"
    for r in db.rows("SELECT r.*,rc.expression AS condition_expression FROM rules r LEFT JOIN rule_conditions rc ON rc.rule_id=r.id WHERE r.project_id=? AND r.rule_key!=''"+where,(pid,)):
        r['conditions']=db.loads(r.pop('condition_expression'),{})
        groups.setdefault((r['rule_key'],r['scope']),[]).append(r)
    result=[]
    from .rule_duplicates import collapse
    for (key,scope),items in groups.items():
        items=collapse(items)
        involved={}
        for i,a in enumerate(items):
            for b in items[i+1:]:
                if db.loads(a['value'])==db.loads(b['value']):continue
                if not set(db.loads(a['operations'],[])) & set(db.loads(b['operations'],[])):continue
                # Clearly disjoint equality conditions apply to different contexts.
                ca,cb=a['conditions'],b['conditions']
                if ca.get('op')==cb.get('op')=='eq' and ca.get('field')==cb.get('field') and ca.get('value')!=cb.get('value'):continue
                if a['checker'] in ('word_min','word_max','word_range') and b['checker'] in ('word_min','word_max','word_range'):
                    def interval(r):
                        v=db.loads(r['value']);return (v['min'],v['max']) if r['checker']=='word_range' else (v,float('inf')) if r['checker']=='word_min' else (0,v)
                    lo,hi=interval(a);other_lo,other_hi=interval(b)
                    if max(lo,other_lo)<=min(hi,other_hi):continue
                involved[a['id']]=a;involved[b['id']]=b
        if involved:result.append({'key':key,'scope':scope,'rules':list(involved.values())})
    return result
@db.atomic
def publish(pid):
    from .rule_workspace import diagnostics
    status=diagnostics(pid)
    if status['blockers']:raise HTTPException(422,status['blockers'][0])
    rr=db.rows("SELECT * FROM rules WHERE project_id=? AND state IN ('confirmed','machine_verified') AND modality IN ('mandatory','recommended')",(pid,))
    version=db.one('SELECT COALESCE(MAX(version),0)+1 AS n FROM rule_packages WHERE project_id=?',(pid,))['n'];rid=db.uid()
    for row in rr:
        condition=db.one('SELECT expression FROM rule_conditions WHERE rule_id=?',(row['id'],));row['conditions']=db.loads(condition['expression'],{}) if condition else {}
    manifest=db.rows("SELECT id,sha256,egress FROM files WHERE project_id=? AND role='rules' AND active=1",(pid,))
    with db.transaction() as c:
        c.execute('INSERT INTO rule_packages VALUES(?,?,?,?,?,?)',(rid,pid,version,db.dumps(rr),db.dumps(manifest),db.now()))
        c.execute('UPDATE projects SET rule_package_id=?,updated_at=? WHERE id=?',(rid,db.now(),pid))
        c.execute("UPDATE sections SET stale=1,stale_reason='规则已更新' WHERE project_id=? AND current_revision_id IS NOT NULL",(pid,))
    from .writing_agent.impact import changed
    changed(pid,'rule_package',rid,'论文规则包已更新')
    db.audit(pid,'publish_rules',{'id':rid,'version':version});return {'id':rid,'version':version,'rule_count':len(rr)}
def applicable(package,section,operation):
    from .rule_engine import condition_matches
    project=db.one('SELECT * FROM projects WHERE id=?',(package['project_id'],))
    context={'paper_type':project['paper_type'],'section_kind':section['kind'],'operation':operation,'language':'en' if section['kind']=='abstract_en' else 'zh'}
    result=[r for r in db.loads(package['rules'],[]) if r['scope'] in ('global',section['kind'],section['id']) and operation in db.loads(r['operations'],[]) and r['modality'] in ('mandatory','recommended') and condition_matches(r.get('conditions',{}),context)]
    from .rule_text_repair import affected_snapshot
    affected=affected_snapshot(package['project_id'],result)
    if affected:raise HTTPException(422,{'code':'RULE_TEXT_INCOMPLETE','message':'当前规则包含已撤回的残缺条款，请先更新规则包，再继续写作','rule_ids':affected})
    return result
