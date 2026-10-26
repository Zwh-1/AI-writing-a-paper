import re
from copy import deepcopy
from pydantic import Field
from typing import Literal
from .models import Strict,Finding,Verification
from .common import owned,blob,unblob,fail,digest
from . import sentences,claims,evidence
from .. import db

class MappedClaim(Strict):
    statement:str=Field(min_length=1,max_length=5000)
    evidence_ids:list[str]=Field(default_factory=list,max_length=30)
    support:Literal['supported','partial','unsupported','contradicted','unknown']='unknown'
    conditions:list[str]=Field(default_factory=list,max_length=30)
class MappedSentence(Strict):
    sentence_id:str
    role:Literal['fact','purpose','inference','transition','unknown']='unknown'
    claims:list[MappedClaim]=Field(default_factory=list,max_length=12)
    # Empty provider metadata cannot carry a claim or replace its conditions.
    claims_note:Literal[None]=None
    conditions:list[str]=Field(default_factory=list,max_length=30)
    findings:list[Finding]=Field(default_factory=list,max_length=30)
class Annotation(Verification):
    sentences:list[MappedSentence]=Field(max_length=250)

def normalize_empty_sentence_metadata(data):
    """Preserve known provider notes without inventing claims or source support."""
    result=deepcopy(data);corrections=[]
    for sentence in result.get('sentences',[]):
        if 'sentence_id_note' in sentence and sentence['sentence_id_note'] is None:
            sentence.pop('sentence_id_note');corrections.append({'sentence_id':sentence.get('sentence_id'),'kind':'empty_identifier_note_removed','prose_changed':False,'semantic_support':'not_granted'})
        if type(sentence.get('evidence_ids')) is list and not sentence['evidence_ids']:
            sentence.pop('evidence_ids')
            corrections.append({'sentence_id':sentence.get('sentence_id'),'kind':'empty_sentence_level_evidence_list_removed','prose_changed':False,'semantic_support':'not_granted'})
        note=sentence.get('role_note')
        if isinstance(note,str) and len(note)<=3000 and isinstance(sentence.get('findings',[]),list):
            sentence.pop('role_note')
            if note.strip():sentence.setdefault('findings',[]).append({'sentence_id':sentence.get('sentence_id',''),'node_id':'','severity':'info','kind':'model_classification_note','message':note,'evidence_ids':[],'rationale':'保留模型对句子角色的说明，不作为原文证据，也不授予语义支持。'})
            corrections.append({'sentence_id':sentence.get('sentence_id'),'kind':'classification_note_preserved_as_information','prose_changed':False,'semantic_support':'not_granted'})
        support=sentence.get('support')
        if support in ('supported','partial','unsupported','contradicted','unknown') and isinstance(sentence.get('findings',[]),list):
            sentence.pop('support')
            severity='info' if support=='supported' else 'blocker' if support in ('unsupported','contradicted') else 'warning'
            sentence.setdefault('findings',[]).append({'sentence_id':sentence.get('sentence_id',''),'node_id':'','severity':severity,'kind':'sentence_support_note','message':'模型另给出的句子整体支持性标记：'+support,'evidence_ids':[],'rationale':'此标记不替代逐项论断的来源映射，也不提升任何论断的支持程度。'})
            corrections.append({'sentence_id':sentence.get('sentence_id'),'kind':'sentence_support_preserved_as_finding','prose_changed':False,'semantic_support':'not_granted'})
    return result,corrections

