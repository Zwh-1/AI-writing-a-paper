"""Read originals first, then create attributed, independently assessable claims."""
from fastapi import HTTPException
from . import db, knowledge_units as units, material_policy as policy
from . import knowledge_lineage as lineage, knowledge_graph as graph, knowledge_wiki as wiki

VERSION='knowledge-compiler.v2'
MODEL_SCHEMA='attributed-claim-roles-and-values.v3'
QUOTE_VALIDATION_VERSION='exact-original-whitespace-fullwidth-ascii.v2'


def locating_char(char):
    # Only the one-to-one ASCII width block. Deliberately not NFKC: ligatures,
    # superscripts, Roman numerals and mathematical symbols stay distinct.
    return chr(ord(char)-0xFEE0) if '\uff01'<=char<='\uff5e' else char


def align_quote(original,quote):
    """Locate a contiguous original range; never alter words or punctuation."""
    if not isinstance(quote,str) or not quote.strip():return None
    if quote in original:return quote
    positions=[i for i,char in enumerate(original) if not char.isspace()]
    compact=''.join(locating_char(original[i]) for i in positions)
    needle=''.join(locating_char(char) for char in quote if not char.isspace())
    start=compact.find(needle) if needle else -1
    return original[positions[start]:positions[start+len(needle)-1]+1] if start>=0 else None


def validate_candidates(observation,claims):
    if not isinstance(claims,list) or len(claims)>8:lineage.fail('KNOWLEDGE_OUTPUT_INVALID','抽取论断数组无效',422)
    accepted=[];rejected=[];aligned=[];width_aligned=[]
    for index,claim in enumerate(claims):
        if (not isinstance(claim,dict) or claim.get('observation_id')!=observation['id']
                or not isinstance(claim.get('statement'),str) or not claim['statement'].strip()
                or not isinstance(claim.get('conditions',[]),list)):
            rejected.append({'index':index,'reason':'invalid_claim_structure_or_source_identity'});continue
        quote=align_quote(observation['text'],claim.get('quote'))
        if quote is None:
            rejected.append({'index':index,'reason':'quote_not_a_contiguous_original_range'});continue
        candidate={**claim,'quote':quote}
        if quote!=claim['quote']:
            target=width_aligned if any(locating_char(char)!=char for char in quote+claim['quote']) else aligned
            target.append(index)
        if isinstance(claim.get('entities'),list):
            candidate['entities']=[{**entity,'quote':located} for entity in claim['entities']
                if isinstance(entity,dict) and isinstance(entity.get('name'),str) and entity['name'].strip()
                and (located:=align_quote(observation['text'],entity.get('quote'))) is not None]
        accepted.append(candidate)
    return accepted,{'version':QUOTE_VALIDATION_VERSION,'observation_id':observation['id'],'received':len(claims),
        'accepted':len(accepted),'whitespace_aligned_indices':aligned,'fullwidth_ascii_aligned_indices':width_aligned,'rejected':rejected,
        'source_words_or_punctuation_changed':False,'original_character_width_preserved':True,'human_approval_added':False}

