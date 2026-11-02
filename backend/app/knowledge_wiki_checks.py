"""Deterministic maintenance findings and explicitly provisional semantic checks."""
from fastapi import HTTPException
import re
from . import db, security, knowledge_lineage as lineage, knowledge_wiki as wiki, knowledge_graph as graph

def check(pid):
    security.require_project(pid);findings=[];pages=db.rows('SELECT * FROM wiki_pages WHERE project_id=?',(pid,))
    with lineage.validation_scope():
        for page in pages:
            if not page['current_version_id']:
                findings.append({'kind':'unpublished','page_id':page['id'],'message':'页面尚无发布版本'});continue
            p=wiki.get_page(pid,page['id'])
            if not p['current']:findings.append({'kind':'stale','page_id':p['id'],'message':'来源或页面版本需复查','issues':p.get('issues',[])})
            for b in p['blocks']:
                if b['kind'] in ('fact','comparison','synthesis','excerpt','limitation') and not b['sources']:findings.append({'kind':'missing_source','page_id':p['id'],'block_key':b['block_key'],'message':'事实或综合块缺少出处'})
            incoming=db.one("SELECT id FROM wiki_links WHERE project_id=? AND target_type IN ('wiki_page','wiki_version') AND target_id IN (?,?) LIMIT 1",(pid,p['id'],p['current_version_id']))
            if not incoming and p['slug']!='source-index':findings.append({'kind':'orphan','page_id':p['id'],'message':'页面尚未被其他页面链接，可加入主题索引'})
        groups={}
        for a in db.rows("SELECT * FROM kg_assertions WHERE project_id=? AND lifecycle NOT IN ('stale','excluded','superseded')",(pid,)):
            key=(a['object_id'] or a['subject_id'],a['predicate'],a['conditions_json'])
            groups.setdefault(key,[]).append(a)
        for key,items in groups.items():
            positive=[a['claim_version_id'] for a in items if a['polarity']=='positive'];negative=[a['claim_version_id'] for a in items if a['polarity']=='negative']
            if positive and negative:findings.append({'kind':'possible_disagreement','claim_ids':positive+negative,'message':'相似对象存在正负结论，需核对条件与比较对象；未自动认定矛盾'})
    return {'findings':findings,'count':len(findings),'method':'deterministic_checks_and_candidate_signals','semantic_quality_verified':False}

def assess_with_model(task,body):
    from .knowledge_model_bridge import json_call
    pid=task['project_id'];results=[]
    for cid in body.get('claim_ids',[])[:20]:
        claim=graph.get_assertion(pid,cid,True);observations=[lineage.owned(pid,'observation',r['id']) for r in claim['sources'] if r['type']=='observation']
        if not observations:continue
        refs=[lineage.ref(pid,'assertion',cid,True)]
        for obs in observations:
            proposition=dict(claim['proposition'])
            if claim['method']=='literal' and proposition.get('statement')==obs['text'].strip():
                proposition.pop('statement')
                proposition['statement_reference']={'field':'original.text','relation':'strip_outer_whitespace','kind':'verbatim_excerpt_not_research_conclusion'}
            data,run=json_call(task,'knowledge_support_check',{'claim':proposition,'original':{'id':obs['id'],'text':obs['text'],'metadata':db.loads(obs['metadata_json'],{})},
                'output_schema':{'support':'supported/partial/contradicted/insufficient/not_checked','quote':'逐字引句','note':'明确理由','conditions':['适用条件']},
                'requirements':['statement_reference存在时，待核对的论断全文就是original.text去除首尾空白，完整原文只发送一份，不省略内容',
                    '逐字摘录的对应性不能替代研究结论的语义审查或人工判断；在理由中保留这一限制']},refs)
            results.append(adopt_model_assessment(pid,cid,obs,data,run))
    return {'assessments':results,'human_approval_added':False}

