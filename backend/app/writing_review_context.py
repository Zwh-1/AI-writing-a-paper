"""Restore the exact source registries of an unadopted writing candidate."""
from copy import deepcopy
from fastapi import HTTPException
from . import db
from .writing_evidence_quality import _source_records, merge_unit_readings
from .writing_agent.common import digest,unblob


def restore(pid, manifest):
    from .writing_agent import evidence
    from .document_reading import context as read_context
    from .knowledge_units import get as unit_get
    context = {'sources': [], 'knowledge_units': [], 'document_readings': [],
               'project_evidence': [], 'facts': []}
    for sid in manifest.get('source_ids', []):
        row = db.one('SELECT s.* FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?', (sid, pid))
        if not row: raise HTTPException(409, '候选文献来源已变化')
        scope=manifest.get('source_read_scopes',{}).get(sid)
        if scope:
            text=unblob(scope['text_blob'])
            if not isinstance(text,str) or digest(text)!=scope['text_hash']:
                raise HTTPException(409,'候选来源阅读快照校验失败')
            row={**row,'text':text}
        context['sources'].append(row)
    for uid, version in manifest.get('knowledge_unit_versions', {}).items():
        unit = unit_get(pid, uid, remote=True)
        scope = manifest.get('knowledge_read_scopes', {}).get(uid) or {}
        ranges = [item for value in manifest.get('knowledge_read_scopes',{}).values()
                  for item in value.get('reading_ranges',[]) if (item.get('unit_id') or uid)==uid]
        if not ranges:
            # A grouping handle may carry only its children's explicit ranges.
            # Check its version, but do not invent a full-text read for it.
            if unit['content_hash'] != version: raise HTTPException(409,'候选知识单元版本已变化')
            if scope.get('reading_ranges'): continue
            raise HTTPException(409, '候选知识单元缺少实际阅读范围，需重新取材')
        observed = None
        for item in ranges:
            original = unit_get(pid, item.get('unit_id') or uid, remote=True)
            start, end = item.get('char_start'), item.get('char_end')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(original['text']):
                raise HTTPException(409, '候选知识单元阅读范围已失效')
            part = {'unit_id': original['id'], 'file_id': original['file_id'], 'parse_id': original['parse_id'],
                    'unit_hash': original['content_hash'], 'text': original['text'][start:end],
                    'char_start': start, 'char_end': end, 'reading_id': item.get('reading_id')}
            # Keep disjoint observations separate; do not read their gaps.
            if original['id'] != uid:
                context['knowledge_units'].append(part)
            else: observed = part if observed is None else merge_unit_readings(observed, part)
        if observed: context['knowledge_units'].append(observed)
        if unit['content_hash'] != version: raise HTTPException(409, '候选知识单元版本已变化')
    for rid, version in manifest.get('document_reading_versions', {}).items():
        reading = read_context(pid, rid, remote=True)
        if reading.get('intent') != 'research': continue
        if reading['version'] != version or not reading['current']:
            raise HTTPException(409, '候选文献阅读版本已变化')
        context['document_readings'].append({**reading, 'reading_id': rid})
    for eid, version in manifest.get('evidence_versions', {}).items():
        row = evidence.get(pid, eid, remote=True)
        if row['origin_version'] != version: raise HTTPException(409, '候选项目证据版本已变化')
        context['project_evidence'].append(row)
    for fid in {**manifest.get('fact_revisions', {}), **manifest.get('fact_versions', {})}:
        row = db.one('SELECT * FROM facts WHERE id=? AND project_id=?', (fid, pid))
        if not row: raise HTTPException(409, '候选研究事实已变化')
        context['facts'].append(row)
    links = {'source_ids': [row['id'] for row in context['sources']],
             'knowledge_unit_ids': [row['unit_id'] for row in context['knowledge_units']],
             'document_reading_ids': [row['reading_id'] for row in context['document_readings']],
             'evidence_ids': [row['id'] for row in context['project_evidence']],
             'fact_ids': [row['id'] for row in context['facts']]}
    try: records = _source_records(pid, links, context, manifest)
    except ValueError as error: raise HTTPException(409, str(error)) from error
    originals = []
    for record in records:
        if record['type'] == 'evidence': originals.append(evidence.for_model(record['evidence']))
        else:
            originals.append({'id': record['id'], 'kind': 'literature' if record['type'] != 'fact' else 'fact_record',
                              'conditions': [], 'payload': {'text': record['text'], 'version': record['version'],
                                                          'locator': record['locator'], 'registry': record['type'],
                                                          'range_start':record.get('range_start'),'range_end':record.get('range_end')}})
    grouped={}
    for original in originals:
        key=original['id']
        observation={name:deepcopy(original['payload'].get(name)) for name in ('version','locator','registry','range_start','range_end')}
        observation['text_hash']=digest(original['payload'].get('text',''))
        if key not in grouped:
            grouped[key]=deepcopy(original)
            grouped[key]['payload']['read_observations']=[observation]
        else:
            target=grouped[key]['payload'];part=deepcopy(original['payload'])
            if observation not in target['read_observations']:
                target['read_observations'].append(observation)
                target['text'] += '\n\n[另一实际读取范围；中间未读内容不作为证据]\n\n'+part.get('text','')
                target['scope']='disjoint_read_observations'
    return list(grouped.values()), context
