from .models import Judgment
from .common import fail
from .prompts import BOUNDARY
from copy import deepcopy

CHECK_PROMPT=BOUNDARY+'''核对judgment中的每个命题是否得到给定原文支持，条件、否定、版本、实现/运行层次是否准确。只返回 {"findings":[{"node_id":"","sentence_id":"","severity":"blocker|warning|info","kind":"judgment_support","message":"具体问题","evidence_ids":[],"rationale":"简短理由"}],"coverage_complete":true,"summary":"逐项检查范围"}。缺依据或推理跳步不能默认通过。'''

def normalize_redundant_books(data,manifest,references):
    """Remove a duplicate book label only when its concrete evidence is explicit."""
    result=deepcopy(data);corrections=[];allowed=set(manifest.get('provided_evidence_ids',manifest.get('evidence_versions',{})))
    books={r['id']:set(r.get('source_ids',[])) for r in references}
    source_info=manifest.get('source_evidence',{})
    for claim in result.get('claims',[]):
        ids=claim.get('evidence_ids',[]);remove=[]
        for identity in ids:
            if identity not in books or identity in allowed:continue
            concrete=[eid for eid in ids if eid in allowed and source_info.get(eid,{}).get('source_id') in books[identity]]
            if concrete:
                remove.append(identity);corrections.append({'local_id':claim.get('local_id'),'kind':'redundant_book_label_removed','book_id':identity,'explicit_evidence_ids':concrete,'prose_changed':False,'semantic_support':'not_granted'})
        if remove:claim['evidence_ids']=[eid for eid in ids if eid not in remove]
    return result,corrections

def validate(data,manifest):
    result=Judgment.model_validate(data).model_dump();allowed=set(manifest['evidence_versions'])
    if 'provided_evidence_ids' in manifest:allowed.intersection_update(manifest['provided_evidence_ids'])
    if len({c['local_id'] for c in result['claims']})!=len(result['claims']):fail('INVALID_JUDGMENT','判定命题标识重复')
    for c in result['claims']:
        if set(c['evidence_ids'])-allowed:fail('INVALID_EVIDENCE','判定引用未提供证据')
        if not c['evidence_ids']:result['blocking_gaps'].append('命题缺少依据：'+c['statement'])
    if not result['claims']:result['blocking_gaps'].append('没有形成具备依据的命题')
    return result
