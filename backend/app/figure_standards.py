"""Project-scoped figure policy, explicit prose links and deterministic checks."""
import copy
import hashlib
import re
from fastapi import APIRouter,HTTPException
from . import db,documents,security

router=APIRouter()
PURPOSES={'design_schematic':'设计示意图','original_result':'原创结果图','experimental_result':'实验结果图',
          'interface_screenshot':'真实界面截图','test_capture':'真实测试日志','ai_method_output':'AI 研究方法产物'}
PROTECTED={'original_result','experimental_result','interface_screenshot','test_capture'}
SEMANTIC_GUIDES={
    'use_case':'核对外部角色、椭圆用例、普通关联线及 include/extend 方向，避免把关联画成泛化',
    'flowchart':'核对用户操作、后端响应、判断条件和最终返回结果；涉及区块链的环节还需区分链上与链下',
    'architecture':'核对实际架构模式、层次、组件与层间交互',
    'er':'核对实体、属性、实体间关系与基数',
    'module':'核对模块层级及需求、设计、实现章节的模块名称是否一致',
    'class_diagram':'核对实际类名、属性、操作及关联、依赖、继承、聚合、组合的线型',
    'sequence':'核对参与者、生命线、调用顺序、服务处理与返回消息',
    'data_flow':'核对数据流名称、起点、终点与主要处理步骤',
    'contract_interaction':'核对调用方、具体合约或方法、调用顺序以及链上链下边界',
    'circuit':'核对输入、运算或约束、连接和输出是否符合实际电路设计',
    'structure':'核对组成、连接、分类或拓扑关系，不能仅凭美观判为正确',
}
DEFAULT={'numbering':'continuous','caption_space':True,'include_directory':False,'require_explanation':True,
         'require_requirement_link':False,'blockchain':False,'ai_image_policy':'unconfirmed',
         'method_section_id':None,'method_statement':'','reproducibility_artifact_ids':[],
         'required_diagrams':[],'source_note':'用户提供的规范摘要尚未核对原文页码；按本项目正式要求确认。'}


def policy(pid):
    return {**copy.deepcopy(DEFAULT),**db.setting('figure_policy:'+pid,{})}


@router.get('/projects/{pid}/figure-standards')
def get_policy(pid):
    security.require_project(pid)
    from .paper_visuals import KINDS
    return {'policy':policy(pid),'purposes':PURPOSES,'diagram_types':KINDS,'source_status':'summary_requires_original_verification'}


@router.put('/projects/{pid}/figure-standards')
@db.atomic
def save_policy(pid,body:dict):
    security.require_project(pid);values=body.get('policy',body)
    if not isinstance(values,dict):raise ValueError('配图执行设置须为对象')
    result={**policy(pid),**{k:v for k,v in values.items() if k in DEFAULT}}
    if result['numbering'] not in ('continuous','chapter'):raise ValueError('图表编号方式无效')
    for name in ('caption_space','include_directory','require_explanation','require_requirement_link','blockchain'):
        if not isinstance(result[name],bool):raise ValueError('配图设置开关无效：'+name)
    if result['ai_image_policy'] not in ('unconfirmed','allowed_design','forbidden','research_method'):raise ValueError('AI 配图政策无效')
    for name,limit in (('method_statement',4000),('source_note',6000)):
        if not isinstance(result[name],str) or len(result[name])>limit:raise ValueError('配图政策说明格式无效')
    sid=result['method_section_id']
    if sid:
        section=security.require_section(sid)
        if section['project_id']!=pid or section['state']=='archived':raise HTTPException(404,'方法章节不属于本项目')
    ids=result['reproducibility_artifact_ids']
    if not isinstance(ids,list) or len(ids)>30 or any(not isinstance(a,str) for a in ids):raise ValueError('可复现材料标识无效')
    for aid in ids:
        if not db.one('SELECT id FROM artifacts WHERE id=? AND project_id=?',(aid,pid)):raise HTTPException(404,'可复现材料不属于本项目')
    if result['ai_image_policy']=='research_method':_method_evidence(pid,result)
    plans=result['required_diagrams']
    if not isinstance(plans,list) or len(plans)>100:raise ValueError('章节配图计划最多100项')
    from .paper_visuals import KINDS
    seen=set()
    for item in plans:
        if not isinstance(item,dict) or item.get('kind') not in KINDS or not isinstance(item.get('section_id'),str):raise ValueError('章节配图计划格式无效')
        sec=security.require_section(item['section_id'])
        if sec['project_id']!=pid or sec['state']=='archived':raise HTTPException(404,'配图计划章节不属于本项目')
        key=(item['section_id'],item['kind'])
        if key in seen:raise ValueError('配图计划重复')
        seen.add(key)
    db.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),pid,'figure_policy',db.dumps(policy(pid)),db.now()))
    db.set_setting('figure_policy:'+pid,result)
    return get_policy(pid)