def ranges(text,max_chars=6000):
    start=0
    while start<len(text):
        end=min(len(text),start+max_chars)
        if end<len(text):
            boundary=max(text.rfind(mark,start+max_chars//2,end) for mark in ('。','\n','；','. '))
            if boundary>start:end=boundary+1
        yield start,end
        if end==len(text):break
        start=max(start+1,end-400)

@db.atomic
def observe_unit(pid,uid,start=0,end=None,remote=False,intent=None):
    row=db.one('SELECT p.file_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?',(uid,pid))
    if not row:lineage.fail('SOURCE_MISSING','原文单元不存在',404)
    original,head,stamp=lineage.file_stamp(pid,row['file_id']);intent=intent or ('research' if policy.is_research(original) else 'guidance')
    if not policy.source_context(original,intent)['allowed_for_intent']:lineage.fail('SOURCE_PURPOSE_CHANGED','原件没有获准用于所选知识范围',403)
    item=units.get(pid,uid,remote=remote,intent=intent)
    if not item['current'] or item['review_state']=='excluded':lineage.fail('SOURCE_STALE','请先构建当前原文版本')
    end=len(item['text']) if end is None else end
    read=units.read(pid,uid,remote=remote,before=0,after=0,start=start,end=end,max_chars=16000,intent=intent)
    if read['status']!='read':lineage.fail('READ_SCOPE_INCOMPLETE','原文过长，请按明确范围分步读取')
    ns='literature' if intent=='research' else 'guidance';text=item['text'][start:end]
    meta={'unit_hash':item['content_hash'],'parse_id':item['parse_id'],'file_hash':original['sha256'],'char_start':start,'char_end':end,
          'issues':item['issues'],'content_roles':item['content_roles'],'material_use':item['material_use'],'observation_scope':units.observation_scope(item['locator']),'read_scope':'machine_observed_explicit_range',
          'human_reviewed':False,'semantic_support':'not_checked','open_disputes':[d for d in item['disputes'] if d['state']=='open']}
    extra_refs=[]
    if original['extension']=='.pptx' and item['origin']!='pptx_slide':
        anchor=db.one("SELECT id,text,content_hash FROM knowledge_units WHERE parse_id=? AND origin='pptx_slide' AND json_extract(locator,'$.page')=?",(item['parse_id'],item['locator'].get('page')))
        if anchor:
            if len(anchor['text'])<=16000:
                native=units.read(pid,anchor['id'],remote=remote,before=0,after=0,start=0,end=len(anchor['text']),max_chars=16000,intent=intent)
                if native['status']=='read':
                    meta['native_slide_context']={'unit_id':anchor['id'],'text':native['text'],'locator':native['locator'],'scope':'complete_requested_native_slide_not_semantic_proof'}
                    extra_refs.append(lineage.ref(pid,'unit',anchor['id'],remote))
                else:meta['requires_native_slide_reading']=True
            else:meta['requires_native_slide_reading']=True
    content=lineage.digest([stamp,uid,start,end,text,meta]);old=db.one('SELECT * FROM knowledge_observations WHERE project_id=? AND content_hash=?',(pid,content))
    if old:return old
    oid=db.uid();db.execute('INSERT INTO knowledge_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(oid,pid,original['id'],uid,None,ns,intent,stamp,text,db.dumps(item['locator']),db.dumps(meta),content,db.now()))
    lineage.add_dependencies(pid,'observation',oid,content,[{'type':'file','id':original['id'],'version':stamp},{'type':'unit','id':uid,'version':item['content_hash']},*extra_refs])
    for previous in db.rows("SELECT id FROM knowledge_observations WHERE project_id=? AND unit_id=? AND namespace=? AND id!=? AND json_extract(metadata_json,'$.char_start')=? AND json_extract(metadata_json,'$.char_end')=?",(pid,uid,ns,oid,start,end)):
        for old in db.rows("SELECT a.claim_version_id FROM kg_assertions a JOIN knowledge_dependencies d ON d.downstream_id=a.claim_version_id AND d.downstream_type='assertion' WHERE a.project_id=? AND d.upstream_type='observation' AND d.upstream_id=? AND a.method IN ('literal','llm') AND a.lifecycle NOT IN ('stale','superseded','excluded')",(pid,previous['id'])):
            db.execute("UPDATE kg_assertions SET lifecycle='superseded' WHERE claim_version_id=?",(old['claim_version_id'],))
        lineage.changed(pid,'observation',previous['id'],'同一原文范围的读取语境已更新，保留旧观察并复查派生内容')
    return lineage.owned(pid,'observation',oid)

@db.atomic
def observe_evidence(pid,eid):
    from .writing_agent.evidence import get
    item=get(pid,eid);version=lineage.version_of(pid,'evidence',eid);payload=item['payload'];text=payload.get('text') or db.dumps(payload)
    content=lineage.digest([eid,version,text]);old=db.one('SELECT * FROM knowledge_observations WHERE project_id=? AND content_hash=?',(pid,content))
    if old:return old
    oid=db.uid();meta={'evidence_kind':item['kind'],'origin_type':item['origin_type'],'conditions':item['conditions'],'semantic_support':'not_checked'}
    db.execute('INSERT INTO knowledge_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(oid,pid,None,None,eid,'project','project',version,text,db.dumps(payload.get('locator',{})),db.dumps(meta),content,db.now()))
    lineage.add_dependencies(pid,'observation',oid,content,[{'type':'evidence','id':eid,'version':version}]);return lineage.owned(pid,'observation',oid)

def evidence_for(pid,observation):
    from .writing_agent import evidence
    if observation['evidence_id']:return evidence.get(pid,observation['evidence_id'])
    meta=db.loads(observation['metadata_json'],{});locator={'before':0,'after':0,'start':meta['char_start'],'end':meta['char_end'],'intent':observation['intent'],'max_chars':16000}
    kind='literature' if observation['intent']=='research' else 'guidance_observation'
    old=db.one('SELECT id FROM evidence_items WHERE project_id=? AND origin_type=? AND origin_id=? AND locator_json=? AND validity=?',(pid,'knowledge_unit',observation['unit_id'],db.dumps(locator),'current'))
    if old:
        item=evidence.get(pid,old['id'])
        if item['version_current']:return item
    return evidence.create(pid,{'kind':kind,'origin_type':'knowledge_unit','origin_id':observation['unit_id'],'locator':locator,'conditions':['原文已按明确范围读取；仍需针对论断检查语义与适用条件']})

def literal_candidate(observation):
    text=observation['text']
    return {'statement':text,'quote':text,'observation_id':observation['id'],'predicate':'contains_excerpt','conditions':[],
            'modality':'verbatim_excerpt','polarity':'unknown','support':'not_checked','note':'确定性原文摘录，未进行模型综合和人工语义核对'}

def extracted(task,observation,mode):
    if mode=='literal':return [literal_candidate(observation)],[]
    if db.loads(observation['metadata_json'],{}).get('requires_native_slide_reading'):lineage.fail('GUIDANCE_CONTEXT_INCOMPLETE','该图像转录需要先分步读取完整原生幻灯片语境，未调用模型推断正反例',422)
    from .knowledge_model_bridge import json_call
    refs=[lineage.ref(task['project_id'],'observation',observation['id'],remote=True)]
    payload={'operation':'extract_attributed_claims','schema_version':MODEL_SCHEMA,'observation':{'id':observation['id'],'text':observation['text'],'namespace':observation['namespace'],'context':db.loads(observation['metadata_json'],{})},
             'output_schema':{'claims':[{'statement':'明确且保留归属的论断','quote':'逐字原文引句','observation_id':observation['id'],'predicate':'reports/uses/limits/compares/defines','conditions':['原文限定，未知留空'],'modality':'reported/possible/required/example','polarity':'positive/negative/unknown','entities':[{'name':'实际提及名称','kind':'concept/method/system/dataset/metric/environment','quote':'名称在原文中的逐字引句'}]}]},
             'requirements':['最多8个论断，不凭共现认定因果','保留数字单位、条件、否定与结论强度','指导资料中正反例及等级要求必须保留语境','模型判断不能填写人工确认']}
    payload['output_schema']['claims'][0].update(subject_entity_name='实际主语的原文实体名；未知留空',object_entity_name='实际宾语的原文实体名；未知留空')
    payload['output_schema']['claims'][0]['object_value']={'metric':'原文指标名或未知','value':'原文数值或未知','unit':'原文单位或未知','comparator':'原文比较对象或未知'}
    data,run=json_call(task,'knowledge_extract',payload,refs);claims=data.get('claims')
    accepted,validation=validate_candidates(observation,claims)
    validation['run_id']=run
    if not db.one("SELECT id FROM audit WHERE project_id=? AND action='knowledge_candidate_quote_validation' AND detail=?",(task['project_id'],db.dumps(validation))):
        db.audit(task['project_id'],'knowledge_candidate_quote_validation',validation)
    if claims and not accepted:
        lineage.fail('INVALID_QUOTE','本次模型候选均无法对应连续原文；结果已留存，未采用或自动重发',422)
    return accepted,[run]

@db.atomic
@lineage.validation_scope()
def adopt_candidates(pid,observation,candidates,document_entity,mode):
    sources=[lineage.ref(pid,'observation',observation['id'])];ev=evidence_for(pid,observation);sources.append(lineage.ref(pid,'evidence',ev['id']))
    ids=[];entities=[]
    for candidate in candidates:
        if not candidate.get('quote') or candidate['quote'] not in observation['text']:lineage.fail('INVALID_QUOTE','候选缺少实际原文引句',422)
        entity_ids=[];entity_names={};resolution=None
        for proposed in candidate.get('entities',[])[:12]:
            if not isinstance(proposed,dict) or not proposed.get('quote') or proposed['quote'] not in observation['text'] or align_quote(proposed['quote'],proposed.get('name')) is None:continue
            kind=proposed.get('kind','concept')
            if kind not in graph.KINDS:kind='concept'
            term=db.one('SELECT id FROM terms WHERE project_id=? AND (zh=? OR en=?)',(pid,proposed['name'],proposed['name']))
            entity_key=('term:'+term['id'] if term else 'source:'+document_entity['id']+':'+graph.normalized(proposed['name']))+':'+kind
            resolved=[]
            for alias in db.rows("SELECT * FROM kg_aliases WHERE project_id=? AND normalized=? AND state='human'",(pid,graph.normalized(proposed['name']))):
                decision=db.loads(alias['decision_json'],{})
                if observation['id'] in decision.get('observation_ids',[]) and candidate['statement'] in decision.get('statements',[]):resolved.append(alias['entity_id'])
            if len(set(resolved))>1:lineage.fail('ENTITY_RESOLUTION_CONFLICT','该原文论断存在相冲突的人工消歧记录，请先复查')
            existing=db.one('SELECT id,redirect_id FROM kg_entities WHERE project_id=? AND namespace=? AND entity_key=?',(pid,observation['namespace'],entity_key))
            current=graph.get_entity(pid,existing['redirect_id'] or existing['id']) if existing else None
            # A repeated model occurrence does not edit an established identity.
            # Case variants and the registered bilingual term key share its head;
            # explicit human description edits still create a new version.
            e=(graph.get_entity(pid,resolved[0]) if resolved else current if current and current['current'] else
               graph.entity(pid,{'namespace':observation['namespace'],'kind':kind,'key':entity_key,'name':proposed['name'],'sources':sources,'method':'model_candidate'}))
            if current and e['id']==current['id'] and proposed['name']!=e['name']:
                db.execute('INSERT OR IGNORE INTO kg_aliases VALUES(?,?,?,?,?,?,?,?)',(db.uid(),pid,e['id'],proposed['name'],graph.normalized(proposed['name']),'candidate',db.dumps({'observation_id':observation['id'],'quote':proposed['quote']}),db.now()))
            entity_ids.append(e['id']);entity_names[proposed['name']]=e['id'];entities.append(e['id'])
            if e['entity_key']!=entity_key and not resolved:
                original=db.one('SELECT id FROM kg_entities WHERE project_id=? AND namespace=? AND entity_key=?',(pid,observation['namespace'],entity_key))
                if original:resolution={'source_entity_id':original['id'],'target_entity_id':e['id']}
        subject=entity_names.get(candidate.get('subject_entity_name'),document_entity['id']);object_id=entity_names.get(candidate.get('object_entity_name'),entity_ids[0] if entity_ids else None)
        object_value=dict(candidate.get('object_value',{})) if isinstance(candidate.get('object_value',{}),dict) else {}
        if resolution:object_value['entity_resolution']=resolution
        claim=graph.assertion(pid,{'namespace':observation['namespace'],
            'subject_id':subject,'object_id':object_id,'object':object_value,
            'predicate':str(candidate.get('predicate','reports'))[:120],'statement':candidate['statement'],'conditions':candidate.get('conditions',[]),
            'attribution':document_entity['name'],'modality':candidate.get('modality','reported'),'polarity':candidate.get('polarity','unknown'),
            'method':mode,'sources':sources,'evidence':[{'evidence_id':ev['id'],'relation':'supports','scope':{'observation_id':observation['id'],'quote':candidate['quote']}}]})
        ids.append(claim['claim_version_id'])
    return {'assertion_ids':ids,'entity_ids':entities,'evidence_id':ev['id']}

@lineage.validation_scope()
def document_page(pid,original,entity,assertions,mode):
    ns=entity['namespace'];page=wiki.create_page(pid,{'namespace':ns,'kind':'document' if ns=='literature' else 'guidance','slug':'source-'+original['id'],'title':original['name']})
    blocks=[]
    for cid in assertions:
        claim=graph.get_assertion(pid,cid);text=claim['proposition']['statement'];conditions=claim['conditions']
        if claim['support_state'] in ('partial','contradicted','insufficient'):text='【支持性需注意：'+claim['support_state']+'】\n'+text
        if conditions:text+='\n\n适用条件：'+'；'.join(str(c) for c in conditions)
        blocks.append({'key':'claim-'+cid,'kind':'excerpt' if mode=='literal' else 'fact','text':text,'sources':[lineage.ref(pid,'assertion',cid)],'metadata':{'claim_id':cid,'mode':mode,'support_state':claim['support_state']}})
    # A page can hold many complete blocks; large sources are split into companion pages.
    pages=[]
    for offset in range(0,len(blocks),200):
        current=page if offset==0 else wiki.create_page(pid,{'namespace':ns,'kind':page['kind'],'slug':page['slug']+'-'+str(offset//200+1),'title':page['title']+'（续）'})
        version=wiki.revise(pid,current['id'],{'base_version_id':current['current_version_id'],'blocks':blocks[offset:offset+200]},author='literal' if mode=='literal' else 'model')
        published=wiki.publish(pid,current['id'],{'version_id':version['version']['id'],'base_version_id':current['current_version_id']});pages.append(published['id'])
    return pages

def compile_file(task,fid,mode='literal',progress=None,namespace=None):
    pid=task['project_id'];original,head,stamp=lineage.file_stamp(pid,fid)
    if not head or not units.parse_current(head,original):
        units.build(pid,fid,allow_material=True)
        original,head,stamp=lineage.file_stamp(pid,fid)
    rows=db.rows('SELECT id,text FROM knowledge_units WHERE parse_id=? AND review_state!=? ORDER BY ordinal',(head['id'],'excluded'))
    observations=[];assertions=[];runs=[];entities=[];doc=None
    for index,row in enumerate(rows):
        for start,end in ranges(row['text']):
            if not row['text'][start:end].strip():continue
            observed=observe_unit(pid,row['id'],start,end,remote=mode=='llm',intent='guidance' if namespace=='guidance' else 'research' if namespace=='literature' else None);observations.append(observed['id'])
            if doc is None:
                doc=graph.entity(pid,{'namespace':observed['namespace'],'kind':'document','key':'file:'+fid,'name':original['name'],'sources':[lineage.ref(pid,'observation',observed['id'])],'method':'source_identity'})
            from . import provider
            cfg=(__import__('app.model_routing',fromlist=['config']).config() if db.loads(task.get('payload'),{}).get('provider_profile')=='gpt' else provider.config()) if mode=='llm' else {}
            fingerprint=lineage.digest([VERSION,MODEL_SCHEMA if mode=='llm' else None,mode,{k:cfg.get(k) for k in ('base_url','model','protocol','context_limit','max_output_tokens','json_mode','thinking_enabled','reasoning_effort','temperature','top_p','seed')},db.rows('SELECT id,redirect_id,current_version_id FROM kg_entities WHERE project_id=? AND redirect_id IS NOT NULL',(pid,))]+([QUOTE_VALIDATION_VERSION] if mode=='llm' else []))
            key='observation:'+observed['id'];input_hash=lineage.digest([observed['content_hash'],fingerprint])
            saved=db.one('SELECT * FROM knowledge_build_items WHERE project_id=? AND item_key=? AND input_hash=?',(pid,key,input_hash));adopted=None;known_candidates=None
            if saved and saved['status']=='completed':
                previous=db.loads(saved['result_json'],{})
                try:lineage.validate_refs(pid,[lineage.ref(pid,'assertion',cid) for cid in previous['assertion_ids']]);adopted=previous
                except HTTPException:known_candidates=previous.get('candidates')
            if adopted is None:
                if saved and saved['status'] in ('outcome_unknown','completed_invalid') and not db.loads(task.get('payload'),{}).get('acknowledge_possible_duplicate_charge'):lineage.fail('MODEL_OUTCOME_UNRESOLVED','上次模型请求结果不明或输出无效，已保存运行；请先核对或明确重试策略')
                itemid=saved['id'] if saved else db.uid()
                db.execute('INSERT INTO knowledge_build_items VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,item_key,input_hash) DO UPDATE SET task_id=excluded.task_id,status=excluded.status,updated_at=excluded.updated_at',
                    (itemid,pid,task['id'],key,input_hash,'extract','running','{}',None,db.now()))
                try:
                    candidates,new_runs=(known_candidates,previous.get('run_ids',[])) if known_candidates is not None else extracted(task,observed,mode);runs.extend(new_runs)
                    adopted=adopt_candidates(pid,observed,candidates,doc,mode);adopted['run_ids']=new_runs;adopted['candidates']=candidates
                    db.execute("UPDATE knowledge_build_items SET status='completed',result_json=?,error=NULL,updated_at=? WHERE id=?",(db.dumps(adopted),db.now(),itemid))
                except Exception as error:
                    state='outcome_unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'completed_invalid' if isinstance(error,HTTPException) and isinstance(error.detail,dict) and error.detail.get('code') in ('KNOWLEDGE_OUTPUT_INVALID','INVALID_QUOTE') else 'failed'
                    db.execute('UPDATE knowledge_build_items SET status=?,error=?,updated_at=? WHERE id=?',(state,str(error)[:500],db.now(),itemid));raise
            assertions.extend(adopted['assertion_ids']);entities.extend(adopted['entity_ids'])
        if progress:progress(index+1,len(rows))
    pages=document_page(pid,original,doc,assertions,mode) if doc and assertions else []
    if doc:
        from .knowledge_metadata import enrich
        for book in db.rows("SELECT id FROM bibliography WHERE project_id=? AND file_id=? AND COALESCE(json_extract(metadata,'$._studio_deleted'),0)=0",(pid,fid)):enrich(pid,book['id'])
    return {'file_id':fid,'observation_ids':observations,'assertion_ids':assertions,'entity_ids':list(dict.fromkeys(entities+([doc['id']] if doc else []))),'page_ids':pages,'run_ids':runs,'mode':mode,'semantic_quality_verified':False}

@db.atomic
def backfill_claims(pid):
    imported=0;skipped=[]
    for claim in db.rows('SELECT c.* FROM claim_versions c LEFT JOIN kg_assertions a ON a.claim_version_id=c.id WHERE c.project_id=? AND a.claim_version_id IS NULL',(pid,)):
        refs=[]
        for link in db.rows('SELECT evidence_id FROM claim_evidence_links WHERE claim_version_id=?',(claim['id'],)):
            try:refs.append(lineage.ref(pid,'evidence',link['evidence_id']))
            except HTTPException:pass
        if not refs:skipped.append(claim['id']);continue
        content=lineage.digest([claim['id'],claim['proposition_json'],refs])
        db.execute('INSERT INTO kg_assertions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(claim['id'],pid,'project',None,'legacy_claim',None,'{}','已有候选论断','[]','unknown','candidate','legacy_import','candidate',content,db.now()))
        lineage.add_dependencies(pid,'assertion',claim['id'],content,refs);imported+=1
    return {'imported':imported,'skipped_without_current_evidence':skipped,'human_approval_added':False}

def update_indexes(pid):
    pages=[]
    for ns in ('literature','guidance','project'):
        entries=wiki.search(pid,ns=ns,limit=100)['items'];entries=[p for p in entries if p['kind'] in ('document','guidance','project')]
        if not entries:continue
        page=wiki.create_page(pid,{'namespace':ns,'kind':'topic','slug':'source-index','title':{'literature':'研究文献与问题索引','guidance':'写作指导与示例索引','project':'项目实现与证据索引'}[ns]})
        blocks=[{'key':p['id'],'kind':'note','text':p['title']+'：已提供可追溯原文摘录或候选整理；请按具体问题核对条件、结果与局限。','sources':[lineage.ref(pid,'wiki_version',p['version']['id'])]} for p in entries if p['id']!=page['id']]
        if blocks:
            version=wiki.revise(pid,page['id'],{'base_version_id':page['current_version_id'],'blocks':blocks},'literal');wiki.publish(pid,page['id'],{'version_id':version['version']['id'],'base_version_id':page['current_version_id']});pages.append(page['id'])
    return pages

def compile_project_evidence(task):
    pid=task['project_id'];ids=[];pages=[]
    for row in db.rows("SELECT id FROM evidence_items WHERE project_id=? AND validity='current' AND kind IN ('code_observation','test_definition','test_run','deployment','data_result','author_statement') ORDER BY created_at",(pid,)):
        try:obs=observe_evidence(pid,row['id'])
        except HTTPException:continue
        e=graph.entity(pid,{'namespace':'project','kind':'result','key':'evidence:'+row['id'],'name':'项目记录 '+row['id'][:10],'sources':[lineage.ref(pid,'observation',obs['id'])],'method':'record_identity'})
        result=adopt_candidates(pid,obs,[literal_candidate(obs)],e,'literal');ids.extend(result['assertion_ids'])
    if ids:
        page=wiki.create_page(pid,{'namespace':'project','kind':'project','slug':'project-evidence','title':'项目实现与验证记录'})
        blocks=[{'key':cid,'kind':'excerpt','text':graph.get_assertion(pid,cid)['proposition']['statement'],'sources':[lineage.ref(pid,'assertion',cid)]} for cid in ids[:200]]
        revision=wiki.revise(pid,page['id'],{'base_version_id':page['current_version_id'],'blocks':blocks},'literal')
        wiki.publish(pid,page['id'],{'version_id':revision['version']['id'],'base_version_id':page['current_version_id']});pages.append(page['id'])
    return {'assertion_ids':ids,'page_ids':pages}

@lineage.validation_scope()
def synthesis_inputs(pid,refs,compact=False):
    inputs=[]
    for index,r in enumerate(refs):
        if r['type']=='assertion':
            value=graph.get_assertion(pid,r['id'],True)
            if compact:
                quotes=[db.loads(e.get('scope_json'),{}).get('quote') for e in value.get('evidence',[])]
                value={'source_index':index,'statement':value['proposition']['statement'],'attribution':value['attribution'],
                    'conditions':value['conditions'],'polarity':value['polarity'],'modality':value['modality'],
                    'support_state':value['support_state'],'original_quotes':list(dict.fromkeys(q for q in quotes if isinstance(q,str) and q)),
                    'human_reviewed':value['human_reviewed']}
        elif r['type']=='observation':
            value=lineage.owned(pid,'observation',r['id'])
            if compact:value={'source_index':index,'text':value['text'],'namespace':value['namespace'],'human_reviewed':False}
        elif r['type']=='wiki_version':
            value=wiki.get_page(pid,lineage.owned(pid,'wiki_version',r['id'])['page_id'],r['id'],True)
            if compact:value={'source_index':index,'title':value['title'],'blocks':[{'kind':b['kind'],'text':b['text']} for b in value['blocks']],
                'scope':'model-derived knowledge page; trace its sources for original evidence'}
        else:lineage.fail('INVALID_SYNTHESIS_SOURCE','综合输入请选择论断、原文观察或Wiki版本',422)
        inputs.append(value)
    return inputs


def synthesis_payload(pid,body,refs):
    compact=body.get('compact_inputs') is True
    payload={'operation':'maintain_wiki','title':body['title'],'kind':body['kind'],'inputs':synthesis_inputs(pid,refs,compact),
        'allowed_sources':([{'index':i,'type':r['type'],'id':r['id']} for i,r in enumerate(refs)] if compact else refs),
        'output_schema':{'blocks':[{'key':'稳定块标识','kind':'synthesis/comparison/limitation/inference/question','text':'综合文字',
            **({'source_indices':[0]} if compact else {'sources':refs})}]},
        'requirements':['逐块使用所提供的真实来源，不扩大条件或结论强度','比较时保留指标、单位和环境差异，不可比要明确',
            '未覆盖不等于领域空白；矛盾双方都保留','人工备注由程序保留','模型来源尚非人工金标准，不将文献结论当成本项目实测']}
    if compact:payload['requirements']+=['每块source_indices选择实际支持该块的输入序号，程序回填真实版本引用；不得输出其他来源',
        '生成3至6个完整内容块，每块尽量在260个汉字内，保留必要条件与限制；不编造不存在的分歧']
    return payload


def synthesize(task,body):
    from .knowledge_model_bridge import json_call
    pid=task['project_id'];page=wiki.create_page(pid,body);base=page['current_version_id'];refs=body.get('sources',[])
    if base:
        if not lineage.allow_remote_history(pid,lineage.references(pid,'wiki_version',base)):lineage.fail('SOURCE_ACCESS_REVOKED','当前页面的来源范围禁止外发，未发送页面标题或派生内容',403)
    if not refs:lineage.fail('PROVENANCE_MISSING','综合需要选择图谱论断或原文来源',422)
    lineage.validate_refs(pid,refs,remote=True)
    payload=synthesis_payload(pid,{**body,'title':page['title'],'kind':page['kind']},refs)
    data,run=json_call(task,'wiki_synthesis',payload,refs);blocks=data.get('blocks')
    if not isinstance(blocks,list):lineage.fail('KNOWLEDGE_OUTPUT_INVALID','综合结果缺少内容块',422)
    allowed={(r['type'],r['id'],r['version']) for r in refs}
    for b in blocks:
        if body.get('compact_inputs'):
            indices=b.get('source_indices') if isinstance(b,dict) else None
            if not isinstance(indices,list) or not indices or any(type(i) is not int or not 0<=i<len(refs) for i in indices):
                lineage.fail('INVALID_PROVENANCE','综合来源序号无效，未补造或猜测引用',422)
            b['sources']=[refs[i] for i in dict.fromkeys(indices)]
            b['metadata']={**b.get('metadata',{}),'citation_binding':'provided_source_indices','human_reviewed':False}
        if any((r.get('type'),r.get('id'),r.get('version')) not in allowed for r in b.get('sources',[])):lineage.fail('INVALID_PROVENANCE','综合引用了未提供的来源',422)
    from .knowledge_wiki_merge import submit
    revision=submit(pid,page['id'],{'base_version_id':base,'blocks':blocks},'model')
    if revision.get('conflict_id'):return {**revision,'run_id':run,'published':False,'action':'候选及并发编辑已保存，请合并后发布'}
    return {'page_id':page['id'],'version_id':revision['version']['id'],'run_id':run,'published':False,'action':'审阅后发布'}
