import copy
import io
import json
import pytest
from PIL import Image
from docx import Document
from docx.oxml.ns import qn
from app import db,documents,security
from app.paper_visuals import TEMPLATES,validate_spec,create_visual,generate_candidate
from app.paper_visual_render import render_diagram
from app.paper_visual_layout import layout_diagram,_intersects
from app.paper_tables import table_document
from test_workbench import client,project,section,save,file


def overlap(a,b,c,d):
    if a[0]==b[0]==c[0]==d[0]:return max(0,min(max(a[1],b[1]),max(c[1],d[1]))-max(min(a[1],b[1]),min(c[1],d[1])))
    if a[1]==b[1]==c[1]==d[1]:return max(0,min(max(a[0],b[0]),max(c[0],d[0]))-max(min(a[0],b[0]),min(c[0],d[0])))
    return 0


def test_er_relationships_and_cardinalities_are_distinct():
    spec=copy.deepcopy(TEMPLATES['er'])
    spec['edges'][0]['source_cardinality']='0..N';spec['edges'][0]['target_cardinality']='1..N'
    scene=layout_diagram(validate_spec(spec))
    first,second=scene['edges']
    assert first['points'][-1]!=second['points'][-1]
    assert sum(overlap(a,b,c,d) for a,b in zip(first['points'],first['points'][1:]) for c,d in zip(second['points'],second['points'][1:]))==0
    labels=[label for edge in scene['edges'] for label in edge['cardinalities']]
    assert [label['text'] for label in labels]==['0..N','1..N','1','N']
    for i,label in enumerate(labels):
        assert not any(_intersects(label['box'],n['box']) for n in scene['nodes'])
        assert not any(_intersects(label['box'],other['box']) for other in labels[i+1:])


@pytest.mark.parametrize('kind',['flowchart','use_case','er'])
def test_layout_edges_avoid_unrelated_nodes_and_labels(kind):
    scene=layout_diagram(validate_spec(TEMPLATES[kind]))
    for edge in scene['edges']:
        for node in scene['nodes']:
            if node['id'] in (edge['source'],edge['target']):continue
            x0,y0,x1,y1=node['box']
            for a,b in zip(edge['points'],edge['points'][1:]):
                if a[0]==b[0]:assert not (x0<a[0]<x1 and min(a[1],b[1])<y1 and max(a[1],b[1])>y0)
                else:assert not (y0<a[1]<y1 and min(a[0],b[0])<x1 and max(a[0],b[0])>x0)
        if edge['label_box']:assert not any(_intersects(edge['label_box'],n['box']) for n in scene['nodes'])
    data,size=render_diagram(validate_spec(TEMPLATES[kind]))
    with Image.open(io.BytesIO(data)) as image:assert image.size==size and min(size)>=1080


def test_long_accepted_labels_fields_and_cyclic_flow_render():
    spec=copy.deepcopy(TEMPLATES['flowchart'])
    spec['nodes'][1]['label']='录入并核对当前学生所选课程期末考试成绩和课堂考勤记录，确保数据完整准确'
    spec['edges'][2]['label']='验证通过后进入数据库保存并更新成绩统计信息'
    scene=layout_diagram(validate_spec(spec));render_diagram(validate_spec(spec))
    assert scene['edges'][2]['label_text']==spec['edges'][2]['label']
    shuffled=copy.deepcopy(spec);shuffled['nodes'].reverse()
    assert {n['id']:n['rank'] for n in layout_diagram(validate_spec(shuffled))['nodes']}=={n['id']:n['rank'] for n in scene['nodes']}
    entity=copy.deepcopy(TEMPLATES['er']);entity['entities'][0]['fields'][1]['name']='long_descriptive_student_name_for_the_current_enrollment'
    render_diagram(validate_spec(entity))


@pytest.mark.parametrize('kind',['flowchart','er'])
def test_self_loops_are_visible(kind):
    spec=copy.deepcopy(TEMPLATES[kind])
    source=spec['nodes'][1]['id'] if kind=='flowchart' else spec['entities'][0]['id']
    spec['edges'].append({'source':source,'target':source,'label':'重试' if kind=='flowchart' else '上级'})
    scene=layout_diagram(validate_spec(spec));edge=scene['edges'][-1]
    assert edge['points'][0]!=edge['points'][-1]
    assert len(edge['points'])>=4
    render_diagram(validate_spec(spec))