def _method_evidence(pid,settings):
    sid=settings.get('method_section_id')
    if not sid or len(settings.get('method_statement','').strip())<20 or not settings.get('reproducibility_artifact_ids'):
        raise HTTPException(422,'AI 作为研究方法时须选择方法章节、填写可复现方法说明并关联真实代码或数据材料')
    sec=security.require_section(sid)
    if sec['project_id']!=pid:raise HTTPException(404,'方法章节不属于当前项目')
    rev=documents.current(sec)
    if not rev or not rev['text'].strip():raise HTTPException(422,'请先在方法章节保存 AI 研究设计说明')
    if re.sub(r'\s+','',settings['method_statement']) not in re.sub(r'\s+','',rev['text']):raise HTTPException(422,'请先将所登记的 AI 方法说明保存到方法章节正文中，不能仅写在配图设置里')
    for aid in settings['reproducibility_artifact_ids']:
        row=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(aid,pid))
        if not row or not row['sha256'] or not security.safe_object(row['sha256']).is_file():raise HTTPException(422,'可复现材料文件缺失')
        if row['kind'] in ('figure','table'):raise HTTPException(422,'可复现材料须关联代码、数据或执行记录，不能只提供图片本身')


def assert_ai_allowed(pid,purpose='design_schematic'):
    if not isinstance(purpose,str) or purpose not in PURPOSES:raise ValueError('图片用途无效')
    if purpose in PROTECTED:raise HTTPException(403,'原创、实验结果及真实截图不能用生图模型生成或改动，请上传实际产物')
    settings=policy(pid)
    if settings['ai_image_policy']=='forbidden':raise HTTPException(403,'本项目的配图政策禁止 AI 图片生成与修改')
    if purpose=='ai_method_output':
        if settings['ai_image_policy']!='research_method':raise HTTPException(403,'AI 研究产物须先登记研究方法和可复现材料')
        _method_evidence(pid,settings)
    return settings


def metadata(pid,body,base=None):
    old=base or {};purpose=body.get('figure_purpose',old.get('figure_purpose','design_schematic'))
    if not isinstance(purpose,str) or purpose not in PURPOSES:raise ValueError('图片用途无效')
    explanation=body.get('explanation',old.get('explanation',''))
    if not isinstance(explanation,str) or len(explanation)>6000:raise ValueError('图后说明须为不超过6000字的文字')
    links=body.get('related_section_ids',old.get('related_section_ids',[]))
    if not isinstance(links,list) or len(links)>30 or any(not isinstance(x,str) for x in links):raise ValueError('需求与实现章节关联格式无效')
    for sid in links:
        sec=security.require_section(sid)
        if sec['project_id']!=pid or sec['state']=='archived':raise HTTPException(404,'关联章节不属于当前项目')
    return {'figure_purpose':purpose,'explanation':explanation.strip(),'related_section_ids':list(dict.fromkeys(links))}


def insert_nodes(asset,node):
    result=[node];meta=asset['metadata']
    if node['type']=='figure':
        node['attrs'].update({'diagramType':meta.get('spec',{}).get('kind','unknown'),'figurePurpose':meta.get('figure_purpose','design_schematic'),
                              'relatedSectionIds':meta.get('related_section_ids',[])})
    if meta.get('explanation'):
        identity=db.uid();node['attrs']['explanationNodeId']=identity
        result.append({'type':'paragraph','attrs':{'nodeId':identity,'describesFigureId':node['attrs']['nodeId']},
                       'content':[{'type':'text','text':meta['explanation']}]})
    return result


def bookmark(section_id,node_id):
    return 'fig_'+hashlib.sha256((section_id+':'+node_id).encode()).hexdigest()[:26]


def _chinese_number(value):
    digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if value.isdigit():return int(value)
    if value=='十':return 10
    if '十' in value:
        a,b=value.split('十',1);return digits.get(a,1)*10+digits.get(b,0)
    return digits.get(value)


