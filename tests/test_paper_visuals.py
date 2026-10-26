import copy
import io
import zipfile
import pytest
from PIL import Image
from docx import Document
from docx.oxml.ns import qn
from app import db, documents, exports, security, tasks
from app.paper_visuals import TEMPLATES, validate_spec, generate_candidate, check_context
from app.paper_tables import table_document
from test_workbench import client, project, section, save, file


@pytest.mark.parametrize('kind',['flowchart','use_case','er'])
def test_paper_visual_hd_images_and_immutable_sources(client,kind):
    pid = project(client)
    spec = copy.deepcopy(TEMPLATES[kind])
    preview = client.post(f'/api/v1/projects/{pid}/paper-visuals/preview',json={'spec':spec})
    assert preview.status_code == 200,preview.text
    assert min(preview.json()['width'],preview.json()['height'])>=1080
    saved = client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':spec})
    assert saved.status_code==200,saved.text
    asset = saved.json()
    source = client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download')
    assert source.json() == validate_spec(spec)
    png = client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download?format=png')
    assert png.headers['content-type']=='image/png'
    assert '.png' in png.headers['content-disposition']
    with Image.open(io.BytesIO(png.content)) as image:
        assert min(image.size)>=1080
    modified = {**spec,'title':'修改后的图'}
    updated = client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':modified,'parent_artifact_id':asset['id']}).json()
    assert updated['id']!=asset['id']
    assert client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download').json()['title']==spec['title']
    assert updated['metadata']['parent_artifact_id']==asset['id']


def assert_three_lines(table):
    borders = table._tbl.tblPr.find(qn('w:tblBorders'))
    assert borders is not None
    assert borders.find(qn('w:top')).get(qn('w:val'))=='single'
    assert borders.find(qn('w:bottom')).get(qn('w:val'))=='single'
    for name in ('left','right','insideH','insideV'):
        assert borders.find(qn('w:'+name)).get(qn('w:val'))=='nil'
    header = table.rows[0]
    assert header._tr.trPr.find(qn('w:tblHeader')) is not None
    assert header.cells[0]._tc.tcPr.find(qn('w:tcBorders')).find(qn('w:bottom')).get(qn('w:sz'))=='6'
    assert table.rows[-1].cells[0]._tc.tcPr.find(qn('w:tcBorders')).find(qn('w:bottom')).get(qn('w:sz'))=='12'


def test_paper_visual_native_table_roundtrip_and_thesis_export(client,monkeypatch):
    pid, spec = project(client),copy.deepcopy(TEMPLATES['three_line_table'])
    spec['rows'][0][0] = '编号 & <主键>'
    asset = client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':spec}).json()
    downloaded = client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download?format=docx')
    assert downloaded.status_code == 200
    standalone = Document(io.BytesIO(downloaded.content))
    assert len(standalone.tables)==1
    assert standalone.tables[0].cell(1,0).text=='编号 & <主键>'
    assert_three_lines(standalone.tables[0])
    sid = section(client,pid)
    figure = client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':TEMPLATES['flowchart']}).json()
    document = {'type':'doc','content':[asset['node'],figure['node']]}
    saved = client.put(f'/api/v1/sections/{sid}/content',json={'document':document,'base_revision_id':None})
    assert saved.status_code==200,saved.text
    node = db.loads(saved.json()['document'])['content'][0]
    assert node['attrs']['tableStyle']=='three-line'
    assert node['attrs']['caption']==spec['title']
    assert node['attrs']['note']==spec['note']
    monkeypatch.setattr(exports,'renderer',lambda:None)
    result = exports.export_project(pid,{'pdf':False})
    folder = exports.DATA/'exports'/result['export_id']
    thesis = Document(folder/'论文.docx')
    assert_three_lines(thesis.tables[0])
    assert len(thesis.inline_shapes)==1
    assert any(p.text=='表 1 '+spec['title'] for p in thesis.paragraphs)
    assert any(p.text=='图 1 '+TEMPLATES['flowchart']['title'] for p in thesis.paragraphs)
    markdown = (folder/'论文.md').read_text(encoding='utf-8')
    assert spec['title'] in markdown and spec['note'] in markdown
    with zipfile.ZipFile(folder/'论文交付包.zip') as bundle:
        assert '图-'+figure['id']+'.png' in bundle.namelist()


