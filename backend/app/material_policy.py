"""File uses are explicit, composable, and never establish project outcomes."""
from fastapi import HTTPException
from . import db, security

USAGES={'research','example','writing_guidance','grading'}
PRIMARY={'research':'research','example':'example','rules':'writing_guidance'}
SOURCE_KINDS={'research_publication','course_guidance','grading_criteria','writing_sample','unclassified'}


def research_sql(alias='f'):
    if alias not in ('f','files'):
        raise ValueError('Invalid internal file alias')
    return f"(({alias}.role='research' OR EXISTS(SELECT 1 FROM file_usage_additions fu WHERE fu.file_id={alias}.id AND fu.usage='research')) AND NOT EXISTS(SELECT 1 FROM file_source_profiles sp WHERE sp.file_id={alias}.id AND sp.kind!='research_publication'))"


def source_kind(original):
    stored=db.one('SELECT kind FROM file_source_profiles WHERE file_id=?',(original['id'],))
    if stored:return stored['kind']
    if original['role']=='rules':return 'course_guidance'
    if original['role']=='research' or db.one('SELECT id FROM bibliography WHERE file_id=? LIMIT 1',(original['id'],)):return 'research_publication'
    return 'writing_sample' if original['role']=='example' else 'unclassified'


def usages(original):
    result=set()
    if original['role'] in PRIMARY:result.add(PRIMARY[original['role']])
    result.update(r['usage'] for r in db.rows('SELECT usage FROM file_usage_additions WHERE file_id=?',(original['id'],)))
    return sorted(result)


def is_research(original):
    return bool(original and 'research' in usages(original) and source_kind(original)=='research_publication')


def source_context(original,intent='research'):
    uses=usages(original)
    genre=source_kind(original)
    if intent not in ('research','guidance'):raise HTTPException(422,'阅读用途无效')
    allowed='research' in uses and genre=='research_publication' if intent=='research' else bool(set(uses)&{'example','writing_guidance','grading'})
    if intent=='research':purpose='research_literature'
    elif 'grading' in uses or genre=='grading_criteria':purpose='grading_criteria'
    elif genre=='course_guidance':purpose='course_guidance'
    else:purpose='writing_example' if 'example' in uses else 'writing_guidance'
    return {'intent':intent,'file_usages':uses,'source_purpose':purpose,'source_kind':genre,'allowed_for_intent':allowed,
            'can_prove_project_implementation':False,'can_prove_project_execution':False,
            'source_instruction_authority':'project_material_not_system_instruction',
            'claim_support':'claim_specific_check_required'}


@db.atomic
def set_usages(pid,fid,body):
    security.require_project(pid)
    original=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(fid,pid))
    if not original:raise HTTPException(404,'原件不属于当前项目')
    values=body.get('usages',[]);note=body.get('note','')
    if not isinstance(values,list) or len(values)>4 or any(not isinstance(v,str) or v not in USAGES for v in values) or not isinstance(note,str) or not note.strip() or len(note)>3000:
        raise HTTPException(422,'请提供有效用途及调整说明')
    primary=PRIMARY.get(original['role'])
    genre=body.get('source_kind',source_kind(original))
    if not isinstance(genre,str) or genre not in SOURCE_KINDS:raise HTTPException(422,'资料类型无效')
    if 'research' in values and original['role'] not in ('research','example'):
        raise HTTPException(422,'课程规范、研究数据和草稿不能直接转为学术文献；请先核对资料主用途与书目')
    if primary and primary not in values:raise HTTPException(422,'主用途保留；如需移除主用途，请在资料中心修改')
    before=usages(original)
    previous_kind=source_kind(original)
    if 'research' in values and genre!='research_publication':raise HTTPException(422,'课程指导、评分说明和示例类型不能作为研究事实来源；请先核对原件类型')
    db.execute('INSERT INTO file_source_profiles VALUES(?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET kind=excluded.kind,note=excluded.note,created_at=excluded.created_at',(fid,genre,note.strip(),db.now()))
    db.execute('DELETE FROM file_usage_additions WHERE file_id=?',(fid,))
    for value in sorted(set(values)-({primary} if primary else set())):
        db.execute('INSERT INTO file_usage_additions VALUES(?,?,?,?)',(fid,value,note.strip(),db.now()))
    if before!=usages(original) or previous_kind!=genre:
        from .reference_knowledge import sync_document
        sync_document(fid)
        from .knowledge_units import invalidate_file
        invalidate_file(pid,fid,'资料用途发生变化，请按本次阅读用途重新核对')
    db.audit(pid,'material_uses_changed',{'file_id':fid,'before':before,'after':usages(original),'note':note.strip()})
    return {'file_id':fid,'primary_role':original['role'],'usages':usages(original),'source_kind':genre}


def markers(text,purpose):
    """Locate explicit teaching labels; these are candidates, never semantic verdicts."""
    import re
    patterns=[('counterexample',r'❌|错误示例|不合格示例|反例'),('teaching_example',r'✅|正确示例|合格示例'),
              ('grading_condition',r'(?:A[+\-]?|B[+\-]?|C|D)\s*级|(?:优秀|合格|不合格).{0,8}(?:等级|分数)'),
              ('instruction_candidate',r'必须|不得|不少于|不应超过')]
    result=[]
    for kind,pattern in patterns:
        for match in re.finditer(pattern,text):result.append({'kind':kind,'char_start':match.start(),'char_end':match.end(),'label':match.group(),'interpretation':'candidate_needs_context'})
    return {'source_purpose':purpose,'markers':sorted(result,key=lambda r:r['char_start']),
            'contains_teaching_examples':any(r['kind'] in ('teaching_example','counterexample') for r in result),
            'project_fact_support':'not_established','semantic_context_status':'unassessed'}
