"""Fit optional whole documents around the immutable manuscript and selected evidence."""
from copy import deepcopy
import hashlib
import json
from fastapi import HTTPException
from . import db
from .source_payload import compact_locator, token_upper_bound

FORMAT_REPAIR_INPUT_RESERVE = 2048
WIRE_SCHEMA = 'writing.wire.exact_text.v1'
WIRE_FIELD = '_writing_wire_compaction'
_TEXT_REF = '_writing_text_ref'
_JSON_VALUE = '_writing_serialized_json'


def _pointer(path):
    return '/' + '/'.join(str(part).replace('~','~0').replace('/','~1') for part in path)


def _pointer_parts(value):
    if not isinstance(value,str) or not value.startswith('/'):
        raise ValueError('Invalid writing text reference')
    return [part.replace('~1','/').replace('~0','~') for part in value[1:].split('/')]


def compact_model_context(context):
    """Return a lossless wire view; local evidence/quality inputs stay unchanged.

    References mean equal text values, not common source identity or semantic
    support. Their offsets address an already supplied wire string, never an
    inferred original-document interval. Full manuscript and rule fields are
    never transformed, nor are the canonical top-level unit texts.
    """
    if WIRE_FIELD in context:
        # Do not overwrite caller data or compact an already encoded view twice.
        return deepcopy(context)
    view=deepcopy(context)
    references=[];json_values=[];texts=[]
    targets={'knowledge_context','knowledge_units','document_readings',
             'writing_examples','project_evidence','research_products'}
    def register(path,text):
        if isinstance(text,str) and len(text.encode('utf-8'))>=256:
            texts.append((tuple(path),text))
    # Prefer immutable, actually supplied originals over copies in directories.
    unit_text_paths=set()
    for index,unit in enumerate(context.get('knowledge_units',[])):
        path=('knowledge_units',index,'text');unit_text_paths.add(path)
        register(path,unit.get('text'))

    def walk(value,path,key=None):
        if isinstance(value,str):
            if path in unit_text_paths:
                return value
            # These database fields contain JSON strings, often a second copy
            # of an assertion quote. Only accept the exact canonical encoding;
            # arbitrary source prose and noncanonical JSON remain literal.
            if key and key.endswith('_json') and len(value.encode('utf-8'))>=256:
                try:decoded=json.loads(value)
                except (ValueError,TypeError):decoded=None
                if isinstance(decoded,(dict,list)) and db.dumps(decoded)==value:
                    nested=walk(decoded,(*path,_JSON_VALUE,'value'))
                    encoded={_JSON_VALUE:{'value':nested,'sha256':hashlib.sha256(value.encode()).hexdigest()}}
                    if len(db.dumps(encoded).encode())<len(db.dumps(value).encode()):
                        json_values.append(_pointer(path))
                        return encoded
                    # Remove references to a wrapper that will not be emitted.
                    prefix=_pointer((*path,_JSON_VALUE))+'/'
                    references[:]=[item for item in references if not item.startswith(prefix)]
                    json_values[:]=[item for item in json_values if not item.startswith(prefix)]
                    texts[:]=[(target,text) for target,text in texts if target[:len(path)]!=path]
            if len(value.encode('utf-8'))>=256:
                for target,text in texts:
                    start=text.find(value)
                    if start<0:continue
                    ref={_TEXT_REF:{'path':_pointer(target),'value_start_cp':start,
                         'value_end_cp':start+len(value),
                         'sha256':hashlib.sha256(value.encode()).hexdigest()}}
                    if len(db.dumps(ref).encode())<len(db.dumps(value).encode()):
                        references.append(_pointer(path));return ref
                register(path,value)
            return value
        if isinstance(value,list):
            return [walk(item,(*path,index)) for index,item in enumerate(value)]
        if isinstance(value,dict):
            return {name:walk(item,(*path,name),name) for name,item in value.items()}
        return value

    for key,value in context.items():
        if key in targets:view[key]=walk(value,(key,),key)
    if references or json_values:
        view[WIRE_FIELD]={'schema':WIRE_SCHEMA,'text_references':references,
            'json_strings':json_values,
            'meaning':'仅压缩逐字相同的文本副本。_writing_text_ref.path是本请求JSON路径，'
                'value_start_cp/value_end_cp是该已提供字符串的Unicode半开区间；'
                '不是原文新范围，不拼接未读间隔。_writing_serialized_json.value还原后按紧凑JSON编码恢复原字符串。'
                '沿原记录的来源ID、范围、用途、限制、否定与支持状态核对，文本相同不代表来源相同或获得支持。'}
        if len(db.dumps(view).encode('utf-8'))>=len(db.dumps(context).encode('utf-8')):
            return deepcopy(context)
    return view