@pytest.mark.parametrize('patch,message',[
    ({'nodes':[{'id':'a','label':'甲'},{'id':'a','label':'乙'}]},'重复'),
    ({'edges':[{'source':'start','target':'missing'}]},'现有节点'),
    ({'nodes':[{'id':'x','label':'','shape':'process'}]},'节点名称'),
    ({'nodes':[{'id':'x','label':'甲','shape':'script'}]},'形状'),
    ({'nodes':[{'id':'../path','label':'甲'}]},'id'),
    ({'edges':{'bad':'value'}},'数组'),
])
def test_paper_visual_invalid_code(patch,message):
    with pytest.raises(ValueError,match=message):
        validate_spec({**TEMPLATES['flowchart'],**patch})


def test_paper_visual_table_validation():
    with pytest.raises(ValueError,match='列数'):
        validate_spec({**TEMPLATES['three_line_table'],'rows':[['x']]})
    with pytest.raises(ValueError,match='有限数值'):
        validate_spec({**TEMPLATES['three_line_table'],'headers':['a'],'rows':[[float('nan')]]})
    with pytest.raises(ValueError,match='只能连接用例'):
        validate_spec({**TEMPLATES['use_case'],'edges':[{'source':'admin','target':'score','relation':'include'}]})


def test_paper_visual_project_boundary(client):
    first,second = project(client),project(client,'其他项目')
    asset = client.post(f'/api/v1/projects/{first}/paper-visuals',json={'spec':TEMPLATES['three_line_table']}).json()
    assert client.get(f'/api/v1/projects/{second}/paper-visuals/{asset["id"]}/download').status_code==404
    sid = section(client,second)
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':{'type':'doc','content':[asset['node']]},'base_revision_id':None}).status_code==422
    assert client.post(f'/api/v1/projects/{second}/paper-visuals',json={'spec':TEMPLATES['flowchart'],'parent_artifact_id':asset['id']}).status_code==404
    assert not db.rows('SELECT * FROM revisions')


def test_paper_visual_ai_candidate_uses_chapter_and_requires_adoption(client,monkeypatch):
    pid = project(client)
    sid = section(client,pid)
    revision = save(client,sid,'系统只有学生管理、课程管理和成绩录入。').json()
    seen = []
    def model_call(task,system,user,manifest,images=None):
        seen.append((system,db.loads(user),manifest))
        return {'text':db.dumps({'spec':TEMPLATES['use_case'],'missing_materials':['请核对功能是否全部实现']}),'run_id':'synthetic'}
    monkeypatch.setattr(tasks,'model_call',model_call)
    queued = client.post(f'/api/v1/projects/{pid}/paper-visuals/ai-tasks',json={'kind':'use_case','section_id':sid,'instruction':'画管理员用例图'})
    assert queued.status_code==200,queued.text
    result = generate_candidate(queued.json())
    assert result['automatic_acceptance'] is False
    assert result['source_context']['revision_ids']==[revision['id']]
    assert seen[0][1]['chapter_text']==['系统只有学生管理、课程管理和成绩录入。']
    assert not db.rows('SELECT * FROM artifacts')
    assert security.require_section(sid)['current_revision_id']==revision['id']


