"""Budgeted retrieval of advisory rules and whole source chunks; never clip claims."""
import re
from .. import db
from ..source_payload import token_upper_bound
from .common import digest

VERSION='whole_source_retrieval.v1'

def frame(payload,manifest,system,cfg):
    if manifest.get('stage')=='investigate':
        from .tools import investigation_catalog,investigation_system
        catalog=investigation_catalog(native=cfg.get('tool_mode')=='native',include_knowledge=bool(manifest.get('knowledge_refs')),include_code=bool(manifest.get('target_snapshot_id')))
        native=cfg.get('tool_mode')=='native'
        return investigation_system(system,manifest.get('target_snapshot_id')),{'messages':[{'role':'user','content':db.dumps({'context':payload} if native else {'context':payload,'available_tools':catalog})}],'tools':catalog if native else []}
    from .identifiers import encode
    from .provider_adapter import ALIAS_NOTE
    encoded,aliases=encode(payload,manifest)
    return system+(ALIAS_NOTE if aliases and ALIAS_NOTE not in system else ''),{'messages':[{'role':'user','content':db.dumps(encoded)}],'tools':[]}

def estimate(payload,manifest,system,cfg):
    prompt,body=frame(payload,manifest,system,cfg)
    return token_upper_bound(prompt,db.dumps(body))

def terms(text):
    result=set(re.findall(r'[a-z][a-z0-9_]{1,}',str(text).lower()))
    for run in re.findall(r'[\u4e00-\u9fff]+',str(text)):
        result.update(run[i:i+2] for i in range(len(run)-1))
    return result-{'本节','章节','内容','规则','确认','组织','材料','不足','列出','当前','项目','论文'}

def rank(text,query):
    return len(terms(text)&query)

