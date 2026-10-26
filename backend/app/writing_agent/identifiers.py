"""Short, scoped model-facing identifiers; persistent IDs and source text never change."""
from copy import deepcopy

FIELDS={'id','nodeId','node_id','node_ids','sentence_id','evidence_ids','reference_ids','issue_id','section_id','revision_id','source_id','source_ids','file_id','file_ids','material_evidence_ids','rule_ids','parent_id','dependencies','depends_on_sections','current_revision_id','target_node_ids','knowledge_unit_id','knowledge_unit_ids','knowledgeUnitIds','unit_id','unit_ids','reading_id','document_reading_ids','documentReadingIds','passage_id','passage_ids'}
STAGES={'judgment','judgment_check','draft','annotation','semantic','coherence','regression','paper_chunk','paper_cross_passages','paper_navigation'}

def encode(payload,manifest):
    if manifest.get('stage') not in STAGES:return payload,{}
    mapping={};counters={}
    def add(value,prefix):
        if isinstance(value,str) and value and value not in mapping:
            counters[prefix]=counters.get(prefix,0)+1;mapping[value]=prefix+str(counters[prefix])
    for identity in manifest.get('evidence_versions',{}):add(identity,'E')
    for source in manifest.get('source_evidence',{}).values():add(source.get('source_id'),'D')
    for identity in manifest.get('knowledge_unit_versions',{}):add(identity,'U')
    for identity in manifest.get('document_reading_versions',{}):add(identity,'Q')
    for identity in manifest.get('file_ids',[]):add(identity,'F')
    for rule in payload.get('rules',[]):
        if isinstance(rule,dict):add(rule.get('id'),'L')
    for row in payload.get('references',[]):add(row.get('id'),'B')
    for row in payload.get('sentences',[]):
        add(row.get('id'),'S');add(row.get('node_id'),'P')
    for row in payload.get('prior_issues',[]):add(row.get('issue_id'),'I')
    if payload.get('section'):add(payload['section'].get('id'),'C')
    for row in payload.get('outline',[]):
        add(row.get('id'),'C');add(row.get('current_revision_id'),'R')
    for row in [*payload.get('related_sections',[]),*payload.get('available_section_versions',[])]:
        add(row.get('section_id'),'C');add(row.get('revision_id'),'R')
    def collect(value):
        if isinstance(value,dict):
            if value.get('type') in ('paragraph','heading'):add(value.get('attrs',{}).get('nodeId'),'P')
            if 'node_id' in value:add(value.get('node_id'),'P')
            for item in value.values():collect(item)
        elif isinstance(value,list):
            for item in value:collect(item)
    collect(payload)
    return translate(payload,mapping),{alias:identity for identity,alias in mapping.items()}

def translate(value,mapping,key=None):
    if isinstance(value,dict):return {k:translate(v,mapping,k) for k,v in value.items()}
    if isinstance(value,list):return [translate(item,mapping,key) for item in value]
    if isinstance(value,str) and key in FIELDS:return mapping.get(value,value)
    return value

def decode(value,alias_to_id):return translate(value,alias_to_id)

def repair_identifier_keys(value,alias_to_id):
    result=deepcopy(value);repairs=[];known=set(alias_to_id)|set(alias_to_id.values())|{''}
    def visit(item):
        if isinstance(item,dict):
            for old,new in (('_node_id','node_id'),('_sentence_id','sentence_id')):
                if old in item and new not in item and isinstance(item[old],str) and item[old] in known:
                    item[new]=item.pop(old);repairs.append({'from':old,'to':new,'identifier_known':True,'prose_changed':False,'semantic_support':'not_granted'})
            for child in item.values():visit(child)
        elif isinstance(item,list):
            for child in item:visit(child)
    visit(result);return result,repairs
