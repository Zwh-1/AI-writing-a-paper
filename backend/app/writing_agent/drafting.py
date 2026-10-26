from copy import deepcopy
from .. import documents,db
from .models import Draft
from .common import fail

def document(data,current=None,targets=None,locks=None,references=None,evidence_ids=None):
    # A known source ID can be classified without altering one character of
    # prose. Unknown IDs still fail, and independent annotation must verify it.
    normalized=Draft.model_validate(data).model_dump();permitted_evidence=set(evidence_ids or []);books={r['id'] for r in references or []};corrections=[]
    for paragraph in normalized.get('paragraphs',[]):
        refs=paragraph.get('reference_ids',[])
        if not isinstance(refs,list):continue
        misplaced=[r for r in refs if r in permitted_evidence and r not in books]
        if misplaced:
            paragraph['reference_ids']=[r for r in refs if r not in misplaced];paragraph['evidence_ids']=list(dict.fromkeys([*paragraph.get('evidence_ids',[]),*misplaced]))
            corrections.append({'local_id':paragraph.get('local_id'),'kind':'known_evidence_ids_classified_separately_from_bibliography','evidence_ids':misplaced,'prose_changed':False,'semantic_support':'not_granted'})
    draft=Draft.model_validate(normalized).model_dump();draft['field_corrections']=corrections;ids=[p['local_id'] for p in draft['paragraphs']]
    if len(set(ids))!=len(ids):fail('INVALID_DRAFT','候选段落标识重复')
    permitted={r['id']:r for r in references or []};content=[]
    for p in draft['paragraphs']:
        if set(p['evidence_ids'])-permitted_evidence:fail('INVALID_EVIDENCE','候选关联了未提供的来源编号')
        if set(p['reference_ids'])-set(permitted):fail('INVALID_REFERENCE','候选引用了未提供原文依据的书目')
        children=[{'type':'text','text':p['text']}];source_ids=[];unit_ids=[];reading_ids=[]
        for ref in dict.fromkeys(p['reference_ids']):
            children.append({'type':'citation','attrs':{'nodeId':db.uid(),'citationId':ref}});source_ids.extend(permitted[ref]['source_ids'])
            unit_ids.extend(permitted[ref].get('knowledge_unit_ids',[]))
            reading_ids.extend(permitted[ref].get('document_reading_ids',[]))
        attrs={'nodeId':db.uid()}
        if source_ids:attrs['sourceIds']=list(dict.fromkeys(source_ids))
        if unit_ids:attrs['knowledgeUnitIds']=list(dict.fromkeys(unit_ids))
        if reading_ids:attrs['documentReadingIds']=list(dict.fromkeys(reading_ids))
        content.append({'type':'paragraph','attrs':attrs,'content':children})
    old=(current or {}).get('content',[]);targets=set(targets or []);locks=set(locks or [])
    if targets:
        positions=[i for i,n in enumerate(old) if n.get('attrs',{}).get('nodeId') in targets]
        if not positions or len(positions)!=len(targets) or positions!=list(range(min(positions),max(positions)+1)):fail('INVALID_SELECTION','定向修改需要当前连续段落')
        if any(n['type']!='paragraph' or n.get('attrs',{}).get('nodeId') in locks or documents.visual_paragraph(n) for n in old[min(positions):max(positions)+1]):fail('LOCKED','选区包含锁定、图文关联、交叉引用或非段落内容，请使用润写或手动编辑',409)
        content=old[:min(positions)]+content+old[max(positions)+1:]
    else:
        for i,n in enumerate(old):
            if n['type']!='paragraph' or n.get('attrs',{}).get('nodeId') in locks or documents.visual_paragraph(n):content.insert(min(i,len(content)),n)
    return documents.normalize({'type':'doc','content':content}),draft
