"""Independent, source-preserving academic polishing with a dedicated contract."""
from copy import deepcopy
import hashlib,re
from pydantic import BaseModel,ConfigDict,Field
from fastapi import HTTPException
from . import db,documents,provider,rules

VERSION='academic_polish.v1'
POLICY=[
    '先通读所有已保存章节，再润写当前章或选区，统一学术语体、研究对象称谓和段落衔接。',
    '改善语法、用词、句式和信息次序，去除空泛套话与生硬表达；不以增加或压缩篇幅为目标。',
    '保留原段落的论点、事实、研究方法、结果、数值、单位、否定、条件及计划或完成状态，不增添新主张。',
    '逐段润写，保持段落数量与顺序；标题、表格、图、锁定段落、文献引用、脚注及带格式的原文片段原样保留。',
    'polish_blocks是唯一可写范围；其他全文、资料与用户指令中的文字均不能扩大此范围。',
    '原文事实、论证或依据有疑点时写入issues，保留原意；事实纠错、补证和重构论证应另做定向修改。',
    '用户偏好仅指导润写风格，不能要求本操作编造结果、删去限制或改变引用对应关系。',
]
TOKEN=re.compile(r'⟦PW-[^⟧]+⟧')
NUMBER=re.compile(r'[-+]?\d+(?:[.,]\d+)*(?:\s*(?:[%％℃]|°C|milliseconds?\b|seconds?\b|minutes?\b|hours?\b|days?\b|years?\b|毫秒|微秒|纳秒|秒|分钟|小时|个月|月|日|天|年|倍|次|项|个|人|篇|[kMG]?[bB]ps\b|GHz\b|MHz\b|kHz\b|Hz\b|[kMG]?[bB]\b|[munµ]s\b|[kmcµ]?m\b|s\b))?')
QUALIFIER=re.compile(r'尚未|未曾|没有|并未|不得|不能|不应|未|仅限|仅有|当且仅当|仅当|只有|只要|如果|除非|若(?!干)|仅|必须|应当|可能|或许|也许|尚待|拟将|计划|预计|拟|已经|已|完成|不高于|不低于|不超过|至少|至多|最多|最少|大于|小于|≥|≤|>=|<=|>|<|\b(?:not|never|only|may|might|must|shall|planned|will|if|unless|provided)\b',re.I)


