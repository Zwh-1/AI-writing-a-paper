"""Compile source-linked visual candidates into actual editable document nodes."""
from copy import deepcopy
import re
from . import db,documents,security


def insert_existing(pid, identities, available, paragraph,section_id=None,existing_nodes=()):
    from .paper_tables import table_node
    allowed={a['id']:a for a in available};out=[]
    new_ids=[aid for aid in dict.fromkeys(identities) if not any(n.get('attrs',{}).get('artifactId')==aid for n in existing_nodes)]
    if len(new_ids)>1:raise ValueError('每张首次插入的图表需要独立说明段落；已有图表可以在同段交叉引用')
    for aid in dict.fromkeys(identities):
        if aid not in allowed:raise ValueError('模型引用了本次未提供的图表资源')
        record=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(aid,pid))
        if not record:raise ValueError('图表资源不存在或不属于当前项目')
        meta=db.loads(record['metadata'],{})
        prior=next((n for n in existing_nodes if n.get('attrs',{}).get('artifactId')==aid),None)
        if prior:
            paragraph.setdefault('content',[]).extend([{'type':'text','text':'（见'},{'type':'crossReference','attrs':{'nodeId':db.uid(),'targetSectionId':section_id or '', 'targetId':prior['attrs']['nodeId']}},{'type':'text','text':'）'}])
            continue
        if record['kind']=='table':
            if not meta.get('spec'):raise ValueError('表格资源缺少可编辑的结构数据')
            node=table_node(meta['spec'],aid)
        else:
            node={'type':'figure','attrs':{'artifactId':aid,'nodeId':db.uid(),'caption':allowed[aid]['caption'],
                                         'figurePurpose':allowed[aid]['purpose'],'relatedSectionIds':meta.get('related_section_ids',[])}}
        node['attrs']['explanationNodeId']=paragraph['attrs']['nodeId']
        paragraph['attrs']['describesFigureId']=node['attrs']['nodeId']
        out.append(node)
        paragraph.setdefault('content',[]).extend([{'type':'text','text':'（见'},{'type':'crossReference','attrs':{'nodeId':db.uid(),'targetSectionId':section_id or '', 'targetId':node['attrs']['nodeId']}},{'type':'text','text':'）'}])
    return out


def preview_data(pid,overrides):
    from .figure_standards import inventory
    sections=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position,id",(pid,))
    snapshots=[];used=set();numbering={}
    for section in sections:
        revision=documents.current(section)
        doc=overrides.get(section['id']) or (db.loads(revision['document'],{}) if revision else None)
        if doc is None:continue
        snapshots.append({'section':section,'revision':revision or {'id':'candidate:'+section['id']},'document':doc})
        used.update(n.get('attrs',{}).get('artifactId') for n in documents.nodes(doc) if n.get('attrs',{}).get('artifactId'))
        for node in documents.nodes(doc):
            if node['type']=='citation' and node['attrs']['citationId'] not in numbering:numbering[node['attrs']['citationId']]=len(numbering)+1
    return {'citation_numbers':numbering,'figure_index':[{k:v for k,v in row.items() if k!='metadata'} for row in inventory(pid,snapshots=snapshots,section_plan=sections)],
            'preview_assets':[{'id':a['id'],'sha256':a['sha256'],'name':a['name'],'kind':a['kind']} for a in db.rows('SELECT * FROM artifacts WHERE project_id=?',(pid,)) if a['id'] in used]}


