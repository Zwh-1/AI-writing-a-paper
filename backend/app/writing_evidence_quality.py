"""Compile exact, source-bound citation intentions without granting semantic support.

The caller supplies the *actual sent context*, never a model's claimed reading
history.  This module uses immutable versions and the existing evidence, citation
and claim stores.  No model/network calls and no new schema are required.
"""
from copy import deepcopy
import hashlib
import re

from fastapi import HTTPException
from pydantic import ValidationError

from . import db, security
from .schemas import ClaimSpan, CitationIntent
from .writing_agent.common import digest

VERSION = 'writing_evidence_quality.v1'
LINK_KEYS = ('source_ids', 'knowledge_unit_ids', 'document_reading_ids', 'evidence_ids', 'fact_ids')
ATTR_KEYS = {'source_ids':'sourceIds', 'knowledge_unit_ids':'knowledgeUnitIds',
             'document_reading_ids':'documentReadingIds', 'evidence_ids':'evidenceIds', 'fact_ids':'factIds'}
_RESULT = re.compile(r'(?:测试|实验|实测|测量|验证|运行|性能).{0,22}(?:表明|证明|达到|为\s*\d|提升|降低|通过|成功)|已部署|\d+(?:\.\d+)?\s*(?:毫秒|ms|QPS|qps|%|％)')
_NEGATIVE = re.compile(r'尚未|未执行|未测|待验证|待测试|计划|拟|预计|不能证明|不代表|不等于|无法证明|未能证明')