def adopt_model_assessment(pid,cid,observation,data,run):
    marker='[模型运行 '+run+'] '
    previous=db.one("SELECT * FROM kg_support_assessments WHERE project_id=? AND claim_version_id=? AND observation_id=? AND method='model' AND instr(note,?)=1",(pid,cid,observation['id'],marker))
    if previous:return {**previous,'run_id':run,'cache_hit':True,'human_approval_added':False}
    quote=data.get('quote','');quote=quote if isinstance(quote,str) else '';original=observation['text'];aligned=False;unlocated=False
    if quote and quote not in original:
        # Only whitespace alignment is allowed. Never repair words or numbers.
        chars=[];positions=[]
        for index,char in enumerate(original):
            if not char.isspace():chars.append(char);positions.append(index)
        needle=''.join(c for c in quote if not c.isspace());start=''.join(chars).find(needle) if needle else -1
        if start>=0:quote=original[positions[start]:positions[start+len(needle)-1]+1];aligned=True
        else:unlocated=True
    if not quote and data.get('support') in ('supported','partial','contradicted'):unlocated=True
    candidate={**data,'observation_id':observation['id'],'reviewer':'模型辅助核对','quote':quote,'note':str(data.get('note') or '模型未提供完整判断理由')}
    if unlocated:candidate.update(support='not_checked',quote='',note='模型引句无法逐字定位，未采用其支持性结论。'+candidate['note'])
    if candidate.get('support') not in ('supported','partial','contradicted','insufficient','not_checked'):candidate.update(support='not_checked',note='模型返回的支持性状态无效，保留待判断。'+candidate['note'])
    candidate['note']=marker+candidate['note'];result=graph.assess(pid,cid,candidate,method='model')
    return {**result,'run_id':run,'whitespace_aligned':aligned,'unlocated_quote':unlocated,'human_approval_added':False}

def semantic_check(task,body):
    from .knowledge_model_bridge import json_call,SYSTEM
    from .source_payload import token_upper_bound
    from .provider import config
    from .config import DATA
    pid=task['project_id'];limit=body.get('limit',8)
    if type(limit) is not int or not 2<=limit<=20:lineage.fail('INVALID_CHECK_LIMIT','语义检查范围须为2至20条完整论断',422)
    candidates=graph.search(pid,body.get('query',''),body.get('namespace','literature'),1,60,limit,True,file_ids=body.get('file_ids'))['assertions']
    selected=[];refs=[];omitted=[];cfg=config()
    for a in candidates:
        item={'id':a['claim_version_id'],'statement':a['proposition']['statement'],'conditions':a['conditions'],'attribution':a['attribution'],'namespace':a['namespace']}
        if token_upper_bound(SYSTEM,db.dumps(selected+[item]))+int(cfg.get('max_output_tokens',8192))+3000>int(cfg.get('context_limit',32000)):omitted.append(a['claim_version_id']);continue
        selected.append(item);refs.append(lineage.ref(pid,'assertion',a['claim_version_id'],True))
    if len(selected)<2:lineage.fail('CHECK_SCOPE_TOO_SMALL','预算内不足两条可比较论断，请调整问题或范围',422)
    data,run=json_call(task,'knowledge_semantic_maintenance',{'claims':selected,'output_schema':{'findings':[{'kind':'possible_contradiction/missing_condition/incomparable/duplicate_concept','claim_ids':['仅使用所给ID'],'note':'待核对原因与应回读范围'}]},'requirements':['只看到候选论断，不得宣称已经证明原文矛盾或材料错误','限定条件不同不能直接判为冲突','不得代填人工判断，输出仅作回读线索']},refs)
    allowed={a['id'] for a in selected};findings=data.get('findings',[])
    if not isinstance(findings,list) or any(not isinstance(f,dict) or f.get('kind') not in ('possible_contradiction','missing_condition','incomparable','duplicate_concept') or not isinstance(f.get('note'),str) or not f['note'].strip() or not isinstance(f.get('claim_ids'),list) or not f['claim_ids'] or not set(f['claim_ids'])<=allowed for f in findings):lineage.fail('KNOWLEDGE_OUTPUT_INVALID','语义检查的类型、理由或论断定位无效',422)
    identity=db.uid();folder=DATA/'knowledge'/'maintenance';folder.mkdir(parents=True,exist_ok=True);path=folder/(identity+'.json');path.write_text(db.dumps(data),encoding='utf-8');content=lineage.digest(data)
    manifest={'refs':refs,'run_id':run,'scope':'candidate_claim_comparison_not_original_verification','omitted_claim_ids':omitted,'human_approval_added':False}
    db.execute('INSERT INTO knowledge_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(identity,pid,task['id'],'semantic_maintenance',content,str(path),db.dumps(manifest),'ready',db.now()));lineage.add_dependencies(pid,'artifact',identity,content,refs)
    return {'id':identity,'findings':findings,'manifest':manifest,'semantic_quality_verified':False}