def _check_physical_er(spec,provided_evidence):
    """Check known physical tables, without pretending this proves all semantics."""
    if spec['kind']!='er':return
    from .project_structures import sql_tables
    known={};conflicts=set()
    for item in provided_evidence:
        payload=item.get('payload',{})
        text=item.get('text') or payload.get('text','')
        path=payload.get('path') or item.get('path','source.sql')
        for table in sql_tables(text,path):
            if table['name'] in known and known[table['name']]['fields']!=table['fields']:conflicts.add(table['name'])
            known[table['name']]=table
    nodes={n['id']:n for n in spec['entities']}
    for node in nodes.values():
        table=known.get(node['label'])
        if table and (node['label'] in conflicts or not table['complete_definition']):
            raise ValueError('ER图对应的表定义有冲突或未完整解析，需补齐依据后再生成')
        if table and not {f['name'] for f in node['fields']}<={f['name'] for f in table['fields']}:
            raise ValueError('ER图包含对应建表定义中不存在的字段')
        if table:
            definitions={f['name']:f for f in table['fields']}
            for field in node['fields']:
                actual=definitions[field['name']];constraints=set(actual['constraints'])
                key='PK/FK' if {'PK','FK'}<=constraints else 'PK' if 'PK' in constraints else 'FK' if 'FK' in constraints else ''
                if field.get('key','')!=key:raise ValueError('ER图字段的主键或外键标记与表定义不一致')
                declared=re.sub(r'\s+',' ',actual['data_type']).lower()
                claimed=re.sub(r'\s+',' ',field.get('data_type','')).lower()
                if claimed and claimed not in {declared,re.match(r'\w+',declared)[0]}:
                    raise ValueError('ER图字段类型与表定义不一致')
    for edge in spec['edges']:
        if nodes[edge['source']]['label'] not in known or nodes[edge['target']]['label'] not in known:continue
        source=known[nodes[edge['source']]['label']];target=known[nodes[edge['target']]['label']]
        supported=False
        for child,parent,reverse in ((source,target,False),(target,source,True)):
            for fk in child['foreign_keys']:
                if fk['target_table']!=parent['name']:continue
                cols=set(fk['columns']);refs=set(fk['target_columns'])
                if not any(key and set(key)<=refs for key in [parent['primary_key'],*parent['unique_keys']]):continue
                if not cols<={f['name'] for f in nodes[edge['target'] if reverse else edge['source']]['fields']}:continue
                if not refs<={f['name'] for f in nodes[edge['source'] if reverse else edge['target']]['fields']}:continue
                unique=any(key and set(key)<=cols for key in [child['primary_key'],*child['unique_keys']])
                required=all(any(f['name']==col and ('NOT NULL' in f['constraints'] or 'PK' in f['constraints']) for f in child['fields']) for col in cols)
                cards=('0..1' if unique else '0..N','1' if required else '0..1')
                if reverse:cards=cards[::-1]
                if (edge['source_cardinality'],edge['target_cardinality'])==cards:supported=True
        if not supported:raise ValueError('ER图关系或基数与已提供的外键、唯一性、可空性不一致')