def normalize_annotation_sources(data,manifest,references):
    """Keep unmatched book labels as explicit gaps, never as concrete evidence."""
    from .judgment import normalize_redundant_books
    result=deepcopy(data);corrections=[];books={row['id'] for row in references};allowed=set(manifest.get('provided_evidence_ids',manifest.get('evidence_versions',{})))
    for sentence in result.get('sentences',[]):
        repaired,notes=normalize_redundant_books({'claims':sentence.get('claims',[])},manifest,references)
        sentence['claims']=repaired['claims'];corrections.extend({**note,'sentence_id':sentence.get('sentence_id')} for note in notes)
        for claim in sentence['claims']:
            unmapped=[identity for identity in claim.get('evidence_ids',[]) if identity in books and identity not in allowed]
            if not unmapped:continue
            claim['evidence_ids']=[identity for identity in claim['evidence_ids'] if identity not in unmapped]
            if claim.get('support','unknown') not in ('unsupported','contradicted'):claim['support']='unknown'
            sentence.setdefault('findings',[]).append({'sentence_id':sentence.get('sentence_id',''),'node_id':'','severity':'warning','kind':'unmapped_bibliographic_reference','message':'模型以书目登记编号代替具体证据，需人工核对：'+','.join(unmapped),'evidence_ids':[],'rationale':'保留原命题、条件与原始响应；书目登记不被自动转换成研究内容的原文证据。'})
            result['coverage_complete']=False
            corrections.append({'sentence_id':sentence.get('sentence_id'),'kind':'unmapped_book_reference_retained_as_gap','reference_ids':unmapped,'prose_changed':False,'semantic_support':'not_granted'})
    return result,corrections

def asserts_execution_result(text):
    """A narrow structural guard, not a general semantic classifier."""
    pattern=r'(?:测试|实验|验证).{0,12}(?:已通过|通过了|结果表明|结果证明|表明|证明)|已部署'
    for match in re.finditer(pattern,text):
        start=max([text.rfind(mark,0,match.start()) for mark in '，,。；;！!?？']+[-1])+1
        clause=text[start:match.end()]
        # Lack of proof is not an assertion that a run succeeded. Ambiguous
        # or double-negative phrasing remains for the independent semantic stage.
        if re.search(r'没有|尚未|尚无|未能|未曾|缺少|缺乏|不能|不足以|无法|不代表|不等于|并非|不得',clause):continue
        return True
    return False

def execution_evidence_missing(pid,text,ids):
    if not asserts_execution_result(text) or not ids:return False
    kinds=[evidence.get(pid,eid)['kind'] for eid in ids]
    if all(k in ('code_observation','test_definition','author_statement') for k in kinds):return True
    return bool(re.search(r'本项目|本系统|本研究|本平台|我们|本文实现|本次',text) and all(k in ('code_observation','test_definition','author_statement','literature','material_observation') for k in kinds))


def save_run(pid,analysis_id,stage,manifest,result,method='model_assisted',status='completed'):
    if analysis_id:owned('analysis',analysis_id,pid)
    identity=db.uid()
    db.execute('INSERT INTO writing_verification_runs VALUES(?,?,?,?,?,?,?,?,?)',(identity,pid,analysis_id,stage,blob(manifest),method,blob(result),status,db.now()))
    return identity

