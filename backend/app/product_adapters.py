"""Existing planning, review and delivery outputs in the shared product contract."""
from fastapi import HTTPException
from . import db, security, documents

EXTRA_TABLES = {'rule':'rules', 'rule_package':'rule_packages', 'outline':'sections',
                'finding':'findings', 'mentor_task':'mentor_tasks', 'reading':'document_readings',
                'export':'exports', 'execution':'tasks'}


def owned(pid, kind, identity):
    security.require_project(pid)
    row=db.one('SELECT * FROM '+EXTRA_TABLES[kind]+' WHERE id=? AND project_id=?',(identity,pid))
    if not row:raise HTTPException(404,'产物不存在或不属于当前项目')
    return row


def origins(pid, kind, row):
    if kind=='rule':return [('source',row['source_id'])] if row['source_id'] else []
    if kind=='rule_package':return [('source',rule['source_id']) for rule in db.loads(row['rules'],[]) if rule.get('source_id')]
    if kind=='finding':return [('revision',row['revision_id'])] if row['revision_id'] else []
    if kind=='reading':return [('file',row['file_id'])]
    if kind=='mentor_task':return [('note',item['id']) for item in db.rows("SELECT id FROM studio_records WHERE project_id=? AND kind='note' AND deleted_at IS NULL AND json_extract(data,'$.mentor_task_id')=?",(pid,row['id']))]
    if kind=='execution':
        data=db.loads(row['payload'],{})
        values=[('file',fid) for fid in data.get('file_ids',[])]
        if data.get('file_id'):values.append(('file',data['file_id']))
        if data.get('message_id'):values.append(('message',data['message_id']))
        values.extend((ref['type'],ref['id']) for ref in data.get('source_refs',[]))
        values.extend((ref['type'],ref['id']) for ref in data.get('product_refs',[]))
        result=db.loads(row['result'],{})
        values.extend((ref['type'],ref['id']) for ref in result.get('source_refs',[]))
        return values
    if kind=='export':
        manifest=db.loads(row['manifest'],{})
        return [('revision',item['revision_id']) for item in manifest.get('sections',[]) if isinstance(item,dict) and item.get('revision_id')]
    return []


def version(pid, kind, identity, remote=False, seen=None):
    from . import resource_refs as refs
    row=owned(pid,kind,identity)
    if kind=='outline':
        if row['state']=='archived':raise HTTPException(409,'章节计划已归档')
        return refs.digest({key:row[key] for key in ('id','title','parent_id','kind','position','target_words','goal','dependencies')})
    if kind=='rule' and row['state']=='rejected':raise HTTPException(409,'规则已排除')
    if kind=='finding':
        if row['status']=='superseded':raise HTTPException(409,'检查意见已由新检查替代')
        if row['section_id']:
            section=security.require_section(row['section_id'])
            if row['revision_id'] and section['current_revision_id']!=row['revision_id']:raise HTTPException(409,'检查意见对应的正文已更新')
    if kind=='execution':
        payload=db.loads(row['payload'],{})
        if row['status']!='succeeded' or not (row['kind'] in ('advanced_execute','advanced_screenshot') or row['kind']=='studio' and payload.get('action')=='execute'):
            raise HTTPException(409,'仅支持复用已成功执行的代码或截图结果')
        refs.validate(pid,db.loads(row['result'],{}).get('source_refs',[]),remote)
        refs.validate(pid,payload.get('product_refs',[]),remote)
    if kind=='export' and row['status']!='ready':raise HTTPException(409,'导出产物尚未就绪')
    if kind=='reading':
        from .document_reading import context
        value=context(pid,identity,remote)
        if not value['current']:raise HTTPException(409,'问题阅读的来源或任务范围已更新')
        return value['version']
    upstream=[[typ,uid,refs.version_of(pid,typ,uid,remote,seen)] for typ,uid in origins(pid,kind,row)]
    return refs.digest([row,upstream])


def text(pid, kind, identity):
    row=owned(pid,kind,identity)
    if kind=='rule':return '论文规则 · '+row['statement'][:60], '\n'.join([row['statement'],'要求级别：'+row['modality'],'适用范围：'+row['scope'],'确认状态：'+row['state']])
    if kind=='rule_package':
        return '已发布规则包 v'+str(row['version']), '\n'.join(rule.get('statement','') for rule in db.loads(row['rules'],[]))
    if kind=='outline':return row['title']+' · 章节计划', '\n'.join([row['title'],row['goal'],'目标字数：'+str(row['target_words']),'前置章节：'+row['dependencies']])
    if kind=='finding':return '检查意见 · '+row['message'][:60], '\n'.join([row['message'],'检查结果：'+row['result'],'方法：'+row['method'],'处理状态：'+row['status'],'检查依据：'+row['evidence']])
    if kind=='mentor_task':return '导师意见 · '+row['comment'][:60], '\n'.join([row['comment'],'处理状态：'+row['status'],'处理记录：'+row['resolution']])
    if kind=='execution':return '执行结果 · '+row['kind'], db.dumps(db.loads(row['result'],{}))
    if kind=='reading':
        from .document_reading import context
        value=context(pid,identity)
        lines=[row['question'],'阅读状态：'+row['state'],row['review_note']]
        lines.extend(db.dumps(item) for item in value.get('findings',[]))
        lines.extend(item['text'] for item in value.get('passages',[]))
        return '问题阅读 · '+row['question'][:60], '\n'.join(lines)
    from .export_service import describe
    value=describe(row)
    return '交付记录 · '+row['filename'], db.dumps({'format':value['format'],'mode':value['mode'],'snapshot_id':value['snapshot_id'],'checks':value['checks'],'manifest':db.loads(row['manifest'],{}),'artifacts':value['artifacts']})


def dependents(pid, kind, identity):
    result=[]
    if kind=='source':
        result.extend(('rule',r['id']) for r in db.rows('SELECT id FROM rules WHERE project_id=? AND source_id=?',(pid,identity)))
    if kind=='file':
        result.extend(('reading',r['id']) for r in db.rows('SELECT id FROM document_readings WHERE project_id=? AND file_id=?',(pid,identity)))
    if kind=='revision':
        result.extend(('finding',r['id']) for r in db.rows('SELECT id FROM findings WHERE project_id=? AND revision_id=?',(pid,identity)))
    return result
