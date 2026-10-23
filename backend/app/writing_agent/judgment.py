from .models import Judgment
from .common import fail
from .prompts import BOUNDARY

CHECK_PROMPT=BOUNDARY+'''核对judgment中的每个命题是否得到给定原文支持，条件、否定、版本、实现/运行层次是否准确。只返回 {"findings":[{"node_id":"","sentence_id":"","severity":"blocker|warning|info","kind":"judgment_support","message":"具体问题","evidence_ids":[],"rationale":"简短理由"}],"coverage_complete":true,"summary":"逐项检查范围"}。缺依据或推理跳步不能默认通过。'''

def validate(data,manifest):
    result=Judgment.model_validate(data).model_dump();allowed=set(manifest['evidence_versions'])
    if len({c['local_id'] for c in result['claims']})!=len(result['claims']):fail('INVALID_JUDGMENT','判定命题标识重复')
    for c in result['claims']:
        if set(c['evidence_ids'])-allowed:fail('INVALID_EVIDENCE','判定引用未提供证据')
        if not c['evidence_ids']:result['blocking_gaps'].append('命题缺少依据：'+c['statement'])
    if not result['claims']:result['blocking_gaps'].append('没有形成具备依据的命题')
    return result