def inventory(pid,settings=None,snapshots=None,artifacts=None,section_plan=None):
    settings=settings or policy(pid)
    format_hash=settings.get('_format_hash') or hashlib.sha256(db.dumps({'format':db.setting('format:'+pid,{}) or {},'template':db.setting('template:'+pid)}).encode()).hexdigest()
    if snapshots is None:
        sections=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,));snapshots=[]
        for sec in sections:
            rev=documents.current(sec)
            if rev:snapshots.append({'section':sec,'revision':rev,'document':db.loads(rev['document'],{})})
    else:sections=section_plan if section_plan is not None else [s['section'] for s in snapshots]
    artifacts=artifacts if artifacts is not None else {a['id']:a for a in db.rows('SELECT * FROM artifacts WHERE project_id=?',(pid,))}
    by_id={s['id']:s for s in sections};roots={};number=0
    for sec in sections:
        if sec.get('parent_id') in by_id:continue
        if sec['kind'] in ('abstract','abstract_en','acknowledgements','references'):roots[sec['id']]=0;continue
        match=re.match(r'^\s*(?:第([一二两三四五六七八九十零\d]+)章|([1-9]\d*)[.、\s])',sec['title'])
        explicit=_chinese_number(next(v for v in match.groups() if v)) if match else None
        number=explicit if explicit is not None else number+1;roots[sec['id']]=number
    counts={};rows=[]
    for item in snapshots:
        sec=item['section'];root=sec;seen=set()
        while root.get('parent_id') in by_id and root['id'] not in seen:seen.add(root['id']);root=by_id[root['parent_id']]
        chapter=roots.get(root['id'],0)
        for node in documents.nodes(item['document']):
            if node['type'] not in ('figure','table'):continue
            attrs=node.get('attrs',{});kind=node['type'];key=(kind,chapter if settings['numbering']=='chapter' else None)
            counts[key]=counts.get(key,0)+1;index=counts[key]
            value=f'{chapter}-{index}' if settings['numbering']=='chapter' and chapter else str(index)
            label=('图' if kind=='figure' else '表')+(' ' if settings['caption_space'] else '')+value
            asset=artifacts.get(attrs.get('artifactId'));meta=db.loads(asset['metadata'],{}) if asset and isinstance(asset['metadata'],str) else asset.get('metadata',{}) if asset else {}
            declared=attrs.get('figurePurpose')
            purpose=declared if declared not in (None,'','unknown') else meta.get('figure_purpose','design_schematic' if meta.get('generator') in ('paper-image-v1','paper-visuals-v1') else 'experimental_result' if meta.get('actual_execution') and meta.get('source_file_id') else 'unknown')
            related=attrs.get('relatedSectionIds',meta.get('related_section_ids',[]))
            rows.append({'section_id':sec['id'],'section_title':sec['title'],'revision_id':item['revision']['id'],'node_id':attrs.get('nodeId',''),'kind':kind,'index':index,'chapter':chapter,
                         'format_hash':format_hash,
                         'label':label,'caption':attrs.get('caption') or (asset['name'] if asset else ''),'artifact_id':attrs.get('artifactId'),
                         'diagram_type':attrs.get('diagramType') or meta.get('spec',{}).get('kind','unknown'),
                         'purpose':purpose,
                         'ai_generated':meta.get('render_engine')=='gpt-image-2' or meta.get('ai_generated') is True or purpose=='ai_method_output','explanation_node_id':attrs.get('explanationNodeId'),
                         'related_section_ids':related,'related_revision_ids':[(key,by_id[key].get('current_revision_id')) for key in related if key in by_id],
                         'bookmark':bookmark(sec['id'],attrs.get('nodeId','')),'metadata':meta})
    return rows


def resolve_reference(rows,attrs,section_id=None):
    matches=[r for r in rows if r['node_id']==attrs.get('targetId') and (not attrs.get('targetSectionId') or r['section_id']==attrs['targetSectionId'])]
    return matches[0] if len(matches)==1 else None


@router.get('/projects/{pid}/figure-links')
def figure_links(pid):
    security.require_project(pid);rows=inventory(pid);paragraphs=[]
    for sec in db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,)):
        rev=documents.current(sec)
        if not rev:continue
        for node in db.loads(rev['document'],{}).get('content',[]):
            if node['type']=='paragraph' and documents.plain(node).strip():paragraphs.append({'section_id':sec['id'],'node_id':node['attrs']['nodeId'],'text':documents.plain(node),'describes_figure_id':node['attrs'].get('describesFigureId'),'revision_id':rev['id']})
    return {'figures':[{k:v for k,v in r.items() if k!='metadata'} for r in rows],'paragraphs':paragraphs}