@db.atomic
def apply_annotation(pid,analysis_id,data,manifest):
    for old in db.rows("SELECT * FROM writing_verification_runs WHERE analysis_id=? AND stage='annotation' AND status='completed' ORDER BY created_at DESC",(analysis_id,)):
        previous=unblob(old['input_manifest_blob'])
        keys=('core_version_id','target_snapshot_id','contract_version_id','rule_package_id','evidence_versions','profile_fingerprint')
        if all(previous.get(k)==manifest.get(k) for k in keys):return old['id'],unblob(old['result_blob'])
    parsed=Annotation.model_validate(data).model_dump();a=sentences.get(pid,analysis_id);permitted=set(manifest.get('evidence_versions',{}));known={s['id']:s for s in a['sentences']};seen=set();findings=list(parsed['findings'])
    if 'provided_evidence_ids' in manifest:permitted.intersection_update(manifest['provided_evidence_ids'])
    for item in parsed['sentences']:
        identity=item['sentence_id']
        if identity not in known or identity in seen:fail('INVALID_MAPPING','模型句子映射越界或重复')
        seen.add(identity);old=known[identity]
        for finding in item['findings']:
            if finding['sentence_id'] and finding['sentence_id']!=identity:fail('INVALID_FINDING','句内问题指向其他句子')
            finding['sentence_id']=identity
            if not finding['node_id']:finding['node_id']=old['node_id']
            findings.append(finding)
        if item['role'] in ('fact','inference') and not item['claims']:findings.append({'sentence_id':identity,'node_id':old['node_id'],'severity':'blocker','kind':'unmapped_claim','message':'事实或推论缺少独立论断'})
        if item['role']=='transition' and (re.search(r'\d|已经|实现了|通过了|显著|提高|降低|保证|能够',old['text'])):
            findings.append({'sentence_id':identity,'node_id':old['node_id'],'severity':'warning','kind':'transition_needs_review','message':'过渡句包含事实或效果表达，需要复核分类'})
        db.execute('UPDATE sentence_spans SET role=? WHERE id=?',(item['role'],identity))
        for claim in item['claims']:
            ids=claim['evidence_ids']
            if set(ids)-permitted:fail('INVALID_EVIDENCE','模型引用了本次未提供的证据')
            observations=[evidence.get(pid,eid) for eid in ids]
            if any(e['kind']=='guidance_observation' or e['payload'].get('material_use',{}).get('source_purpose') in ('course_guidance','grading_criteria','writing_example','writing_guidance') for e in observations):
                claim['support']='unknown';findings.append({'sentence_id':identity,'severity':'blocker','kind':'wrong_evidence_purpose','message':'教学正反例、评分条件和写作范例不能充当本项目研究或实现成果的事实证据'})
            material=[e for e in observations if e['origin_type'] in ('source','knowledge_unit','document_reading')]
            assessed=bool(material) and all(e['origin_type']=='document_reading' and e['payload'].get('semantic_context_status') in ('candidate_assessed','human_reviewed_for_question') for e in material)
            if material and len(material)==len(observations) and not assessed:
                claim['support']='unknown';findings.append({'sentence_id':identity,'severity':'warning','kind':'document_context_unassessed','message':'原文已读取不等于当前论断的上下文充分；请按文献核对方法、条件、结果及局限'})
            for eid in ids:
                observed=evidence.get(pid,eid)
                if observed['origin_type']=='knowledge_unit':
                    source=observed['payload']
                    if any(d['state']=='open' for d in source.get('disputes',[])) or not source.get('requested_scope_complete',source.get('context_complete',True)):
                        claim['support']='unknown';findings.append({'sentence_id':identity,'severity':'blocker','kind':'unresolved_source_context','message':'原文存在未处理争议或缺失上下文，不能自动确认支持性'})
                    if not source.get('complete',False):findings.append({'sentence_id':identity,'severity':'warning','kind':'partial_original_range','message':'只读取了明确原文区间，需复查限定条件及结论范围'})
            if item['role'] in ('fact','inference') and not ids:findings.append({'sentence_id':identity,'severity':'blocker','kind':'no_evidence','message':'论断没有具体依据'})
            if claim['support']!='supported':findings.append({'sentence_id':identity,'severity':'blocker' if claim['support'] in ('contradicted','unsupported') else 'warning','kind':claim['support'],'message':'论断支持性需要处理：'+claim['statement']})
            if execution_evidence_missing(pid,claim['statement'],ids):
                findings.append({'sentence_id':identity,'severity':'blocker','kind':'evidence_level','message':'测试定义或源码不能证明测试执行或部署结果'})
            c=claims.create(pid,{'kind':item['role'],'proposition':{'statement':claim['statement'],'conditions':list(dict.fromkeys([*claim['conditions'],*item['conditions']])),'support':claim['support'],'method':'model_assisted'},'evidence':[{'evidence_id':e} for e in ids]})
            db.execute('INSERT INTO sentence_claim_links VALUES(?,?,?)',(identity,c['id'],item['role']))
    for identity in set(known)-seen:findings.append({'sentence_id':identity,'severity':'blocker','kind':'missing_sentence','message':'本句未纳入模型分析'})
    complete=bool(parsed['coverage_complete'] and seen==set(known) and all(i['role']!='unknown' for i in parsed['sentences']))
    result={**parsed,'findings':findings,'coverage_complete':complete,'method':'model_assisted','total_sentences':len(known)}
    db.execute('UPDATE document_analyses SET state=? WHERE id=?',('model_reviewed' if complete else 'incomplete',analysis_id))
    return save_run(pid,analysis_id,'annotation',manifest,result),result