def expand_model_context(view):
    """Verify and restore an encoded view, also used by losslessness tests."""
    meta=view.get(WIRE_FIELD,{})
    if not isinstance(meta,dict) or meta.get('schema')!=WIRE_SCHEMA:return deepcopy(view)
    references=set(meta.get('text_references',[]));json_values=set(meta.get('json_strings',[]))
    active=set();memo={}
    def lookup(pointer):
        value=view
        for part in _pointer_parts(pointer):value=value[int(part)] if isinstance(value,list) else value[part]
        return value
    def restore(value,path):
        pointer=_pointer(path)
        if pointer in memo:return deepcopy(memo[pointer])
        if pointer in active:raise ValueError('Cyclic writing text reference')
        active.add(pointer)
        try:
            if pointer in references:
                record=value[_TEXT_REF];target=record['path']
                text=restore(lookup(target),tuple(_pointer_parts(target)))
                if not isinstance(text,str):raise ValueError('Writing text reference is not a string')
                start,end=record['value_start_cp'],record['value_end_cp']
                if type(start)is not int or type(end)is not int or not 0<=start<=end<=len(text):
                    raise ValueError('Writing text reference range is invalid')
                result=text[start:end]
                if hashlib.sha256(result.encode()).hexdigest()!=record['sha256']:
                    raise ValueError('Writing text reference hash does not match')
            elif pointer in json_values:
                record=value[_JSON_VALUE]
                result=db.dumps(restore(record['value'],(*path,_JSON_VALUE,'value')))
                if hashlib.sha256(result.encode()).hexdigest()!=record['sha256']:
                    raise ValueError('Writing JSON string hash does not match')
            elif isinstance(value,list):result=[restore(item,(*path,index)) for index,item in enumerate(value)]
            elif isinstance(value,dict):result={key:restore(item,(*path,key)) for key,item in value.items()}
            else:result=value
            memo[pointer]=result
            return deepcopy(result)
        finally:active.remove(pointer)
    return {key:restore(value,(key,)) for key,value in view.items() if key!=WIRE_FIELD}