@router.get('/projects/{pid}/figure-index')
def figure_index(pid):
    security.require_project(pid)
    return {'figures':[{k:v for k,v in row.items() if k!='metadata'} for row in inventory(pid)]}


@router.put('/sections/{sid}/figure-links')
def link_explanation(sid,body:dict):
    sec=security.require_section(sid);rev=documents.current(sec)
    if not rev or body.get('base_revision_id')!=rev['id']:raise HTTPException(409,'正文版本已变化，请重新读取图文关联')
    doc=db.loads(rev['document'],{});nodes=doc.get('content',[])
    figure=next((n for n in nodes if n['type']=='figure' and n['attrs']['nodeId']==body.get('figure_node_id')),None)
    para=next((n for n in nodes if n['type']=='paragraph' and n['attrs']['nodeId']==body.get('paragraph_node_id')),None)
    if not figure or not para or not documents.plain(para).strip():raise HTTPException(422,'请选择本节的图片和有实际内容的说明段落')
    if para['attrs'].get('describesFigureId') not in (None,'',figure['attrs']['nodeId']):raise HTTPException(409,'该段已关联其他图片，请选择独立说明段落')
    for old in nodes:
        if old is not para and old.get('attrs',{}).get('describesFigureId')==figure['attrs']['nodeId']:old['attrs'].pop('describesFigureId',None)
    figure['attrs']['explanationNodeId']=para['attrs']['nodeId'];para['attrs']['describesFigureId']=figure['attrs']['nodeId']
    return documents.save(sec,doc,rev['id'])


@router.put('/sections/{sid}/figures/{node_id}/metadata')
def update_figure_metadata(sid,node_id,body:dict):
    sec=security.require_section(sid);rev=documents.current(sec)
    if not rev or body.get('revision_id')!=rev['id']:raise HTTPException(409,'正文版本已变化，请重新读取当前图片')
    purpose=body.get('figure_purpose')
    if not isinstance(purpose,str) or purpose not in PURPOSES:raise ValueError('请选择实际图片用途')
    caption=body.get('caption','')
    if not isinstance(caption,str) or not caption.strip() or len(caption)>100 or re.search(r'[\r\n]',caption):raise ValueError('图题须为1–100字且不能手工换行')
    doc=db.loads(rev['document'],{});node=next((n for n in doc.get('content',[]) if n['type']=='figure' and n['attrs']['nodeId']==node_id),None)
    if not node:raise HTTPException(404,'图片不属于当前章节')
    node['attrs'].update({'figurePurpose':purpose,'caption':caption.strip()})
    return documents.save(sec,doc,rev['id'])


@router.get('/projects/{pid}/figure-checks')
def checks(pid):
    security.require_project(pid);return evaluate(pid)


