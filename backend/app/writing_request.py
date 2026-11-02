"""Fit optional whole documents around the immutable manuscript and selected evidence."""
from fastapi import HTTPException
from . import db
from .source_payload import compact_locator, token_upper_bound


def pack(context, knowledge, system, cfg):
    limit = int(cfg.get('context_limit', 100000))
    output = int(cfg.get('max_output_tokens', 8192))
    deferred = []
    reference_candidates=list(context.get('references',[]))
    original_units = list(knowledge['knowledge_units'])
    automatic_readings=set(knowledge.get('automatic_reading_ids',[]))
    required_files = {u['file_id'] for u in original_units
                      if u.get('reading_id') and u['reading_id'] not in automatic_readings or u['unit_id'] in knowledge['selected_unit_ids']}
    required_files.update(r['file_id'] for r in knowledge.get('document_readings', []) if r['reading_id'] not in automatic_readings)
    optional_project=set(context.get('automatic_project_evidence_ids',[]))
    required_files.update(e['file_id'] for e in context.get('project_evidence',[]) if e.get('file_id') and e['id'] not in optional_project)

    # Version hashes, parser geometry and duplicate passage locations remain in
    # the local manifest. Keep the exact text and its semantic restrictions.
    local_fields = {'id', 'unit_hash', 'file_hash', 'parse_id', 'passage_locations',
                    'reading_scope_note', 'semantic_context_complete', 'status'}
    context['knowledge_units'] = [
        {**{k: v for k, v in unit.items() if k not in local_fields},
         'locator': compact_locator(unit.get('locator'))}
        for unit in context['knowledge_units']
    ]
    policies={}
    for unit in context['knowledge_units']:
        fid=unit['file_id'];value=unit.get('material_use')
        if value is None:continue
        same=[u for u in context['knowledge_units'] if u['file_id']==fid]
        if all(u.get('material_use')==value for u in same):policies[fid]=value
    if policies:
        context['knowledge_file_policies']=policies
        for unit in context['knowledge_units']:
            if unit['file_id'] in policies:unit.pop('material_use',None);unit['material_use_ref']=unit['file_id']
    # A source can point to identical text already present in a full unit.
    # Never infer coverage from similarity or from a different document.
    for source in context['sources']:
        covering = [unit['unit_id'] for unit in context['knowledge_units']
                    if unit['file_id'] == source['file_id']
                    and source['text'].strip() and source['text'] in unit['text']]
        if covering:
            source['text_in_knowledge_units'] = covering
            source.pop('text')

    # Keep research text and mandatory rules. Under pressure remove repeated
    # navigation/state fields whose exact versions remain in the manifest.
    if token_upper_bound(system,db.dumps(context))+output>limit:
        section=context.get('section',{})
        context['section']={k:section[k] for k in ('id','title','kind','goal','target_words','parent_id','dependencies') if k in section}
        if context.get('paper_context'):
            context['paper_context']={k:v for k,v in context['paper_context'].items() if k!='sections'}
        if context.get('continuity'):
            context['continuity']={k:v for k,v in context['continuity'].items() if k!='policy'}
        default_goal='按已确认规则组织本节内容，材料不足时列出缺口。'
        if any(row.get('goal')==default_goal for row in context.get('outline',[])):
            context['outline_default_goal']=default_goal
            context['outline']=[{k:v for k,v in row.items() if not(k=='goal' and v==default_goal)} for row in context['outline']]
        context['literature_documents']=[]
        if context.get('section_contract'):
            context['section_contract']={k:v for k,v in context['section_contract'].items() if k!='evidence_ids'}

    def compact_gaps():
        grouped={}
        for item in knowledge['gaps']:
            reason=item.get('reason',str(item));record=grouped.setdefault(reason,{'reason':reason})
            if item.get('kind'):record.setdefault('kind',item['kind'])
            for field,plural in (('file_id','file_ids'),('reading_id','reading_ids')):
                if item.get(field):record.setdefault(plural,[]).append(item[field])
        context['evidence_gaps']=list(grouped.values())

    compact_gaps()
    def estimate():
        files={e['file_id'] for e in context.get('sources',[])}|{u['file_id'] for u in context.get('knowledge_units',[])}|{e['file_id'] for e in context.get('project_evidence',[]) if e.get('file_id')}
        context['references']=[r for r in reference_candidates if r['file_id'] in files]
        if context.get('knowledge_file_policies'):
            used={u['file_id'] for u in context.get('knowledge_units',[])}
            context['knowledge_file_policies']={fid:policy for fid,policy in policies.items() if fid in used}
        return token_upper_bound(system, db.dumps(context))

    def fits():
        return estimate() + output <= limit

    def gap(item):
        deferred.append(item)
        knowledge['gaps'].append(item)
        compact_gaps()

    if not fits() and context.get('knowledge_context'):
        context['knowledge_context'] = {}
        knowledge['knowledge_refs'] = []
        gap({'kind': 'derived_knowledge', 'reason': '派生知识导航超过本次上下文预算，已延后；原文证据单独检查'})

    project_candidates=list(context.get('project_evidence',[]))
    if not fits() and optional_project:
        first_code=next((e['id'] for e in project_candidates if e.get('origin_type')=='code'),None)
        context['project_evidence']=[e for e in project_candidates if e['id'] not in optional_project or e['id']==first_code]

    for reading in sorted(knowledge.get('document_readings',[]),key=lambda r:-sum(len(p.get('text','')) for p in r.get('passages',[]))):
        if fits():break
        rid=reading['reading_id']
        if rid not in automatic_readings:continue
        if any(u['unit_id'] in knowledge['selected_unit_ids'] for u in knowledge['knowledge_units'] if u.get('reading_id')==rid):continue
        knowledge['knowledge_units']=[u for u in knowledge['knowledge_units'] if u.get('reading_id')!=rid]
        context['knowledge_units']=[u for u in context['knowledge_units'] if u.get('reading_id')!=rid]
        knowledge['document_readings']=[r for r in knowledge['document_readings'] if r['reading_id']!=rid]
        context['document_readings']=[r for r in context.get('document_readings',[]) if r['reading_id']!=rid]
        bodies={u['file_id']:[v['text'] for v in knowledge['knowledge_units'] if v['file_id']==u['file_id']] for u in knowledge['knowledge_units']}
        retained_sources=[s for s in knowledge['sources'] if s['file_id']!=reading['file_id'] or any(s['text'].strip() in text for text in bodies.get(s['file_id'],[]))]
        source_ids={s['id'] for s in retained_sources};knowledge['sources']=retained_sources
        context['sources']=[s for s in context['sources'] if s['id'] in source_ids]
        knowledge['gaps']=[g for g in knowledge['gaps'] if g.get('reading_id')!=rid]
        gap({'kind':'automatic_reading_deferred','reading_id':rid,'file_id':reading['file_id'],
             'reason':'自动文献阅读范围整组超过剩余预算，已延后；本次未提供该组原文，不能据其推断结论'})

    # Automatic candidate documents are optional. Explicit selections and
    # question-bound readings remain mandatory, as does every saved chapter.
    candidates = list(dict.fromkeys([u['file_id'] for u in original_units] +
                                   [s['file_id'] for s in knowledge['sources']]))
    for fid in reversed(candidates):
        if fits():
            break
        if fid in required_files:
            continue
        omitted = [u for u in knowledge['knowledge_units'] if u['file_id'] == fid]
        knowledge['knowledge_units'] = [u for u in knowledge['knowledge_units'] if u['file_id'] != fid]
        knowledge['sources'] = [s for s in knowledge['sources'] if s['file_id'] != fid]
        context['knowledge_units'] = [u for u in context['knowledge_units'] if u['file_id'] != fid]
        context['sources'] = [s for s in context['sources'] if s['file_id'] != fid]
        if not any(e.get('file_id')==fid for e in context.get('project_evidence',[])):
            context['references'] = [r for r in context['references'] if r['file_id'] != fid]
        # Preparation notes such as "the whole document was provided" no
        # longer describe the outgoing request after this document is deferred.
        knowledge['gaps'] = [g for g in knowledge['gaps'] if g.get('file_id') != fid]
        gap({'kind': 'automatic_document_deferred', 'file_id': fid,
             'unit_ids': [u['unit_id'] for u in omitted],
             'characters': sum(len(u['text']) for u in omitted),
             'reason': '自动推荐文献整篇超过本次上下文剩余预算，已整篇延后且未截取；本次不能引用该文献，需另行阅读或扩大已确认的接口容量'})

    for example in list(reversed(context.get('writing_examples', []))):
        if fits():
            break
        if example.get('reading_id'):
            continue
        context['writing_examples'].remove(example)
        gap({'kind': 'automatic_guidance_deferred', 'file_id': example.get('file_id'),
             'reason': '自动写作示例超过本次上下文剩余预算，已整篇延后'})

    automatic=set(context.pop('automatic_project_evidence_ids',[]))
    selected_ids={e['id'] for e in context.get('project_evidence',[])}
    if automatic and (not fits() or selected_ids!={e['id'] for e in project_candidates}):
        candidates=project_candidates
        context['project_evidence']=[item for item in candidates if item['id'] not in automatic or item['id'] in selected_ids]
        for item in candidates:
            if item['id'] not in automatic:continue
            if item['id'] in {e['id'] for e in context['project_evidence']}:continue
            context['project_evidence'].append(item)
            if not fits():
                context['project_evidence'].remove(item)
                gap({'kind':'automatic_project_evidence_deferred','evidence_id':item['id'],
                     'reason':'自动项目观察整段超过剩余预算，已延后；未截取或声称读取该观察'})
    actual_files={e['file_id'] for e in context.get('sources',[])}|{u['file_id'] for u in context.get('knowledge_units',[])}|{e['file_id'] for e in context.get('project_evidence',[]) if e.get('file_id')}
    context['references']=[r for r in context.get('references',[]) if r['file_id'] in actual_files]

    sizes = {key: len(db.dumps(value).encode('utf-8')) for key, value in context.items()}
    report = {'method': 'utf8_bytes_upper_bound', 'context_limit': limit,
              'reserved_output': output, 'input_upper_bound': estimate(),
              'component_bytes': sizes, 'deferred_documents': deferred,
              'manuscript_complete': context.get('paper_context', {}).get('complete', False),
              'loaded_unit_ids': [u['unit_id'] for u in knowledge['knowledge_units']],
              'loaded_source_ids': [s['id'] for s in knowledge['sources']]}
    if not fits():
        labels = {'rules': '适用规则', 'knowledge_units': '已选定或已阅读的证据',
                  'related_sections': '其他章节正文', 'current_section_context': '当前章完整正文',
                  'existing_blocks': '当前操作段落', 'writing_examples': '已选定的指导资料',
                  'paper_draft_sections': '整篇任务已完成的候选正文'}
        largest = sorted(sizes, key=sizes.get, reverse=True)[:3]
        parts = '、'.join(f'{labels.get(k, k)} {sizes[k]:,} 字节' for k in largest)
        message = (f'写作请求所需输入保守上界 {report["input_upper_bound"]:,}，预留输出 {output:,}，'
                   f'超过当前接口容量 {limit:,}；主要占用：{parts}。'
                   '已保留全文、全部适用规则和明确选定的证据；请调整已选阅读范围或已确认的接口容量。')
        raise HTTPException(422, {'code': 'WRITING_CONTEXT_BUDGET_EXCEEDED',
                                  'message': message, 'context_budget': report})
    return report