def test_paper_visual_ai_blocks_local_only_and_rechecks_permissions(client,monkeypatch):
    pid = project(client)
    sid = section(client,pid)
    f = file(client,pid,'仅限本机的研究设计。')
    source = db.one('SELECT id FROM sources WHERE file_id=?',(f['id'],))['id']
    document = documents.text_document('系统设计')
    document['content'][0]['attrs']['sourceIds']=[source]
    revision = client.put(f'/api/v1/sections/{sid}/content',json={'document':document,'base_revision_id':None}).json()
    queued = client.post(f'/api/v1/projects/{pid}/paper-visuals/ai-tasks',json={'kind':'flowchart','section_id':sid})
    assert queued.status_code==200
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(f['id'],))
    with pytest.raises(Exception,match='禁止外发'):
        check_context(pid,{'revision_ids':[revision['id']]})
    assert client.post(f'/api/v1/projects/{pid}/paper-visuals/ai-tasks',json={'kind':'flowchart','section_id':sid}).status_code==403
    asset = client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':TEMPLATES['three_line_table'],'source_context':{'revision_ids':[revision['id']]}}).json()
    new_sid = section(client,pid)
    derived = client.put(f'/api/v1/sections/{new_sid}/content',json={'document':{'type':'doc','content':[asset['node']]},'base_revision_id':None}).json()
    with pytest.raises(Exception,match='禁止外发'):
        check_context(pid,{'revision_ids':[derived['id']]})


def test_paper_visual_existing_blocks_survive_ai_rewrite(client):
    from app.writing_agent.drafting import document
    original = {'type':'doc','content':[documents.text_document('原文')['content'][0],
        {'type':'table','attrs':{'nodeId':'table','tableStyle':'three-line','caption':'字段表','note':'按实际实现'},'content':[]},
        {'type':'figure','attrs':{'nodeId':'figure','caption':'原图','artifactId':'local-image'}}]}
    candidate = {'paragraphs':[{'local_id':'p1','text':'改写后的段落','reference_ids':[],'evidence_ids':[]}],'missing_materials':[]}
    result,_ = document(candidate,current=original)
    assert [n for n in result['content'] if n['type'] in ('table','figure')]==original['content'][1:]


def test_paper_visual_routes_avoid_intermediate_nodes():
    from app.paper_visual_render import _route
    first, middle, last = (100,100,300,180),(100,260,300,340),(100,420,300,500)
    path = _route(first,last,[first,middle,last],650,600)
    assert path[0]==(200,180) and path[-1]==(200,420)
    for a,b in zip(path,path[1:]):
        if a[0]==b[0]:
            assert not (middle[0]<a[0]<middle[2] and min(a[1],b[1])<middle[3] and max(a[1],b[1])>middle[1])
        else:
            assert not (middle[1]<a[1]<middle[3] and min(a[0],b[0])<middle[2] and max(a[0],b[0])>middle[0])


def test_paper_visual_bad_types_return_validation_errors(client):
    pid = project(client)
    for spec in ({'kind':[]},{**TEMPLATES['flowchart'],'nodes':[{'id':'a','label':'甲','shape':[]}]},{**TEMPLATES['three_line_table'],'headers':'bad'}):
        response = client.post(f'/api/v1/projects/{pid}/paper-visuals/preview',json={'spec':spec})
        assert response.status_code==422,response.text


def test_paper_visual_worker_budget_and_paid_invalid_output_are_preserved(client,monkeypatch):
    from app.summary_billing import BudgetExceeded
    pid = project(client)
    task = client.post(f'/api/v1/projects/{pid}/paper-visuals/ai-tasks',json={'kind':'er','instruction':'按实际已知实体作图'}).json()
    def blocked(*args,**kwargs):
        raise BudgetExceeded('项目预算不足')
    monkeypatch.setattr(tasks,'model_call',blocked)
    assert tasks.run_one()
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='budget_blocked'


def test_paper_visual_candidate_rejects_wrong_kind(client,monkeypatch):
    pid = project(client)
    task = client.post(f'/api/v1/projects/{pid}/paper-visuals/ai-tasks',json={'kind':'er','instruction':'根据实际数据实体作图'}).json()
    monkeypatch.setattr(tasks,'model_call',lambda *a,**k:{'text':db.dumps({'spec':TEMPLATES['flowchart'],'missing_materials':[]})})
    with pytest.raises(ValueError,match='类型与请求不一致'):
        generate_candidate(task)