def evaluate(pid,saved_snapshots=None,artifacts=None,settings=None):
    settings=settings or policy(pid);items=[];snapshots={}
    if saved_snapshots is None:
        saved_snapshots=[]
        for sec in db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived'",(pid,)):
            rev=documents.current(sec)
            if rev:saved_snapshots.append({'section':sec,'revision':rev,'document':db.loads(rev['document'],{})})
    rows=inventory(pid,settings,saved_snapshots,artifacts)
    for item in saved_snapshots:snapshots[item['section']['id']]=(item['revision'],item['document'])
    def add(row,code,message,state='fail',severity='warning'):
        items.append({'code':code,'message':message,'result':state,'severity':severity,'section_id':row.get('section_id'),'node_id':row.get('node_id'),'label':row.get('label',''),'method':'deterministic' if state!='unknown' else 'human'})
    for row in rows:
        rev,doc=snapshots[row['section_id']];top=doc.get('content',[]);nodes={n.get('attrs',{}).get('nodeId'):n for n in top}
        review=db.one("SELECT content FROM project_revisions WHERE project_id=? AND kind='figure_review' AND json_extract(content,'$.node_id')=? AND json_extract(content,'$.section_id')=? ORDER BY created_at DESC LIMIT 1",(pid,row['node_id'],row['section_id']))
        record=db.loads(review['content'],{}) if review else {}
        reviewed=record.get('revision_id')==rev['id'] and record.get('binding')==review_binding(row,nodes,settings)
        if not row['caption'].strip():add(row,'CAPTION_MISSING','图表缺少标题')
        elif re.search(r'[\r\n]',row['caption']):add(row,'CAPTION_LINEBREAK','图表标题含手工换行，请按模板核对')
        elif len(row['caption'])>40:add(row,'CAPTION_WRAP','长图题版式已人工核对' if reviewed else '图题较长，需在目标排版中检查是否换行','pass' if reviewed else 'unknown')
        if row['kind']=='table' and settings['require_explanation']:
            explanation=nodes.get(row['explanation_node_id'])
            if not explanation or explanation['type']!='paragraph' or explanation.get('attrs',{}).get('describesFigureId')!=row['node_id'] or not documents.plain(explanation).strip():
                add(row,'EXPLANATION_MISSING','表格尚未关联说明段落，需解释关键字段、条件或比较结果')
            else:
                position=next((i for i,n in enumerate(top) if n.get('attrs',{}).get('nodeId')==row['node_id']),-1)
                if position<0 or position+1>=len(top) or top[position+1].get('attrs',{}).get('nodeId')!=row['explanation_node_id']:
                    add(row,'EXPLANATION_POSITION','说明段落没有紧随表格，需核对图文位置','unknown')
                if re.search(r'待补|TODO|TBD',documents.plain(explanation),re.I):add(row,'EXPLANATION_PLACEHOLDER','表格说明仍包含待补占位内容')
                add(row,'EXPLANATION_SEMANTICS','图文已由用户逐项核对' if reviewed else '核对关键字段、联合约束、条件或数据与说明一致，避免逐格复读','pass' if reviewed else 'unknown','info')
        if row['kind']=='figure':
            asset=(artifacts or {}).get(row['artifact_id']) if artifacts is not None else db.one('SELECT * FROM artifacts WHERE id=?',(row['artifact_id'],))
            if asset:
                asset_meta=db.loads(asset['metadata'],{}) if isinstance(asset.get('metadata'),str) else asset.get('metadata',{})
                if asset_meta.get('render_engine')=='gpt-image-2':
                    from .image_briefs import alignment_finding
                    position=next((i for i,n in enumerate(top) if n.get('attrs',{}).get('nodeId')==row['node_id']),-1)
                    state,message,bound=alignment_finding(pid,row['artifact_id'],asset_meta,row['section_id'],top,position)
                    if state=='unknown' and reviewed and bound:
                        state='pass';message='生成依据、插入位置与当前图文已人工核对'
                    add(row,'IMAGE_PLACEMENT_ALIGNMENT',message,state,'blocker' if state=='fail' else 'warning')
                if not isinstance(asset.get('sha256'),str):add(row,'IMAGE_FILE_MISSING','图片文件缺失','fail','blocker')
                else:
                    image_path=security.safe_object(asset['sha256'])
                    if not image_path.is_file():add(row,'IMAGE_FILE_MISSING','图片文件缺失','fail','blocker')
                    elif hashlib.sha256(image_path.read_bytes()).hexdigest()!=asset['sha256']:add(row,'IMAGE_HASH_MISMATCH','图片文件与保存的版本哈希不一致','fail','blocker')
            if row['purpose'] not in PURPOSES:add(row,'PURPOSE_UNCLASSIFIED','图片用途尚未分类，请区分设计示意、真实截图与实验结果','unknown')
            explanation=nodes.get(row['explanation_node_id'])
            if settings['require_explanation'] and (not explanation or explanation['type']!='paragraph' or explanation.get('attrs',{}).get('describesFigureId')!=row['node_id'] or not documents.plain(explanation).strip()):
                add(row,'EXPLANATION_MISSING','图片尚未关联图后说明，需解释各元素及相互关系')
            elif explanation:
                position=next((i for i,n in enumerate(top) if n.get('attrs',{}).get('nodeId')==row['node_id']),-1)
                if position<0 or position+1>=len(top) or top[position+1].get('attrs',{}).get('nodeId')!=row['explanation_node_id']:add(row,'EXPLANATION_POSITION','说明段落没有紧随图片，需核对图文位置','unknown')
                if re.search(r'待补|TODO|TBD',documents.plain(explanation),re.I):add(row,'EXPLANATION_PLACEHOLDER','图片说明仍包含待补占位内容')
                guide=SEMANTIC_GUIDES.get(row['diagram_type'],'核对图中元素、关系和实际来源')
                add(row,'EXPLANATION_SEMANTICS','图文已由用户逐项核对' if reviewed else guide+'；图后说明须覆盖这些要素，仍需人工核对','pass' if reviewed else 'unknown','info')
            if settings['require_requirement_link'] and not row['related_section_ids']:add(row,'REQUIREMENT_LINK_MISSING','设计图未关联需求与实现章节')
            if row['related_section_ids']:add(row,'MODULE_TRACEABILITY','需求、设计、实现的模块名称已人工核对' if reviewed else '已建立章节关联，模块名称及功能双向对应仍需人工核对','pass' if reviewed else 'unknown')
            if row['ai_generated']:
                if row['purpose'] in PROTECTED:add(row,'AI_RESULT_IMAGE','AI 图片不能冒充原创、实验结果或真实截图','fail','blocker')
                elif settings['ai_image_policy'] in ('forbidden','unconfirmed'):add(row,'AI_POLICY_UNCONFIRMED','本项目尚未确认允许 AI 设计示意图，正式使用前须核对原文规范','fail' if settings['ai_image_policy']=='forbidden' else 'unknown','blocker' if settings['ai_image_policy']=='forbidden' else 'warning')
                elif row['purpose']=='ai_method_output':
                    try:_method_evidence(pid,settings)
                    except HTTPException:add(row,'AI_METHOD_MISSING','AI 研究产物缺少方法章节或可复现材料','fail','blocker')
                    else:add(row,'AI_METHOD_REPRODUCIBILITY','可复现性已人工核对' if reviewed else '已登记方法与材料，可复现性仍需人工复核','pass' if reviewed else 'unknown')
                add(row,'AI_IMAGE_CONTENT','AI 图片内容已人工核对' if reviewed else 'AI 图中的文字、连线与实际设计须人工核对，未自动判为合规','pass' if reviewed else 'unknown','info')
            if row['purpose'] in PROTECTED:add(row,'RESULT_AUTHENTICITY','原始产物来源已人工核对' if reviewed else '结果图片、日志或截图的原始来源与真实性须人工核对','pass' if reviewed else 'unknown')
    for sid,(rev,doc) in snapshots.items():
        for node in documents.nodes(doc):
            if node['type']=='crossReference' and not resolve_reference(rows,node.get('attrs',{}),sid):add({'section_id':sid,'node_id':node.get('attrs',{}).get('nodeId')},'REFERENCE_DANGLING','图表交叉引用的目标不存在或不唯一','fail','blocker')
    for plan in settings['required_diagrams']:
        if not any(r['section_id']==plan['section_id'] and r['diagram_type']==plan['kind'] for r in rows):
            from .paper_visuals import KINDS
            add({'section_id':plan['section_id']},'PLANNED_DIAGRAM_MISSING','章节配图计划尚未采用：'+KINDS.get(plan['kind'],plan['kind']))
    return {'items':items,'summary':{state:sum(x['result']==state for x in items) for state in ('pass','fail','unknown')},'source_status':'summary_requires_original_verification'}