def pack(context, knowledge, system, cfg):
    limit = int(cfg.get('context_limit', 100000))
    output = int(cfg.get('max_output_tokens', 8192))
    followup_reserve = max(0, int(cfg.get('reserved_followup_input', 0)))
    packing_limit = limit - followup_reserve
    def model_wire():
        if context.get('chapter_materials'):
            from .chapter_materials import available as available_structures
            context['chapter_materials']=available_structures(context['chapter_materials'],
                [row['id'] for row in context.get('project_evidence',[])])
        if context.get('project_grounding'):
            from .project_grounding import available
            context['project_grounding']=available(context['project_grounding'],
                [row['id'] for row in context.get('project_evidence',[])])
        from .prompt_layout import prepare as prepare_layout
        return prepare_layout({'payload':{'prompt_layout_version':cfg.get('prompt_layout_version')}},
                              system,context,'draft')[:2]
    def wire_bound():
        wire_system,wire=model_wire()
        return token_upper_bound(wire_system,db.dumps(wire))
    deferred = []
    reference_candidates=list(context.get('references',[]))
    original_units = list(knowledge['knowledge_units'])
    material_catalogue=deepcopy(context.get('evidence_bundle'))
    explicit_products=context.pop('required_research_product_refs',None)
    # Callers predating explicit provenance markers keep their prior mandatory
    # treatment. New writers distinguish author-bound inputs from search hits.
    required_products={(ref['type'],ref['id']) for ref in explicit_products} if explicit_products is not None else {
        (row['source_ref']['type'],row['source_ref']['id']) for row in context.get('research_products',[]) if row.get('source_ref')}
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

    # Reading receipts describe the exact ranges already supplied in units.
    # Keep locations and versions, and send their identical text only once.
    compact_readings=[]
    for reading in context.get('document_readings',[]):
        compacted=deepcopy(reading)
        for passage in compacted.get('passages',[]):
            text=passage.get('text');uid=passage.get('unit_id') or passage.get('id')
            covering=[unit['unit_id'] for unit in context.get('knowledge_units',[])
                      if unit['unit_id']==uid and isinstance(text,str) and text.strip()
                      and text in unit.get('text','')]
            if covering:
                passage.pop('text')
                passage['text_in_knowledge_units']=covering
        compact_readings.append(compacted)
    if compact_readings:context['document_readings']=compact_readings

    # Keep research text and mandatory rules. Under pressure remove repeated
    # navigation/state fields whose exact versions remain in the manifest.
    if wire_bound()+output>packing_limit:
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
        if context.get('paper_plan'):
            # The current task already has its exact writing_brief and argument
            # cards. Other chapters need goals/dependencies, not a second copy
            # of every generic paragraph template. No prose or evidence is cut.
            context['paper_plan']=[{k:v for k,v in row.items() if k!='paragraph_plan'} for row in context['paper_plan']]

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
        if material_catalogue is not None:
            # Keep the navigation catalogue aligned as whole optional readings
            # are deferred. The sealed bundle is validated and restricted once
            # after packing; estimating must not reread every source each time.
            source_ids={r['id'] for r in context.get('sources',[])}
            unit_ids={r.get('unit_id') or r.get('id') for r in context.get('knowledge_units',[])}
            evidence_ids={r['id'] for r in context.get('project_evidence',[])}
            products={(r['source_ref']['type'],r['source_ref']['id']) for r in context.get('research_products',[]) if r.get('source_ref')}
            guidance_files={r.get('file_id') for r in context.get('writing_examples',[])}
            view={**material_catalogue};dropped=0
            for group in ('primary_evidence','derived_context','guidance'):
                kept=[]
                for entry in material_catalogue.get(group,[]):
                    ref=entry.get('source_ref',{});key=(ref.get('type'),ref.get('id'))
                    available=key in products
                    if group=='guidance':available=available or entry.get('file_id') in guidance_files
                    else:available=available or ref.get('type')=='source' and ref.get('id') in source_ids or ref.get('type')=='unit' and ref.get('id') in unit_ids or ref.get('type')=='evidence' and ref.get('id') in evidence_ids
                    if available:kept.append(entry)
                    else:dropped+=1
                view[group]=kept
            if dropped:
                view['complete']=False
                view['coverage']={key:value for key,value in material_catalogue.get('coverage',{}).items() if key!='characters'}
                view['coverage']['resolved_items']=sum(len(view.get(group,[])) for group in ('constraints','primary_evidence','guidance','derived_context'))
                view['gaps']=[*material_catalogue.get('gaps',[]),{'reason':'请求打包后原文未提供，不允许用于本次正文','deferred_items':dropped}]
            context['evidence_bundle']=view
        return wire_bound()

    def fits():
        return estimate() + output <= packing_limit

    def gap(item):
        deferred.append(item)
        knowledge['gaps'].append(item)
        compact_gaps()

    def required_guidance(example):
        ref=example.get('source_ref',{})
        return bool(example.get('reading_id') or (ref.get('type'),ref.get('id')) in required_products
                    or ('file',example.get('file_id')) in required_products)

    if not fits() and context.get('knowledge_context'):
        context['knowledge_context'] = {}
        knowledge['knowledge_refs'] = []
        gap({'kind': 'derived_knowledge', 'reason': '派生知识导航超过本次上下文预算，已延后；原文证据单独检查'})

    # Search-discovered summaries and examples are secondary to the research
    # originals. Bound author material and explicit guidance readings stay whole.
    for product in reversed(list(context.get('research_products',[]))):
        if fits():break
        ref=product.get('source_ref',{})
        if (ref.get('type'),ref.get('id')) in required_products:continue
        context['research_products'].remove(product)
        gap({'kind':'automatic_product_deferred','source_ref':ref,
             'reason':'自动推荐的派生产物超过本次预算，已整项延后，优先保留研究原文；本次不能使用该产物'})
    for example in reversed(list(context.get('writing_examples',[]))):
        if fits():break
        if required_guidance(example):continue
        context['writing_examples'].remove(example)
        gap({'kind':'automatic_guidance_deferred','file_id':example.get('file_id'),
             'reason':'自动写作示例超过本次上下文剩余预算，已整篇延后'})

    project_candidates=list(context.get('project_evidence',[]))
    literature_chapter=context.get('section',{}).get('kind') in ('literature','literature_review')
    first_code=None if literature_chapter else next((e['id'] for e in project_candidates if e.get('origin_type')=='code'),None)
    if not fits() and optional_project:
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
        if required_guidance(example):
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
    # Deferral receipts themselves consume input. A final rejected optional
    # observation can add enough gap metadata to cross a previously fitting
    # boundary, so reclaim optional whole items after all receipts exist.
    for item in reversed(list(context.get('project_evidence',[]))):
        if fits():break
        if item['id'] not in automatic or item['id']==first_code:continue
        context['project_evidence'].remove(item)
        gap({'kind':'automatic_project_evidence_deferred','evidence_id':item['id'],
             'reason':'自动项目观察整段超过剩余预算，已延后；未截取或声称读取该观察'})
    for example in reversed(list(context.get('writing_examples',[]))):
        if fits():break
        if required_guidance(example):continue
        context['writing_examples'].remove(example)
        gap({'kind':'automatic_guidance_deferred','file_id':example.get('file_id'),
             'reason':'自动写作示例超过本次上下文剩余预算，已整篇延后'})
    actual_files={e['file_id'] for e in context.get('sources',[])}|{u['file_id'] for u in context.get('knowledge_units',[])}|{e['file_id'] for e in context.get('project_evidence',[]) if e.get('file_id')}
    context['references']=[r for r in context.get('references',[]) if r['file_id'] in actual_files]

    _,wire=model_wire()
    sizes = {key: len(db.dumps(value).encode('utf-8')) for key, value in wire.items()}
    canonical_sizes={key:len(db.dumps(value).encode('utf-8')) for key,value in context.items()}
    report = {'method': 'utf8_bytes_upper_bound', 'context_limit': limit,
              'reserved_output': output, 'input_upper_bound': estimate(),
              'reserved_followup_input': followup_reserve, 'packing_context_limit': packing_limit,
              'component_bytes': sizes, 'deferred_documents': deferred,
              'canonical_component_bytes':canonical_sizes,
              'wire_compaction':{'schema':WIRE_SCHEMA,
                  'text_references':len(wire.get(WIRE_FIELD,{}).get('text_references',[])),
                  'json_strings':len(wire.get(WIRE_FIELD,{}).get('json_strings',[])),
                  'saved_bytes':len(db.dumps(context).encode())-len(db.dumps(wire).encode())},
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
                   + (f'后续格式修复预留输入 {followup_reserve:,}，' if followup_reserve else '') +
                   f'超过当前接口容量 {limit:,}；主要占用：{parts}。'
                   '已保留全文、全部适用规则和明确选定的证据；请调整已选阅读范围或已确认的接口容量。')
        raise HTTPException(422, {'code': 'WRITING_CONTEXT_BUDGET_EXCEEDED',
                                  'message': message, 'context_budget': report})
    return report