def test_bad_uml_duplicate_edges_and_fields_rejected():
    with pytest.raises(ValueError,match='association 须连接'):
        validate_spec({**TEMPLATES['use_case'],'edges':[{'source':'score','target':'check','relation':'association'}]})
    with pytest.raises(ValueError,match='完全重复'):
        validate_spec({**TEMPLATES['flowchart'],'edges':TEMPLATES['flowchart']['edges']*2})
    spec=copy.deepcopy(TEMPLATES['er']);spec['entities'][0]['fields'].append(spec['entities'][0]['fields'][0])
    with pytest.raises(ValueError,match='重复字段'):validate_spec(spec)
    spec=copy.deepcopy(TEMPLATES['er']);spec['edges'][0]['target_cardinality']='0..*'
    assert validate_spec(spec)['edges'][0]['target_cardinality']=='0..N'


def test_same_save_request_is_idempotent_and_conflicting_reuse_fails(client):
    pid=project(client);body={'spec':TEMPLATES['flowchart'],'request_id':'same-save'}
    first=client.post(f'/api/v1/projects/{pid}/paper-visuals',json=body).json()
    second=client.post(f'/api/v1/projects/{pid}/paper-visuals',json=body).json()
    assert first['id']==second['id'] and len(db.rows('SELECT * FROM artifacts'))==1
    assert first['node']['attrs']['nodeId']!=second['node']['attrs']['nodeId']
    changed={**body,'spec':{**body['spec'],'title':'不同内容'}}
    assert client.post(f'/api/v1/projects/{pid}/paper-visuals',json=changed).status_code==409
    loaded=client.get(f'/api/v1/projects/{pid}/paper-visuals/{first["id"]}').json()
    assert loaded['metadata']['spec']==validate_spec(body['spec'])


def test_preview_hash_matches_download_and_old_asset_remains_immutable(client):
    pid=project(client)
    preview=client.post(f'/api/v1/projects/{pid}/paper-visuals/preview',json={'spec':TEMPLATES['er']}).json()
    asset=client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':TEMPLATES['er']}).json()
    assert preview['render_hash']==asset['sha256']
    assert preview['renderer_version']==asset['metadata']['renderer_version']==3
    next_asset=client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':TEMPLATES['er'],'parent_artifact_id':asset['id']}).json()
    assert next_asset['id']!=asset['id']
    assert client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download').json()==validate_spec(TEMPLATES['er'])


def test_ai_revision_keeps_unsaved_candidate_provenance_and_revocation(client,monkeypatch):
    pid=project(client);sid=section(client,pid)
    f=file(client,pid);source=db.one('SELECT id FROM sources WHERE file_id=?',(f['id'],))['id']
    doc=documents.text_document('有来源的设计');doc['content'][0]['attrs']['sourceIds']=[source]
    revision=client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None}).json()
    body={'kind':'flowchart','instruction':'修改候选','current_spec':TEMPLATES['flowchart'],'source_context':{'revision_ids':[revision['id']]}}
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals/ai-tasks',json=body)
    assert response.status_code==200,response.text
    assert db.loads(response.json()['payload'])['source_context']['revision_ids']==[revision['id']]
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(f['id'],))
    assert client.post(f'/api/v1/projects/{pid}/paper-visuals/ai-tasks',json=body).status_code==403


@pytest.mark.parametrize('value',[[],False,'bad',{'revision_ids':'bad'},{'revision_ids':[{}]},{'source_artifact_id':[]}])
def test_bad_context_is_validation_error(client,value):
    pid=project(client)
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':TEMPLATES['flowchart'],'source_context':value})
    assert result.status_code==422,result.text


def test_old_revision_is_identified_on_reopen(client):
    pid=project(client);sid=section(client,pid);original=save(client,sid,'原始设计').json()
    asset=create_visual(pid,{'spec':TEMPLATES['er'],'source_context':{'revision_ids':[original['id']]}})
    save(client,sid,'设计已修改',original['id'])
    loaded=client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}').json()
    assert loaded['source_context']['sources'][0]['current'] is False


def test_multiline_table_content_and_tall_rows_survive():
    cell={'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'第一段'},{'type':'hardBreak'},{'type':'text','text':'第二行'}]},{'type':'paragraph','content':[{'type':'text','text':'第二段'}]}]}
    assert documents.plain(cell)=='第一段\n第二行\n第二段'
    spec=validate_spec({**TEMPLATES['three_line_table'],'headers':['说明','类型','约束','备注'],'rows':[['内容'*450,'文字','','']]})
    table=Document(io.BytesIO(table_document(spec))).tables[0]
    assert table.cell(1,0).text=='内容'*450
    assert table.rows[1]._tr.trPr.find(qn('w:cantSplit')) is None


def test_invalid_unicode_does_not_reach_image_or_docx_engines(client):
    pid=project(client)
    for kind in ('flowchart','three_line_table'):
        spec=copy.deepcopy(TEMPLATES[kind]);spec['title']='bad\ud800'
        result=client.post(f'/api/v1/projects/{pid}/paper-visuals/preview',content=json.dumps({'spec':spec},ensure_ascii=True),headers={'Content-Type':'application/json'})
        assert result.status_code==422