@db.atomic
def compile_proposals(task,section,proposals,content,provided_evidence,*,manifest=None):
    if not proposals:return content,[]
    from .paper_visuals import create_visual,validate_spec
    from .writing_agent.common import digest
    from .paper_visual_render import render_diagram
    from .paper_tables import table_document
    content=deepcopy(content)
    known={e['id'] for e in provided_evidence};created=[];placements=[]
    paragraphs=[n for n in content if n['type']=='paragraph']
    # Validate the complete response before creating any assets.
    for index,proposal in enumerate(proposals):
        evidence_ids=proposal.get('evidence_ids',[])
        if not isinstance(evidence_ids,list) or not evidence_ids or any(eid not in known for eid in evidence_ids):
            raise ValueError('新增图表必须关联本次实际提供的项目证据，不能用占位证据生成图表')
        at=proposal.get('after_paragraph')
        if proposal.get('visual_plan_id') and proposal.get('after_node_id'):
            at=next((position for position,row in enumerate(paragraphs) if row['attrs']['nodeId']==proposal['after_node_id']),None)
        if type(at) is not int or not 0<=at<len(paragraphs):raise ValueError('图表插入位置必须对应本次候选段落')
        explanation=proposal.get('explanation','')
        if not isinstance(explanation,str) or not explanation.strip():raise ValueError('新增图表必须提供正文说明')
        spec=validate_spec(proposal.get('spec'))
        _check_physical_er(spec,[e for e in provided_evidence if e['id'] in evidence_ids])
        # Geometry and text fitting can fail even for valid JSON. Preflight the
        # whole batch before persisting its first asset.
        if spec['kind']=='three_line_table':table_document(spec)
        else:render_diagram(spec)
        placements.append((index,at,evidence_ids,explanation,spec,proposal))
    insertions={};before_insertions={}
    compiled=[]
    for index,at,evidence_ids,explanation,spec,proposal in placements:
        existing=None
        for node in [*content,*compiled]:
            if node['type'] not in ('figure','table') or not node.get('attrs',{}).get('artifactId'):continue
            prior=db.one('SELECT metadata FROM artifacts WHERE id=? AND project_id=?',(node['attrs']['artifactId'],task['project_id']))
            metadata=db.loads(prior['metadata'],{}) if prior else {}
            if metadata.get('spec')==spec and set(metadata.get('evidence_ids',[]))==set(evidence_ids):existing=node;break
        if existing:
            paragraphs[at].setdefault('content',[]).extend([{'type':'text','text':'（见'},{'type':'crossReference','attrs':{'nodeId':db.uid(),'targetSectionId':section['id'],'targetId':existing['attrs']['nodeId']}},{'type':'text','text':'）'}])
            continue
        request={'spec':spec,'explanation':explanation,
            'figure_purpose':'design_schematic','related_section_ids':[section['id']],
            'request_id':'writing_'+digest([task['id'],section['id'],at,spec,sorted(evidence_ids),explanation])[:48]}
        def create_owned(request):
            # The graph validates inputs again before recording the result.
            # Publish ownership with the asset so it is never mistaken for an
            # unrelated change to the task's frozen visual inputs.
            with db.transaction():
                asset=create_visual(task['project_id'],request)
                raw=db.one('SELECT metadata FROM artifacts WHERE id=? AND project_id=?',(asset['id'],task['project_id']))
                meta=db.loads(raw['metadata'],{})
                meta.update(writing_task_id=task['id'],evidence_ids=evidence_ids,needs_review=True,
                            evidence_level='source_linked_candidate_not_execution_result')
                if proposal.get('visual_plan_id'):
                    meta.update(visual_plan_id=proposal['visual_plan_id'],project_material_ids=proposal['project_material_ids'],
                                explanation_node_id=paragraphs[at]['attrs']['nodeId'])
                    if proposal.get('source_material_group')=='execution':
                        meta['evidence_level']='execution_record_summary_requires_review'
                db.execute('UPDATE artifacts SET metadata=? WHERE id=?',(db.dumps(meta),asset['id']))
                return {**asset,'metadata':meta}
        from .ai_runtime.policy import frozen
        if frozen(task).get('graph_version')=='chapter.graph.v3':
            from .ai_graphs.chapter import run
            from .tasks import _validate_model_source_inputs,_validate_model_presend
            # This node owns asset generation only. Random document node IDs are
            # not inputs to rendering. Include every render/source input instead.
            initial={'manifest':manifest or {},'request':request,
                'provided_evidence':[e for e in provided_evidence if e['id'] in evidence_ids]}
            def validate(state):
                _validate_model_source_inputs(task['project_id'],state['manifest'])
                _validate_model_presend(task,state['manifest'])
            def render(state):
                from .skill_runtime import validate_output
                receipt=validate_output(task,state['request']['spec'],'visual_spec')
                return {**state,'asset':create_owned(state['request']),'validation':receipt}
            result=run(task,section,initial,[('generate_visual',render)],validate,namespace='visual:'+request['request_id'])
            asset=result['asset']
        else:asset=create_owned(request)
        nodes=deepcopy(asset['insert_nodes'])
        if proposal.get('explanation_in_paragraph'):
            nodes=[node for node in nodes if node['type'] in ('figure','table')]
            nodes[0].setdefault('attrs',{})['explanationNodeId']=paragraphs[at]['attrs']['nodeId']
            paragraphs[at]['attrs']['describesFigureId']=nodes[0]['attrs']['nodeId']
        for n in nodes:n.setdefault('attrs',{}).setdefault('evidenceIds',evidence_ids)
        compiled.extend(nodes)
        paragraphs[at].setdefault('content',[]).extend([{'type':'text','text':'（见'},
            {'type':'crossReference','attrs':{'nodeId':db.uid(),'targetSectionId':section['id'],'targetId':nodes[0]['attrs']['nodeId']}},
            {'type':'text','text':'）'}])
        bucket=before_insertions if proposal.get('place_before_explanation') else insertions
        bucket.setdefault(paragraphs[at]['attrs']['nodeId'],[]).extend(nodes)
        created.append(asset['id'])
    result=[]
    for node in content:
        result.extend(before_insertions.get(node.get('attrs',{}).get('nodeId'),[]))
        result.append(node);result.extend(insertions.get(node.get('attrs',{}).get('nodeId'),[]))
    return result,created