def text_hash(text):
    """Public hash format for the precise-citation contract (UTF-8 SHA-256)."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _items(context, key):
    value = context.get(key, [])
    return list(value.values()) if isinstance(value, dict) else list(value or [])


def _by_id(items, *keys):
    result={}
    for item in items:
        if not isinstance(item,dict):continue
        for key in keys:
            if not item.get(key):continue
            identity=str(item[key])
            result[identity]=merge_unit_readings(result[identity],item) if 'unit_id' in keys and identity in result else item
    return result


def unit_read_observations(unit):
    """Return original observations, never an invented enclosing character range."""
    if isinstance(unit.get('read_observations'),list):
        return [deepcopy(row) for row in unit['read_observations']]
    return [{key:deepcopy(value) for key,value in unit.items() if key!='read_observations'}]


def merge_unit_readings(first,second):
    for key in ('unit_id','file_id','parse_id','unit_hash','content_hash'):
        if first.get(key) is not None and second.get(key) is not None and first[key]!=second[key]:
            raise HTTPException(409,'不能合并不同版本的原文读取范围：'+key)
    observations=[]
    for row in [*unit_read_observations(first),*unit_read_observations(second)]:
        if row not in observations:observations.append(row)
    if len(observations)==1:return deepcopy(observations[0])
    result={**deepcopy(first),**deepcopy(second)}
    for key in ('char_start','char_end','range_start','range_end','passages','passage_locations'):
        result.pop(key,None)
    result.update(read_observations=observations,
        text='\n\n[独立读取范围；未读取的间隔不作为证据]\n\n'.join(row.get('text','') for row in observations),
        scope='disjoint_read_observations',complete=False,text_read_complete=False,
        requested_scope_complete=all(row.get('requested_scope_complete',True) for row in observations),
        context_complete=all(row.get('context_complete',True) for row in observations))
    return result


def resolve_unit_readings(pid,unit,remote=True):
    """Validate every separately read passage against its authoritative range."""
    from .knowledge_units import get as get_unit
    uid=unit.get('unit_id') or unit.get('id')
    base=get_unit(pid,uid,remote=remote)
    result=[]
    for observation in unit_read_observations(unit):
        expected=observation.get('unit_hash') or observation.get('content_hash') or unit.get('unit_hash') or unit.get('content_hash')
        if expected and expected!=base['content_hash'] or not base['current']:
            raise ValueError('原文读取范围的单元版本已变化')
        passages=observation.get('passages') or observation.get('passage_locations')
        if passages:
            parts=[]
            for passage in passages:
                pid_=passage.get('unit_id') or passage.get('id') or uid
                original=get_unit(pid,pid_,remote=remote)
                start=passage.get('char_start',passage.get('range_start',0))
                end=passage.get('char_end',passage.get('range_end',len(original['text'])))
                text=passage.get('text',original['text'][start:end] if type(start)is int and type(end)is int else '')
                parts.append((original,start,end,text,passage.get('unit_hash') or passage.get('content_hash')))
            if observation.get('text') and observation['text']!='\n\n'.join(part[3] for part in parts):
                raise ValueError('实际已读文本与列出的原文范围不一致')
        else:
            text=observation.get('text','')
            start=observation.get('char_start',observation.get('range_start'))
            end=observation.get('char_end',observation.get('range_end'))
            if start is None and end is None:
                start=base['text'].find(text) if text else -1
                if start<0 or text!=base['text'] and base['text'].find(text,start+1)>=0:
                    raise ValueError('已读文本无法唯一定位到原文范围')
                end=start+len(text)
            parts=[(base,start,end,text,expected)]
        for original,start,end,text,expected in parts:
            if not original['current'] or expected and expected!=original['content_hash']:
                raise ValueError('实际读取片段的原文版本已变化')
            if type(start)is not int or type(end)is not int or not 0<=start<end<=len(original['text']) or original['text'][start:end]!=text:
                raise ValueError('实际读取片段的字符范围与原文不一致')
            record={'unit_id':original['id'],'file_id':original['file_id'],'parse_id':original['parse_id'],
                    'unit_hash':original['content_hash'],'text':text,'char_start':start,'char_end':end,
                    'reading_id':observation.get('reading_id'), 'locator':original['locator'],
                    'ordinal':original.get('ordinal'),'unit_characters':len(original['text'])}
            if record not in result:result.append(record)
    return result


def _contiguous_read_texts(observations):
    """Join only demonstrably adjacent ranges, leaving unread gaps separate."""
    groups=[];last=None
    for row in sorted(observations,key=lambda row:(str(row.get('parse_id')),row.get('ordinal') or 0,row['char_start'],row['char_end'])):
        same=bool(last and row['unit_id']==last['unit_id'] and row['char_start']<=last['char_end'])
        adjacent=bool(last and row.get('parse_id')==last.get('parse_id') and row.get('ordinal')==((last.get('ordinal') or 0)+1)
                      and row['char_start']==0 and last['char_end']==last['unit_characters']
                      and row.get('locator',{}).get('context_stream')==last.get('locator',{}).get('context_stream'))
        if same:
            groups[-1]+=row['text'][max(0,last['char_end']-row['char_start']):]
            if row['char_end']>last['char_end']:last=row
        elif adjacent:groups[-1]+='\n\n'+row['text'];last=row
        else:groups.append(row['text']);last=row
    return groups


def _decode(value, default=None):
    return db.loads(value, {} if default is None else default) if isinstance(value, str) else value or ({} if default is None else default)


def _finding(code, message, claim_id='', **details):
    return {'id':digest([VERSION, code, claim_id, details])[:24], 'code':code,
            'kind':code, 'severity':'blocker', 'message':message, 'claim_id':claim_id,
            'method':'deterministic_evidence_contract', 'requires_evidence':True,
            'source_ids':[], 'evidence_ids':[], 'reference_ids':[],
            'repair_query':details.get('text') or message, **details}


def assess_result_evidence(item):
    """Classify what a result record can establish; kind alone never attests a run.

    Imported measurements may be legitimate without a platform run_id or code
    revision.  They still need a traceable raw record, measured state and a
    verification marker.  Unverified declarations remain usable as declarations.
    """
    item = item if isinstance(item,dict) else {}
    payload = item.get('payload', item)
    payload = payload if isinstance(payload,dict) else {}
    meta = _decode(payload.get('metadata', {}))
    meta = meta if isinstance(meta,dict) else {}
    status = str(payload.get('status', meta.get('status', ''))).lower()
    verification = str(payload.get('verification', meta.get('verification', ''))).lower()
    kind = item.get('kind', '')
    planned = status in {'planned','proposed','pending','not_run','not_executed','draft'} or bool(meta.get('planned'))
    actual = meta.get('actual_execution') is True or payload.get('actual_execution') is True
    imported = bool(meta.get('verification_origin') == 'imported' or meta.get('imported') is True or
                    meta.get('import_record') or payload.get('import_record'))
    raw = bool(meta.get('run_id') or meta.get('receipt') or meta.get('raw_data_sha256') or
               meta.get('input_sha256') or meta.get('source_file_id') or meta.get('import_record') or
               payload.get('artifact_sha256') or payload.get('raw_data_sha256'))
    verified = verification in {'verified','human_confirmed','human_reviewed','confirmed','reviewed','measured'}
    measured = status in {'measured','executed','completed','passed','deployed'}
    if planned:
        level, reason = 'planned', '记录仍为计划或尚未执行，不能写成实测结果'
    elif kind in {'code_observation','test_definition'}:
        level, reason = 'static', '代码或测试定义仅证明静态实现，不能证明实际执行'
    elif kind == 'author_statement':
        level, reason = 'declared', '作者登记说明不自动证明实际运行或实测效果'
    elif actual and raw:
        level, reason = 'executed', '有实际执行标记及可追溯的原始产物；仍须核对指标和适用范围'
    elif imported and raw and (verified or measured):
        level, reason = 'imported_measurement', '有可追溯导入原始记录及测量/核验状态；仍须核对口径'
    else:
        level, reason = 'unverified', '缺少实际执行或可核对的导入原始记录，不能由data_result名称提升等级'
    return {'level':level, 'can_assert_measurement':level in {'executed','imported_measurement'},
            'reason':reason, 'status':status, 'verification':verification,
            'actual_execution':actual, 'imported':imported, 'traceable_raw_record':raw}


def _locate(text, span):
    start, end = span.get('start_cp'), span.get('end_cp')
    quoted = span['text']
    if (start is None) != (end is None):
        raise ValueError('主张的起止范围必须同时提供')
    if start is None:
        start = text.find(quoted)
        if start < 0 or text.find(quoted, start + 1) >= 0:
            raise ValueError('主张原句必须在正文中唯一出现；重复原句请提供精确范围')
        end = start + len(quoted)
    if not 0 <= start < end <= len(text) or text[start:end] != quoted:
        raise ValueError('主张范围与实际正文不一致')
    if span.get('text_hash') and span['text_hash'] not in (text_hash(quoted), digest(quoted)):
        raise ValueError('主张原句哈希与正文不一致')
    return start, end


def _source_records(pid, links, context, manifest):
    """Validate sent IDs, authoritative permissions/versions and actual read text."""
    records = []
    sources = _by_id(_items(context, 'sources'), 'id', 'source_id')
    units = _by_id(_items(context, 'knowledge_units'), 'unit_id', 'id')
    readings = _by_id(_items(context, 'document_readings'), 'reading_id', 'id')
    observed = _by_id([*_items(context, 'project_evidence'), *_items(context, 'evidence')], 'id', 'evidence_id')
    for sid in links.get('source_ids', []):
        if sid not in sources:
            raise ValueError('引用了本次上下文未提供的资料来源：' + sid)
        row = db.one('SELECT s.*,f.sha256 file_hash,f.project_id,f.role,f.extension FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?', (sid,pid))
        from .knowledge_service import source_is_excluded
        if not row or not security.file_allows_remote(row['file_id'],pid) or source_is_excluded(pid,sid):
            raise ValueError('引用资料不存在、被排除或外发权限已撤回')
        from .material_policy import source_context
        if not source_context({'id':row['file_id'],'role':row['role'],'extension':row['extension']},'research')['allowed_for_intent']:
            raise ValueError('规范、教学范例或未获研究用途许可的原文不能作为研究事实引用')
        sent = sources[sid]
        if sent.get('text') and sent['text'] != row['text']:
            raise ValueError('已发送资料原文与当前版本不一致')
        # A same-file hit is not authorization to cite another unread range.
        if not sent.get('text'):
            observed=[]
            for unit in units.values():
                if unit.get('file_id')==row['file_id']:observed.extend(resolve_unit_readings(pid,unit,remote=True))
            canonical=' '.join(row['text'].split())
            if not canonical or not any(canonical in ' '.join(text.split()) for text in _contiguous_read_texts(observed)):
                raise ValueError('资料只有检索身份，对应完整原文不在本次实际已读连续范围中')
        actual_text=sent.get('text') or row['text']
        records.append({'type':'source','id':sid,'file_id':row['file_id'], 'text':actual_text,
                        'version':digest([row['text'],row['file_hash']]), 'locator':_decode(row['locator'])})
    for uid in links.get('knowledge_unit_ids', []):
        if uid not in units:
            raise ValueError('引用了本次上下文未读取的知识单元：' + uid)
        from .knowledge_units import get as get_unit
        row = get_unit(pid,uid,remote=True)
        sent = units[uid]
        expected = manifest.get('knowledge_unit_versions',{}).get(uid) or sent.get('unit_hash') or sent.get('content_hash')
        if not expected or expected != row['content_hash'] or not row.get('current',True):
            raise ValueError('知识单元版本已变化或没有冻结版本')
        if not sent.get('text') or sent.get('status') in {'not_read','needs_narrower_range','deferred'}:
            raise ValueError('知识单元未实际读入当前上下文')
        for actual in resolve_unit_readings(pid,sent,remote=True):
            records.append({'type':'knowledge_unit','id':actual['unit_id'],'file_id':actual['file_id'],'text':actual['text'],
                            'version':actual['unit_hash'],'locator':actual['locator'],
                            'range_start':actual['char_start'],'range_end':actual['char_end'],
                            'read_scope':{'parse_id':actual['parse_id'],'scope':'explicit_partial_range',
                                'passage_ids':[actual['unit_id']], 'reading_ranges':[{key:actual[key] for key in ('unit_id','char_start','char_end','reading_id')}]}})
    for rid in links.get('document_reading_ids', []):
        if rid not in readings:
            raise ValueError('引用了本次未提供的文献阅读：' + rid)
        from .document_reading import context as reading_context
        row = reading_context(pid,rid,remote=True)
        expected = manifest.get('document_reading_versions',{}).get(rid) or readings[rid].get('version')
        if not row['current'] or row['version'] != expected or row['intent'] != 'research' or not row['passages']:
            raise ValueError('阅读版本、用途或已读范围不满足本次引用')
        records.append({'type':'document_reading','id':rid,'file_id':row['file_id'],
                        'text':'\n'.join(p['text'] for p in row['passages']), 'version':expected,
                        'locator':{'reading_id':rid}, 'read_scope':deepcopy(row)})
    for eid in links.get('evidence_ids', []):
        if eid not in observed:
            raise ValueError('引用了本次上下文未提供的项目证据：' + eid)
        from .writing_agent import evidence
        row = evidence.get(pid,eid,remote=True)
        expected = manifest.get('evidence_versions',{}).get(eid) or observed[eid].get('origin_version')
        if not row['version_current'] or row['validity'] not in {'current','historical'} or (expected and row['origin_version'] != expected):
            raise ValueError('项目证据已失效或版本发生变化')
        payload = row['payload']
        file_id = payload.get('file_id')
        if not file_id and row['origin_type'] == 'document_reading':
            file_id = payload.get('file_id')
        if not file_id and row['origin_type'] == 'knowledge_unit':
            file_id = payload.get('file_id')
        records.append({'type':'evidence','id':eid,'file_id':file_id,
                        'text':payload.get('text','') or '\n'.join(p.get('text','') for p in payload.get('passages',[])),
                        'version':row['origin_version'], 'locator':_decode(row.get('locator_json',{})),
                        'evidence':row})
    facts=_by_id(_items(context,'facts'),'id')
    for fid in links.get('fact_ids',[]):
        if fid not in facts:raise ValueError('引用了本次未提供的研究事实：'+fid)
        row=db.one('SELECT * FROM facts WHERE id=? AND project_id=?',(fid,pid))
        expected=manifest.get('fact_versions',{}).get(fid,manifest.get('fact_revisions',{}).get(fid,facts[fid].get('revision')))
        if not row or expected is None or str(row['revision'])!=str(expected):raise ValueError('研究事实版本已变化或没有冻结版本')
        for sid in _decode(row['source_ids'],[]):
            source=db.one('SELECT file_id FROM sources WHERE id=?',(sid,))
            if not source or not security.file_allows_remote(source['file_id'],pid):raise ValueError('研究事实的原始来源不允许外发')
        records.append({'type':'fact','id':fid,'file_id':None,'text':row['statement'],'version':str(row['revision']),
                        'locator':{'fact_id':fid,'revision':row['revision']}})
    return records


def compile_paragraph(pid, paragraph, context, manifest=None, *, node_id=None):
    """Return a native paragraph and precise proposals, or repairable findings.

    ``handled=False`` lets callers retain their existing legacy paragraph path.
    ``coverage_complete`` only means the mapping contract was checked, never that
    the sources semantically establish the claims.
    """
    manifest = manifest or {}
    text = paragraph.get('text','')
    node = {'type':'paragraph','attrs':{'nodeId':node_id or db.uid()},'content':[{'type':'text','text':text}] if text else []}
    findings, mappings, proposals = [], [], []
    handled = bool(paragraph.get('claim_spans') or paragraph.get('citation_intents'))
    for key, attr in ATTR_KEYS.items():
        if paragraph.get(key):node['attrs'][attr] = list(dict.fromkeys(paragraph[key]))
    if not handled:
        return {'node':node,'citation_proposals':[],'findings':[],'claim_spans':[], 'coverage_complete':True,'handled':False}
    claims = {}
    for raw in paragraph.get('claim_spans',[]):
        identity = raw.get('local_id','') if isinstance(raw,dict) else ''
        try:
            span = ClaimSpan.model_validate(raw).model_dump()
            if span['local_id'] in claims:raise ValueError('主张local_id重复')
            start,end = _locate(text,span)
            # A paragraph may contain mutually different claims.  Never attach
            # every paragraph source to every claim merely because it is nearby.
            links = {key:list(dict.fromkeys(span.get(key,[]) or
                     (paragraph.get(key,[]) if len(paragraph.get('claim_spans',[])) == 1 else []))) for key in LINK_KEYS}
            records = _source_records(pid,links,context,manifest)
            mapping = {**span,'start_cp':start,'end_cp':end,'text_hash':text_hash(span['text']),
                       'source_records':[{k:v for k,v in r.items() if k not in {'text','evidence'}} for r in records],
                       'support':'not_checked','method':'deterministic_mapping','evidence_ids':links['evidence_ids']}
            for key in LINK_KEYS:
                mapping[key] = links[key]
                if links[key]:node['attrs'][ATTR_KEYS[key]] = list(dict.fromkeys([*node['attrs'].get(ATTR_KEYS[key],[]),*links[key]]))
            claims[span['local_id']] = (mapping,records)
            mappings.append(mapping)
        except (ValueError,ValidationError,HTTPException,KeyError,TypeError) as error:
            findings.append(_finding('claim_mapping_invalid','主张定位或已读依据无效：'+str(error),identity))
    refs = _by_id(_items(context,'references'),'id','reference_id')
    insertions = []
    seen = set()
    for raw in paragraph.get('citation_intents',[]):
        identity = raw.get('claim_id','') if isinstance(raw,dict) else ''
        try:
            intent = CitationIntent.model_validate(raw).model_dump()
            if identity not in claims:raise ValueError('引用意图没有对应的有效原子主张')
            mapping, records = claims[identity]
            refid = intent['reference_id']
            if refid not in refs:raise ValueError('引用不属于本次提供的书目')
            ref = db.one('SELECT * FROM bibliography WHERE id=? AND project_id=?',(refid,pid))
            if not ref or refs[refid].get('file_id') != ref['file_id']:raise ValueError('书目原件或所属项目不一致')
            expected_ref = manifest.get('reference_versions',{}).get(refid)
            if expected_ref is not None and str(expected_ref) != str(ref['revision']):raise ValueError('书目版本已变化')
            intent_links = {key:list(dict.fromkeys(intent.get(key,[]))) for key in LINK_KEYS}
            records = [*records,*_source_records(pid,intent_links,context,manifest)]
            matching = {(r['type'],r['id'],r.get('range_start'),r.get('range_end'),digest(r.get('read_scope'))):r
                        for r in records if r.get('file_id') == ref['file_id']}
            if not matching:raise ValueError('引用没有对应文献的实际已读原文，请补读后再关联')
            # Intent-only evidence is also part of the precise claim mapping.
            for record in matching.values():
                if record['type']=='evidence' and record['id'] not in mapping['evidence_ids']:
                    mapping['evidence_ids'].append(record['id'])
                archived={k:v for k,v in record.items() if k not in {'text','evidence'}}
                if archived not in mapping['source_records']:mapping['source_records'].append(archived)
            for record in matching.values():
                expected = intent['source_versions'].get(record['id']) or intent.get('source_version')
                if expected and expected != record['version']:raise ValueError('引用意图的来源版本与冻结原文不一致')
            if intent['quote'] and not any(intent['quote'] in record['text'] for record in matching.values()):
                raise ValueError('原文摘录不在本次实际读取范围内')
            if intent['kind'] == 'quote' and not intent['quote']:raise ValueError('直接引用必须提供可逐字核对的原文摘录')
            marker=(identity,refid)
            if marker in seen:continue
            seen.add(marker)
            cid=db.uid()
            detail={key:deepcopy(mapping[key]) for key in ('local_id','text','text_hash','start_cp','end_cp','role')}
            detail.update(support='not_checked',method='deterministic_mapping',
                          source_records=[{k:v for k,v in r.items() if k not in {'text','evidence'}} for r in matching.values()])
            cite={'type':'citation','attrs':{'citationId':refid,'nodeId':cid,'claimMapping':detail}}
            insertions.append((mapping['end_cp'],cite))
            by_type=lambda typ:list(dict.fromkeys(r['id'] for r in matching.values() if r['type']==typ))
            source_ids=by_type('source')
            for r in matching.values():
                observed=r.get('evidence') or {}
                if observed.get('origin_type')=='source':source_ids.append(observed['origin_id'])
            read_scopes={}
            for record in matching.values():
                if record['type']!='knowledge_unit':continue
                scope=read_scopes.setdefault(record['id'],{'passage_ids':[record['id']],'reading_ranges':[],'scope':'explicit_read_observations'})
                for observed_range in record.get('read_scope',{}).get('reading_ranges',[]):
                    if observed_range not in scope['reading_ranges']:scope['reading_ranges'].append(observed_range)
            proposals.append({'node_id':cid,'reference_id':refid,'source_ids':list(dict.fromkeys(source_ids)),
                              'knowledge_unit_ids':by_type('knowledge_unit'),'document_reading_ids':by_type('document_reading'),
                              'knowledge_read_scopes':read_scopes,
                              'kind':intent['kind'],'excerpt':intent['quote'],'claim_mapping':detail})
        except (ValueError,ValidationError,HTTPException,KeyError,TypeError) as error:
            findings.append(_finding('citation_evidence_gap','引用需补证或修正：'+str(error),identity,
                                     reference_id=raw.get('reference_id','') if isinstance(raw,dict) else ''))
    if insertions:
        content=[];position=0
        for offset,cite in sorted(insertions,key=lambda value:value[0]):
            if offset>position:content.append({'type':'text','text':text[position:offset]})
            content.append(cite);position=offset
        if position<len(text):content.append({'type':'text','text':text[position:]})
        node['content']=content
    # Sentence analyses count each native citation as one object replacement.
    for mapping in mappings:
        mapping['rendered_start_cp']=mapping['start_cp']+sum(offset<=mapping['start_cp'] for offset,_ in insertions)
        mapping['rendered_end_cp']=mapping['end_cp']+sum(offset<mapping['end_cp'] for offset,_ in insertions)
    if mappings:node['attrs']['claimEvidence']=mappings
    return {'node':node,'citation_proposals':proposals,'findings':findings,'claim_spans':mappings,
            'coverage_complete':not findings,'handled':True,'semantic_support':'not_checked'}


def _asserts_measurement(text):
    return any(_RESULT.search(clause) and not _NEGATIVE.search(clause)
               for clause in re.split(r'[，,；;。！？!?]',text))


def _result_item(pid,item):
    value=deepcopy(item)
    if item.get('id') and db.one('SELECT id FROM evidence_items WHERE id=? AND project_id=?',(item['id'],pid)):
        from .writing_agent.evidence import get
        value=get(pid,item['id'])
    payload=value.setdefault('payload',{})
    aid=payload.get('artifact_id')
    if aid:
        artifact=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(aid,pid))
        if artifact:
            payload['metadata']=_decode(artifact['metadata'])
            payload['artifact_sha256']=artifact['sha256']
    return value


def check_candidate(pid, paragraphs, context, manifest=None):
    """Local integrity/level checks, returning actionable gaps for the caller."""
    from .writing_agent.sentences import ranges
    results=[];findings=[];claim_checks=[]
    facts=_by_id(_items(context,'facts'),'id')
    observed=_by_id([*_items(context,'project_evidence'),*_items(context,'evidence')],'id','evidence_id')
    for index,paragraph in enumerate(paragraphs):
        result=compile_paragraph(pid,paragraph,context,manifest)
        for finding in result['findings']:finding['paragraph_index']=index
        results.append(result);findings.extend(result['findings'])
        text=paragraph.get('text','')
        linked_records=[]
        if not result['handled'] and any(paragraph.get(key) for key in LINK_KEYS):
            try:linked_records=_source_records(pid,{key:paragraph.get(key,[]) for key in LINK_KEYS},context,manifest or {})
            except (ValueError,HTTPException,KeyError,TypeError) as error:
                findings.append(_finding('claim_mapping_invalid','段落依据不是当前已读有效来源：'+str(error),
                                         paragraph_index=index,text=text,repair_query=text))
        for ordinal,(start,end) in enumerate(ranges(text)):
            sentence=text[start:end]
            linked=bool(linked_records) or any(span.get('start_cp',0)<end and span.get('end_cp',0)>start and
                       (span.get('source_records') or span.get('evidence_ids')) for span in result['claim_spans'])
            identity=f'p{index+1}.s{ordinal+1}'
            requires=bool(_asserts_measurement(sentence) or re.search(r'研究表明|研究发现|文献表明|证明了|证实了|显著(?:提高|降低|优于)|根据.{0,30}(?:数据|文献|实验)',sentence))
            state='unchecked' if linked else 'unsupported' if requires else 'not_assessed'
            claim_checks.append({'claim_id':identity,'paragraph_index':index,'start_cp':start,'end_cp':end,
                                 'text':sentence,'text_hash':text_hash(sentence),'support':state,
                                 'evidence_ids':paragraph.get('evidence_ids',[]),'source_ids':paragraph.get('source_ids',[]),
                                 'method':'local_scope_inventory','requires_semantic_check':True})
            if requires and not linked:
                findings.append(_finding('claim_evidence_gap','实质主张没有关联本次实际读取的依据',identity,
                                         paragraph_index=index,text=sentence,repair_query=sentence))
        if not _asserts_measurement(text):continue
        selected=[]
        fact_ids=list(dict.fromkeys([*paragraph.get('fact_ids',[]),
                      *[fid for span in result['claim_spans'] for fid in span.get('fact_ids',[])]]))
        for identity in fact_ids:
            fact=facts.get(identity)
            if fact:selected.append(_result_item(pid,{'kind':'data_result','origin_type':'fact','payload':fact}))
        for identity in paragraph.get('evidence_ids',[]):
            if identity in observed:selected.append(_result_item(pid,observed[identity]))
        selected.extend(_result_item(pid,observed[eid]) for span in result['claim_spans'] for eid in span.get('evidence_ids',[]) if eid in observed)
        if selected and not any(assess_result_evidence(item)['can_assert_measurement'] for item in selected):
            findings.append(_finding('result_evidence_level','实测或执行结论缺少足够等级的原始记录；请补证或恢复为计划/未核查状态',
                                     paragraph_index=index, text=text, repair_query=text,
                                     evidence_ids=paragraph.get('evidence_ids',[]),evidence_levels=[assess_result_evidence(item) for item in selected]))
    return {'version':VERSION,'paragraphs':results,'findings':findings,'coverage_complete':not findings,'mapping_complete':not findings,
            'passed':not findings,'claim_checks':claim_checks,'semantic_support':'not_checked'}


def validate_mapping_attributes(attrs, citation=False):
    """Drop stale text mappings in the save path; reject malformed metadata."""
    key='claimMapping' if citation else 'claimEvidence'
    value=attrs.get(key)
    if value is None:return None
    entries=[value] if citation else value
    if not isinstance(entries,list) or len(entries)>100 or any(not isinstance(entry,dict) for entry in entries):
        raise HTTPException(422,'精确引用主张映射格式无效')
    for entry in entries:
        if not isinstance(entry.get('local_id'),str) or not 1<=len(entry['local_id'])<=80:
            raise HTTPException(422,'精确引用主张标识无效')
        if not isinstance(entry.get('text'),str) or len(entry['text'])>20000 or entry.get('text_hash') != text_hash(entry['text']):
            raise HTTPException(422,'精确引用主张原文或哈希无效')
        if any(type(entry.get(key)) is not int or entry[key]<0 for key in ('start_cp','end_cp')) or entry['end_cp']<=entry['start_cp']:
            raise HTTPException(422,'精确引用主张范围无效')
        for key in ('rendered_start_cp','rendered_end_cp'):
            if key in entry and (type(entry[key]) is not int or entry[key]<0):
                raise HTTPException(422,'精确引用渲染范围无效')
        if entry.get('role','fact') not in {'fact','inference','purpose','transition'}:
            raise HTTPException(422,'精确引用主张角色无效')
        for key in LINK_KEYS:
            values=entry.get(key,[])
            if not isinstance(values,list) or len(values)>60 or any(not isinstance(identity,str) or not identity or len(identity)>100 for identity in values):
                raise HTTPException(422,'精确引用主张依据列表无效')
        records=entry.get('source_records',[])
        if not isinstance(records,list) or len(records)>240:
            raise HTTPException(422,'精确引用原文范围列表无效')
        for record in records:
            if not isinstance(record,dict) or record.get('type') not in {'source','knowledge_unit','document_reading','evidence','fact'}:
                raise HTTPException(422,'精确引用原文类型无效')
            if any(not isinstance(record.get(key),str) or not 1<=len(record[key])<=100 for key in ('id','version')):
                raise HTTPException(422,'精确引用原文身份或版本无效')
            if record.get('file_id') is not None and (not isinstance(record['file_id'],str) or len(record['file_id'])>100):
                raise HTTPException(422,'精确引用原件标识无效')
            if 'locator' in record and not isinstance(record['locator'],dict):
                raise HTTPException(422,'精确引用原文定位无效')
    return deepcopy(value)


def _record_current(pid, record, remote=False):
    typ,identity=record.get('type'),record.get('id')
    try:
        if typ=='source':
            row=db.one('SELECT s.text,s.file_id,f.sha256 FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(identity,pid))
            return bool(row and (not remote or security.file_allows_remote(row['file_id'],pid)) and digest([row['text'],row['sha256']])==record.get('version'))
        if typ=='knowledge_unit':
            from .knowledge_units import get
            row=get(pid,identity,remote=remote)
            return row['current'] and row['content_hash']==record.get('version')
        if typ=='document_reading':
            from .document_reading import context
            row=context(pid,identity,remote=remote)
            return row['current'] and row['version']==record.get('version')
        if typ=='evidence':
            from .writing_agent.evidence import get
            row=get(pid,identity,remote=remote)
            return row['version_current'] and row['origin_version']==record.get('version') and row['validity'] in {'current','historical'}
        if typ=='fact':
            row=db.one('SELECT revision FROM facts WHERE id=? AND project_id=?',(identity,pid))
            return bool(row and str(row['revision'])==record.get('version'))
    except HTTPException:return False
    return False


def persist_document_mappings(pid, revision_id, document, strict=False):
    """Archive exact mappings and link unverified claims to existing sentence tables.

    This mapping analysis stays unverified.  Neither saving nor copying metadata
    can confer human review or source entailment.
    """
    from . import documents
    from .writing_agent import sentences,claims,evidence
    blocks=[n for n in documents.nodes(document) if n.get('type') in {'paragraph','heading'} and n.get('attrs',{}).get('claimEvidence')]
    if not blocks:return
    analysis=sentences.analyze(pid,document,revision_id=revision_id)
    archive=[]
    for block in blocks:
        body=sentences.paragraph_text(block)
        for mapping in block['attrs']['claimEvidence']:
            start=mapping.get('rendered_start_cp',mapping['start_cp']);end=mapping.get('rendered_end_cp',mapping['end_cp'])
            # Editing or moving citation atoms invalidates an old mapping.  Keep
            # the historical revision intact and require new mapping for new text.
            if not 0<=start<end<=len(body) or body[start:end].replace('\ufffc','') != mapping['text']:
                if strict:raise HTTPException(409,{'code':'INPUT_VERSION_CHANGED','message':'精确引用主张定位与实际正文不一致'})
                archive.append({'node_id':block['attrs']['nodeId'],'local_id':mapping['local_id'],'state':'stale_text_mapping'});continue
            if any(not _record_current(pid,record,remote=strict) for record in mapping.get('source_records',[])):
                if strict:raise HTTPException(409,{'code':'INPUT_VERSION_CHANGED','message':'精确引用的冻结原文依据已变化，请补读并重新核验'})
                archive.append({'node_id':block['attrs']['nodeId'],'local_id':mapping['local_id'],'state':'stale_source_mapping'});continue
            ids=[]
            for eid in mapping.get('evidence_ids',[]):
                item=evidence.get(pid,eid)
                if item['version_current'] and item['validity'] in {'current','historical'}:ids.append(eid)
            for record in mapping.get('source_records',[]):
                if record['type']=='source':observed=evidence.sync_source(pid,record['id'],'literature')
                elif record['type']=='document_reading':observed=evidence.sync_reading(pid,record['id'])
                elif record['type']=='fact':observed=evidence.sync_fact(pid,record['id'])
                elif record['type']=='knowledge_unit' and record.get('range_start') is not None and record.get('range_end') is not None:
                    observed=evidence.sync_unit(pid,record['id'],{'before':0,'after':0,'start':record['range_start'],
                        'end':record['range_end'],'max_chars':40000,'intent':'research'})
                else:continue
                if observed['id'] not in ids:ids.append(observed['id'])
            claim=claims.create(pid,{'kind':mapping.get('role','fact'),'proposition':{'statement':mapping['text'],
                'support':'unknown','method':'deterministic_mapping','source_records':mapping.get('source_records',[]),
                'mapping_local_id':mapping['local_id'],'mapping_revision_id':revision_id},
                'evidence':[{'evidence_id':eid,'scope':{'source_records':mapping.get('source_records',[])}} for eid in ids]})
            for sentence in analysis['sentences']:
                if sentence['node_id']==block['attrs']['nodeId'] and sentence['start_cp']<end and sentence['end_cp']>start:
                    db.execute('INSERT INTO sentence_claim_links VALUES(?,?,?)',(sentence['id'],claim['id'],mapping.get('role','fact')))
            archive.append({'node_id':block['attrs']['nodeId'],'local_id':mapping['local_id'],'claim_version_id':claim['id'],
                            'state':'mapped_not_verified','mapping':mapping})
    db.set_setting('writing_claim_mapping:'+revision_id,{'version':VERSION,'analysis_id':analysis['id'],'revision_id':revision_id,'claims':archive})