def runs(pid,analysis_id):
    owned('analysis',analysis_id,pid)
    return [{**r,'result':unblob(r['result_blob']),'input_manifest':unblob(r['input_manifest_blob'])} for r in db.rows('SELECT * FROM writing_verification_runs WHERE analysis_id=? ORDER BY created_at',(analysis_id,))]

@db.atomic
def human_review(pid,analysis_id,body):
    from .impact import check_manifest
    a=sentences.get(pid,analysis_id);note=body.get('note','').strip();decision=body.get('decision')
    if decision not in ('approved','needs_revision') or len(note)<8:fail('REVIEW_REQUIRED','请给出明确判断和至少8字的核对依据')
    if a['candidate_id']:
        from .common import candidate
        c=candidate(a['candidate_id'],pid);session=owned('session',c['session_id'],pid)
        if session['status'] in ('queued','running','outcome_unknown','cancelled'):fail('REVIEW_NOT_READY','执行中、远端未知或已取消的候选不能提前登记人工通过',409)
    rr=runs(pid,analysis_id)
    manifest=next((r['input_manifest'] for r in reversed(rr) if r['stage']=='annotation'),None)
    if not manifest:fail('REVIEW_REQUIRED','需先完成句子和论断分析')
    if check_manifest(pid,manifest):fail('INPUT_VERSION_CHANGED','核对所用来源已变化',409)
    for previous in rr:
        if check_manifest(pid,previous['input_manifest']):fail('INPUT_VERSION_CHANGED','扩展原文或其他核验依据已变化',409)
    if a['coverage']['unclassified'] or any(s['role'] in ('fact','inference') and not s['claims'] for s in a['sentences']):fail('COVERAGE_INCOMPLETE','尚有未分类或未关联的事实句')
    for sentence in a['sentences']:
        if sentence['role'] not in ('fact','inference'):continue
        for claim in sentence['claims']:
            links=db.rows('SELECT evidence_id FROM claim_evidence_links WHERE claim_version_id=?',(claim['id'],))
            if not links:fail('REQUIRED_EVIDENCE_MISSING','事实或推论仍有无来源主张；请补证或重新标注后再登记通过')
            for link in links:
                e=evidence.get(pid,link['evidence_id'])
                if not e['version_current'] or e['validity'] not in ('current','historical'):fail('INPUT_VERSION_CHANGED','论断证据已失效',409)
    result={'decision':decision,'note':note,'scope':'all_sentences_and_findings','resolved_finding_ids':body.get('resolved_finding_ids',[]),'reviewed_text_hash':a['text_hash'],'coverage_complete':True,'method':'human_reviewed'}
    if body.get('confirmed_full_scope') is True:
        if decision!='approved' or body.get('reviewed_text_hash')!=a['text_hash'] or len(note)<20:fail('REVIEW_REQUIRED','完整人工替代复核需要当前文本哈希和至少20字核对依据')
        for sentence in a['sentences']:
            for claim in sentence['claims']:
                proposition=db.loads(claim['proposition_json'],{});links=db.rows('SELECT evidence_id FROM claim_evidence_links WHERE claim_version_id=?',(claim['id'],))
                if execution_evidence_missing(pid,proposition.get('statement',''),[r['evidence_id'] for r in links]):fail('REQUIRED_EVIDENCE_MISSING','普通文献、代码或测试定义不能证明本项目实际执行结果')
        for stage in ('annotation','semantic','coherence'):
            prior=[r['id'] for r in rr if r['stage']==stage]
            save_run(pid,analysis_id,stage,manifest,{'findings':[],'coverage_complete':True,'summary':note,'method':'human_reviewed','reviewed_text_hash':a['text_hash'],'scope':'用户声明已逐句核对全部来源、条件、否定、上下文与已列问题','supersedes_runs':prior},'human_reviewed')
        result['explicit_full_scope_override']=True
    run=save_run(pid,analysis_id,'human_review',manifest,result,'human_reviewed')
    db.execute('UPDATE document_analyses SET state=? WHERE id=?',('human_reviewed' if decision=='approved' else 'needs_revision',analysis_id))
    return {'id':run,**result}