def fit(payload,manifest,system,cfg):
    manifest['provided_evidence_ids']=[e['id'] for e in payload.get('evidence',[]) if isinstance(e,dict) and 'id' in e]
    limit=int(cfg.get('context_limit',32000))-int(cfg.get('max_output_tokens',8192))
    if estimate(payload,manifest,system,cfg)<=limit:return
    from ..writing_continuity import fit_optional
    fit_optional(payload,manifest,lambda:estimate(payload,manifest,system,cfg)<=limit)
    if estimate(payload,manifest,system,cfg)<=limit:return
    original_rules=payload.get('rules',[])
    # Old/foreign contexts without typed rule provenance must retain every rule.
    modalities=manifest.get('rule_modalities',{})
    required=[r for r in original_rules if not isinstance(r,dict) or modalities.get(r.get('id'))!='recommended']
    recommended=[r for r in original_rules if isinstance(r,dict) and modalities.get(r.get('id'))=='recommended']
    payload['rules']=required
    if 'project_inputs' in payload:payload['project_inputs']['rule_ids']=[r['id'] for r in required if isinstance(r,dict)]
    section=payload.get('section',{});title=section.get('title','');goal=payload.get('contract',{}).get('goal','')
    aliases=[]
    for keys,hints in [
        (('相关技术','研究现状','综述'),'文献综述 国内外研究 相关工作 技术比较 引用 研究进展 不足'),
        (('需求',),'功能需求 非功能需求 业务流程 角色 异常 响应'),
        (('设计',),'架构 模块 数据库 接口 设计 方案'),
        (('实现',),'实现 源码 技术 代码 部署'),
        (('测试','实验'),'测试 实验 指标 数据 结果 对比'),
    ]:
        if any(key in title for key in keys):aliases.append(hints)
    focused=terms(title+' '+' '.join(aliases));query=focused|terms(goal)
    original_evidence=list(payload.get('evidence',[]));original_refs=list(payload.get('references',[]))
    source_info=manifest.get('source_evidence',{})
    pinned=set(manifest.get('pinned_evidence_ids',[]))
    for claim in payload.get('judgment',{}).get('claims',[]):pinned.update(claim.get('evidence_ids',[]))
    sources=[e for e in original_evidence if e['id'] in source_info and e['id'] not in pinned]
    fixed=[e for e in original_evidence if e not in sources]
    selected={e['id'] for e in original_evidence}
    # Whole source chunks can be retrieved later. Unread chunks are a catalogue,
    # never evidence the model is told it has already checked.
    if estimate(payload,manifest,system,cfg)>limit and sources:
        selected={e['id'] for e in fixed}
        def apply_sources():
            payload['evidence']=[e for e in original_evidence if e['id'] in selected]
            active_source_ids={source_info[eid]['source_id'] for eid in selected if eid in source_info and source_info[eid].get('source_id')}
            active_unit_ids={source_info[eid]['knowledge_unit_id'] for eid in selected if eid in source_info and source_info[eid].get('knowledge_unit_id')}
            active_readings={source_info[eid]['reading_id'] for eid in selected if eid in source_info and source_info[eid].get('reading_id')}
            payload['references']=[r for r in original_refs if active_source_ids.intersection(r.get('source_ids',[])) or active_unit_ids.intersection(r.get('knowledge_unit_ids',[])) or active_readings.intersection(r.get('document_reading_ids',[]))]
            payload['available_sources']=[{'evidence_id':e['id'],'title':source_info[e['id']]['title'],'locator':e['payload'].get('locator',{}),'text_bytes':len(db.dumps(e['payload']).encode('utf-8')),**{k:source_info[e['id']][k] for k in ('reading_id','knowledge_unit_id','file_id') if k in source_info[e['id']]}} for e in sources if e['id'] not in selected]
            payload['source_scope']='evidence 中是本次完整提供的原文观察，仍需按问题核对语义；available_sources 仅为未读取目录，不支持具体主张。可用get_evidence读取，文献过长则用read_document_range读取明确范围。'
            if 'project_inputs' in payload:
                payload['project_inputs']['evidence_ids']=[e['id'] for e in payload['evidence']]
                payload['project_inputs']['material_evidence_ids']=[e['id'] for e in payload['evidence'] if e['id'] in source_info]
                payload['project_inputs']['reference_ids']=[r['id'] for r in payload['references']]
        apply_sources()
        ranked=sorted(sources,key=lambda e:(-rank(source_info[e['id']]['title']+' '+e['payload'].get('text',''),query),e['id']))
        # Leave room for one bounded tool observation in the investigation.
        headroom=min(12000,max(0,limit//2)) if manifest.get('stage')=='investigate' else min(1500,max(0,limit//8))
        target=limit-headroom
        for item in ranked:
            selected.add(item['id']);apply_sources()
            if estimate(payload,manifest,system,cfg)>target:
                selected.remove(item['id']);apply_sources()
        manifest['source_selection']={'policy':VERSION,'provided_ids':[e['id'] for e in payload['evidence'] if e['id'] in source_info],'catalogue_only_ids':[e['id'] for e in sources if e['id'] not in selected],'pinned_ids':sorted(pinned),'query_terms':sorted(query),'no_original_text_clipped':True}
        manifest['provided_evidence_ids']=[e['id'] for e in payload['evidence']]
    if recommended:
        payload['rule_scope']='以下完整保留全部强制规则；建议条款按本节相关性及输入预算检索，未载入的建议不视为已核验。'
        included=list(required)
        headroom=min(12000,max(0,limit//2)) if manifest.get('stage')=='investigate' else min(1500,max(0,limit//10))
        ranked=sorted(recommended,key=lambda r:(-rank(r['statement'],focused),-rank(r['statement'],query),r['id']))
        for item in ranked:
            payload['rules']=[*included,item]
            if 'project_inputs' in payload:payload['project_inputs']['rule_ids']=[r['id'] for r in payload['rules'] if isinstance(r,dict)]
            if estimate(payload,manifest,system,cfg)<=limit-headroom:included.append(item)
        payload['rules']=included
        loaded=[r['id'] for r in included if isinstance(r,dict)]
        if 'project_inputs' in payload:payload['project_inputs']['rule_ids']=loaded
        manifest['rule_selection']={'policy':VERSION,'all_applicable_ids':[r['id'] for r in original_rules if isinstance(r,dict)],'mandatory_ids':[r['id'] for r in required if isinstance(r,dict)],'loaded_ids':loaded,'deferred_recommended_ids':[r['id'] for r in recommended if r['id'] not in set(loaded)],'applicable_text_hash':digest(original_rules)}
        manifest['loaded_rule_ids']=loaded
        manifest['required_rule_ids']=[r['id'] for r in required if isinstance(r,dict)]
