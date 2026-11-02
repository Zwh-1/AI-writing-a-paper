"""Compile source-linked visual candidates into actual editable document nodes."""
from copy import deepcopy
from fastapi import HTTPException
from . import db,documents,security


def insert_existing(pid, identities, available, paragraph,section_id=None,existing_nodes=()):
    from .paper_tables import table_node
    allowed={a['id']:a for a in available};out=[]
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


def compile_proposals(task,section,proposals,content,provided_evidence):
    if not proposals:return content,[]
    from .paper_visuals import create_visual,validate_spec
    known={e['id'] for e in provided_evidence};created=[];placements=[]
    paragraphs=[n for n in content if n['type']=='paragraph']
    # Validate the complete response before creating any assets.
    for index,proposal in enumerate(proposals):
        evidence_ids=proposal.get('evidence_ids',[])
        if not isinstance(evidence_ids,list) or not evidence_ids or any(eid not in known for eid in evidence_ids):
            raise ValueError('新增图表必须关联本次实际提供的项目证据，不能用占位证据生成图表')
        at=proposal.get('after_paragraph')
        if type(at) is not int or not 0<=at<len(paragraphs):raise ValueError('图表插入位置必须对应本次候选段落')
        explanation=proposal.get('explanation','')
        if not isinstance(explanation,str) or not explanation.strip():raise ValueError('新增图表必须提供正文说明')
        spec=validate_spec(proposal.get('spec'))
        placements.append((index,at,evidence_ids,explanation,spec))
    insertions={}
    for index,at,evidence_ids,explanation,spec in placements:
        existing=None
        for node in content:
            if node['type'] not in ('figure','table') or not node.get('attrs',{}).get('artifactId'):continue
            prior=db.one('SELECT metadata FROM artifacts WHERE id=? AND project_id=?',(node['attrs']['artifactId'],task['project_id']))
            metadata=db.loads(prior['metadata'],{}) if prior else {}
            if metadata.get('spec')==spec and set(metadata.get('evidence_ids',[]))==set(evidence_ids):existing=node;break
        if existing:
            paragraphs[at].setdefault('content',[]).extend([{'type':'text','text':'（见'},{'type':'crossReference','attrs':{'nodeId':db.uid(),'targetSectionId':section['id'],'targetId':existing['attrs']['nodeId']}},{'type':'text','text':'）'}])
            continue
        asset=create_visual(task['project_id'],{'spec':spec,'explanation':explanation,
            'figure_purpose':'design_schematic','related_section_ids':[section['id']],
            'request_id':'writing_'+task['id']+'_'+section['id']+'_'+str(index)})
        raw=db.one('SELECT * FROM artifacts WHERE id=?',(asset['id'],));meta=db.loads(raw['metadata'],{})
        meta.update(writing_task_id=task['id'],evidence_ids=evidence_ids,needs_review=True,
                    evidence_level='source_linked_candidate_not_execution_result')
        db.execute('UPDATE artifacts SET metadata=? WHERE id=?',(db.dumps(meta),asset['id']))
        nodes=deepcopy(asset['insert_nodes'])
        for n in nodes:n.setdefault('attrs',{}).setdefault('evidenceIds',evidence_ids)
        paragraphs[at].setdefault('content',[]).extend([{'type':'text','text':'（见'},
            {'type':'crossReference','attrs':{'nodeId':db.uid(),'targetSectionId':section['id'],'targetId':nodes[0]['attrs']['nodeId']}},
            {'type':'text','text':'）'}])
        insertions.setdefault(paragraphs[at]['attrs']['nodeId'],[]).extend(nodes)
        created.append(asset['id'])
    result=[]
    for node in content:
        result.append(node);result.extend(insertions.get(node.get('attrs',{}).get('nodeId'),[]))
    return result,created
