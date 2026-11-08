"""Project-selected standards and explicit, source-bearing conflict decisions."""
from copy import deepcopy
from fastapi import APIRouter, HTTPException
from . import db, security
from .bibliography_format import render, VERSIONS

router=APIRouter()
DEFAULT={'bibliography_version':'legacy','thesis_version':'gbt7713_2025',
         'institution':'','requirements':[],'conflicts':[]}


def resolve(value=None):
    result={**deepcopy(DEFAULT),**(value or {})}
    from .ai_request_cache import digest
    return {**result,'version_hash':digest(result)}


def profile(pid):
    security.require_project(pid)
    return resolve(db.setting('academic_standards:'+pid,{}))


def validate(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise HTTPException(422,'规范配置字段无效')
    result={**deepcopy(DEFAULT),**value}
    if result['bibliography_version'] not in VERSIONS or result['thesis_version'] not in ('gbt7713_2006','gbt7713_2025'):
        raise HTTPException(422,'规范版本无效')
    if not isinstance(result['institution'],str) or len(result['institution'])>300:raise HTTPException(422,'学校名称无效')
    for key in ('requirements','conflicts'):
        if not isinstance(result[key],list) or len(result[key])>100:raise HTTPException(422,'规范条目数量无效')
        seen=set()
        for row in result[key]:
            allowed={'id','statement','source','clause','modality','scope'} if key=='requirements' else {'id','statement','sources','decision','reason'}
            if not isinstance(row,dict) or set(row)-allowed:raise HTTPException(422,'规范条目字段无效')
            identity=row.get('id')
            if not isinstance(identity,str) or not 1<=len(identity)<=100 or identity in seen:raise HTTPException(422,'规范条目标识无效或重复')
            seen.add(identity)
            if not isinstance(row.get('statement'),str) or not 1<=len(row['statement'])<=4000:raise HTTPException(422,'规范内容无效')
            if key=='requirements':
                if not isinstance(row.get('source'),str) or not row['source'].strip():raise HTTPException(422,'规范须保留正式来源')
                if row.get('modality') not in ('mandatory','recommended'):raise HTTPException(422,'规范性质无效')
            else:
                if not isinstance(row.get('sources'),list) or len(row['sources'])<2 or any(not isinstance(x,str) or not x.strip() for x in row['sources']):raise HTTPException(422,'冲突须保留至少两个来源')
                if row.get('decision') and (not isinstance(row.get('reason'),str) or not row['reason'].strip()):raise HTTPException(422,'冲突处理须填写理由')
            for name,item in row.items():
                if name!='sources' and (not isinstance(item,str) or len(item)>6000):raise HTTPException(422,'规范条目类型或长度无效')
    return result


@router.get('/projects/{pid}/academic-standards')
def get_profile(pid):
    return {'profile':profile(pid),'bibliography_versions':list(VERSIONS),
            'standards':[{'id':'gbt7713_2025','effective_from':'2026-02-01'},
                         {'id':'gbt7714_2025','effective_from':'2026-07-01'}]}


@router.put('/projects/{pid}/academic-standards')
@db.atomic
def put_profile(pid,body:dict):
    security.require_project(pid)
    values=validate(body.get('profile',body))
    db.set_setting('academic_standards:'+pid,values)
    db.audit(pid,'academic_standards_saved',values)
    return get_profile(pid)


@router.post('/projects/{pid}/bibliography/preview')
def preview(pid,body:dict):
    config=profile(pid)
    try:return render(body.get('metadata',{}),body.get('version',config['bibliography_version']))
    except ValueError as error:raise HTTPException(422,str(error)) from error


def report(references,config):
    rows=[]
    for ref in references:
        try:
            formatted=render(db.loads(ref['metadata'],{}) if isinstance(ref['metadata'],str) else ref['metadata'],config['bibliography_version'])
            rows.append({'reference_id':ref['id'],**formatted})
        except ValueError as error:
            rows.append({'reference_id':ref['id'],'text':'','format_status':'needs_correction',
                         'issues':[{'field':'metadata','severity':'error','message':str(error)}]})
    return {'profile':config,'references':rows,'unresolved_conflicts':[x for x in config['conflicts'] if not x.get('decision')],
            'errors':sum(x['severity']=='error' for row in rows for x in row['issues']),
            'source_authenticity':'not_assessed'}


@router.get('/projects/{pid}/bibliography/validation')
def validation(pid):
    config=profile(pid)
    return report(db.rows('SELECT * FROM bibliography WHERE project_id=?',(pid,)),config)


def check_docx(path,config):
    """Check generated properties without claiming visual pagination passed."""
    from zipfile import ZipFile
    from lxml import etree
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with ZipFile(path) as archive:root=etree.fromstring(archive.read('word/document.xml'))
    body=root.find('w:body',ns);checks=[]
    def text(node):return ''.join(node.xpath('.//w:t/text()',namespaces=ns)) if node is not None else ''
    for index,node in enumerate(body):
        if node.xpath('.//w:drawing',namespaces=ns):
            following=body[index+1] if index+1<len(body) else None
            checks.append({'kind':'figure_caption_below','position':index,
                'status':'pass' if following is not None and text(following).startswith('图') else 'fail',
                'scope':'adjacent_caption_property'})
        if etree.QName(node).localname=='tbl':
            previous=body[index-1] if index else None
            checks.append({'kind':'table_caption_above','position':index,'status':'pass' if text(previous).startswith('表') else 'fail'})
            header=node.find('w:tr/w:trPr/w:tblHeader',ns)
            checks.append({'kind':'repeat_table_header','position':index,'status':'pass' if header is not None else 'unknown',
                           'scope':'property_present_not_actual_pagination'})
    return {'thesis_version':config['thesis_version'],'checks':checks,'three_line_table_modality':'recommended',
            'visual_pagination':'not_verified',
            'manual_requirements':[{'id':r['id'],'statement':r['statement'],'state':'requires_manual_verification'} for r in config['requirements']],
            'policy':'属性核对不代表学术终审；续表实际分页仍需目标软件核对'}