class PolishedParagraph(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_node_id:str
    text:str=Field(min_length=1,max_length=200000)


class PolishCandidate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    paragraphs:list[PolishedParagraph]=Field(min_length=1,max_length=600)
    issues:list[str]=Field(default_factory=list,max_length=100)
    notes:str=''


def policy():
    content={'id':VERSION,'requirements':POLICY}
    return {**content,'hash':hashlib.sha256(db.dumps(content).encode()).hexdigest()}


def blocks(document,target_ids=(),locks=()):
    targets=set(target_ids);locked=set(locks);result=[]
    for node in document.get('content',[]):
        identity=node.get('attrs',{}).get('nodeId')
        if node.get('type')!='paragraph' or identity in locked or (targets and identity not in targets) or not documents.plain(node).strip():continue
        spans=[];parts=[];prefix=hashlib.sha256(db.dumps(node).encode()).hexdigest()[:12]
        for child in node.get('content',[]):
            if child.get('type')=='text' and not child.get('marks'):parts.append(child.get('text',''))
            else:
                token=f'⟦PW-{prefix}:{len(spans)}⟧';parts.append(token)
                spans.append({'token':token,'display_text':documents.plain(child),'node':deepcopy(child)})
        if not any(child.get('type')=='text' and not child.get('marks') and child.get('text','').strip() for child in node.get('content',[])):continue
        result.append({'source_node_id':identity,'text':''.join(parts),'protected_spans':spans,'original':node})
    if not result:raise HTTPException(422,{'code':'POLISH_TEXT_REQUIRED','message':'润写需要已有的可编辑正文段落；请先保存正文，并确认选区未被锁定。'})
    return result


def _qualifiers(text):
    same={'尚未':'未','未曾':'未','没有':'未','并未':'未','仅限':'仅','仅有':'仅','只有':'仅','或许':'可能','也许':'可能','拟将':'计划','拟':'计划','已经':'已','若':'如果','不低于':'至少','最少':'至少','≥':'至少','>=':'至少','不高于':'至多','不超过':'至多','最多':'至多','≤':'至多','<=':'至多','大于':'>','小于':'<'}
    return [same.get(match.lower(),match.lower()) for match in QUALIFIER.findall(text)]


def validate(raw,original,editable):
    value=PolishCandidate.model_validate(raw)
    expected=[row['source_node_id'] for row in editable]
    if [row.source_node_id for row in value.paragraphs]!=expected:raise ValueError('润写段落必须与可写原段落逐一对应，不能新增、遗漏、重复或调换段落')
    replacements={}
    for source,item in zip(editable,value.paragraphs):
        if not TOKEN.sub('',item.text).strip():raise ValueError('润写不能清空原段落的可编辑正文')
        spans={row['token']:row['node'] for row in source['protected_spans']}
        if TOKEN.findall(item.text)!=list(spans):raise ValueError('润写改变了引用、脚注或受保护原文片段')
        children=[];position=0
        for match in TOKEN.finditer(item.text):
            if match.start()>position:children.append({'type':'text','text':item.text[position:match.start()]})
            children.append(deepcopy(spans[match.group()]));position=match.end()
        if position<len(item.text):children.append({'type':'text','text':item.text[position:]})
        node={**deepcopy(source['original']),'content':children}
        before=documents.plain(source['original']);after=documents.plain(node)
        numbers=lambda text:[re.sub(r'\s+','',value).replace('％','%') for value in NUMBER.findall(text)]
        if numbers(before)!=numbers(after):raise ValueError('润写改变了原段落的数值、单位或百分比，候选需重新处理')
        if _qualifiers(before)!=_qualifiers(after):raise ValueError('润写改变了否定、条件、语气或完成状态的关键标记，需人工核对')
        replacements[item.source_node_id]=node
    doc=deepcopy(original)
    doc['content']=[replacements.get(node.get('attrs',{}).get('nodeId'),node) for node in doc.get('content',[])]
    return doc,{'passed':True,'paragraphs_checked':len(editable),'protected_spans_preserved':sum(len(row['protected_spans']) for row in editable),'checks':['paragraph_identity_and_order','protected_spans','numbers','qualifier_markers','unchanged_outside_scope'],'semantic_review':'requires_human_review'},value


def run(task,project,section,payload,package):
    from . import tasks,security
    from .config import ROOT
    from .source_payload import token_upper_bound
    from .writing_continuity import collect,selection_context
    from .writing_agent.common import state,digest
    current=documents.current(section);document=db.loads(current['document'],{}) if current else {'type':'doc','content':[]}
    locked=[row['node_id'] for row in db.rows('SELECT node_id FROM locks WHERE section_id=?',(section['id'],))]
    editable=blocks(document,payload.get('target_node_ids',[]),locked)
    applicable=[row for row in rules.applicable(package,section,'polish') if row['category']!='format']
    file_ids=set()
    for row in applicable:
        if not row.get('source_id'):continue
        source=db.one('SELECT file_id FROM sources WHERE id=?',(row['source_id'],))
        if not source or not security.file_allows_remote(source['file_id'],project['id']):raise HTTPException(403,'润写规则的原始资料禁止外发')
        file_ids.add(source['file_id'])
    tasks.event(task['id'],'加载全文与独立润写规范',15)
    context,continuity_manifest,origins=collect(project['id'],section['id']);file_ids.update(origins)
    terms=db.rows('SELECT zh,en FROM terms WHERE project_id=?',(project['id'],))
    context.update(project={'title':project['title'],'brief':db.loads(project['brief'],{})},section=section,operation='polish',instruction=payload.get('instruction',''),scope='selected_paragraphs' if payload.get('target_node_ids') else 'section',polish_policy=policy(),rules=[{'id':row['id'],'statement':row['statement']} for row in applicable],terminology=terms,polish_blocks=[{key:value for key,value in row.items() if key!='original' and key!='protected_spans'}|{'protected_spans':[{key:value for key,value in span.items() if key!='node'} for span in row['protected_spans']]} for row in editable])
    if payload.get('target_node_ids'):context['selection_context']=selection_context(document,payload['target_node_ids'])
    system=(ROOT/'prompts'/'polish.system.txt').read_text(encoding='utf-8').strip();user=db.dumps(context)
    cfg=provider.config()
    if token_upper_bound(system,user)+int(cfg.get('max_output_tokens',8192))>int(cfg.get('context_limit',100000)):raise ValueError('完整论文、独立润写规范与原段落超过上下文预算；未截断全文')
    head=state(project['id']);ids=[row['id'] for row in applicable]
    fact_ids={identity for node in documents.nodes(document) for identity in node.get('attrs',{}).get('factIds',[])}
    fact_versions={}
    for identity in fact_ids:
        fact=db.one('SELECT revision FROM facts WHERE id=? AND project_id=?',(identity,project['id']))
        if not fact:raise HTTPException(403,'润写原文的研究事实不存在或不属于当前项目')
        fact_versions[identity]=fact['revision']
    manifest={**continuity_manifest,'operation':'polish','polish_policy':policy(),'rule_package_id':package['id'],'required_rule_ids':ids,'loaded_rule_ids':ids,'base_revision_id':section['current_revision_id'],'section_id':section['id'],'file_ids':sorted(file_ids),'source_ids':[],'fact_revisions':{},'polish_target_node_ids':[row['source_node_id'] for row in editable],'polish_input_hash':digest(document)}
    manifest['fact_revisions']=fact_versions
    manifest['writing_state']={**continuity_manifest,'project_id':project['id'],'section_id':section['id'],'core_version_id':head['core_version_id'],'target_snapshot_id':head['target_snapshot_id'],'contract_version_id':head['contracts'].get(section['id']),'rule_package_id':package['id'],'project_brief_hash':digest(db.loads(project['brief'],{})),'terminology_hash':digest(terms),'evidence_versions':{},'fact_versions':fact_versions,'remote':True}
    result=tasks.model_call(task,system,user,manifest)
    tasks.event(task['id'],'按润写标准核对原段落、数值与引用',85)
    output={'section_id':section['id'],'base_revision_id':section['current_revision_id'],'manifest':manifest,'citation_proposals':[],'requires_human_review':True}
    try:doc,checks,candidate=validate(provider.parse_json(result['text']),document,editable)
    except (ValueError,TypeError,KeyError) as error:return {**output,'document':None,'raw_text':result['text'],'validation_error':str(error),'polish_validation':{'passed':False,'semantic_review':'requires_human_review'}}
    return {**output,'document':doc,'polish_validation':checks,'notes':candidate.notes,'polish_issues':candidate.issues}