def review_binding(row,nodes,settings):
    explanation=nodes.get(row.get('explanation_node_id'),{})
    return hashlib.sha256(db.dumps({'caption':row['caption'],'explanation':documents.plain(explanation),
        'artifact_id':row['artifact_id'],'purpose':row['purpose'],'policy':{k:settings[k] for k in DEFAULT},'format':row.get('format_hash'),
        'relations':row['related_section_ids'],'related_revisions':row.get('related_revision_ids',[])}).encode()).hexdigest()


@router.post('/sections/{sid}/figures/{node_id}/review')
def record_review(sid,node_id,body:dict):
    sec=security.require_section(sid);rev=documents.current(sec)
    if not rev or rev['id']!=body.get('revision_id'):raise HTTPException(409,'正文版本已变化，请重新核对当前图文')
    note=body.get('note','')
    if not isinstance(note,str) or len(note.strip())<6 or len(note)>3000:raise ValueError('请填写至少6字的实际核对依据')
    row=next((r for r in inventory(sec['project_id']) if r['section_id']==sid and r['node_id']==node_id and r['kind']=='figure'),None)
    if not row:raise HTTPException(404,'图片不属于当前章节')
    nodes={n.get('attrs',{}).get('nodeId'):n for n in db.loads(rev['document'],{}).get('content',[])}
    if not nodes.get(row['explanation_node_id']):raise HTTPException(422,'请先关联图后说明，再确认图文核对')
    result={'section_id':sid,'node_id':node_id,'revision_id':rev['id'],'binding':review_binding(row,nodes,policy(sec['project_id'])),'note':note.strip(),'verification':'human_confirmed'}
    db.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),sec['project_id'],'figure_review',db.dumps(result),db.now()))
    return result
