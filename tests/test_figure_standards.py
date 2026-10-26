import copy
import io
import zipfile
import pytest
from docx import Document
from PIL import Image
from app import db,documents,exports,security,image_generation
from app.paper_visuals import TEMPLATES,validate_spec,create_visual
from app.paper_visual_render import render_diagram
from app.figure_standards import inventory,evaluate,save_policy
from test_workbench import client,project,section,save


@pytest.mark.parametrize('kind',['architecture','module','class_diagram','sequence','data_flow','contract_interaction','circuit','structure'])
def test_extra_types_validate_render_and_keep_source(client,kind):
    pid=project(client);spec=validate_spec(TEMPLATES[kind]);data,size=render_diagram(spec)
    with Image.open(io.BytesIO(data)) as image:assert image.size==size and min(size)>=1080
    asset=create_visual(pid,{'spec':spec,'explanation':'图中各节点对应给定结构，连线说明实际设计中的关系。'})
    assert asset['metadata']['spec']==spec and asset['insert_nodes'][1]['attrs']['describesFigureId']==asset['node']['attrs']['nodeId']
    assert client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download').json()==spec


def test_er_requires_relationships_and_module_hierarchy_has_no_cycle():
    with pytest.raises(ValueError,match='关系'):validate_spec({**TEMPLATES['er'],'edges':[]})
    spec=copy.deepcopy(TEMPLATES['module']);spec['edges'].append({'source':'profile','target':'system'})
    with pytest.raises(ValueError,match='循环'):validate_spec(spec)


def test_data_flow_table_and_explanation_are_grounded_in_spec(client):
    pid=project(client)
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals/data-flow-table',json={'spec':TEMPLATES['data_flow']})
    assert result.status_code==200
    assert result.json()['spec']['headers']==['数据流名称','起点','终点','主要处理步骤']
    assert result.json()['spec']['rows'][0]==['输入数据','用户','校验处理','检查必填项与数据格式']
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals/explanation-draft',json={'spec':TEMPLATES['architecture']}).json()
    assert result['needs_review'] and result['method']=='deterministic_spec'
    for label in ('用户界面','业务服务','数据库'):assert label in result['explanation']
    assert '825' not in result['explanation'] and '251' not in result['explanation']


def adopted(client,pid,sid,kind='architecture',explanation='本图包含用户界面、业务服务与数据库，各层通过请求和读写形成交互。'):
    asset=create_visual(pid,{'spec':TEMPLATES[kind],'explanation':explanation})
    sec=security.require_section(sid);document={'type':'doc','content':asset['insert_nodes']}
    revision=client.put(f'/api/v1/sections/{sid}/content',json={'document':document,'base_revision_id':sec['current_revision_id']})
    assert revision.status_code==200,revision.text
    return asset,revision.json()


def test_explanation_is_a_real_linked_paragraph_and_review_expires(client):
    pid=project(client);sid=section(client,pid);asset,rev=adopted(client,pid,sid)
    document=db.loads(rev['document']);fig,paragraph=document['content']
    assert fig['attrs']['explanationNodeId']==paragraph['attrs']['nodeId']
    assert paragraph['attrs']['describesFigureId']==fig['attrs']['nodeId']
    assert not any(x['code']=='EXPLANATION_MISSING' for x in evaluate(pid)['items'])
    review=client.post(f'/api/v1/sections/{sid}/figures/{fig["attrs"]["nodeId"]}/review',json={'revision_id':rev['id'],'note':'逐项核对图中元素、连线和说明文字。'})
    assert review.status_code==200,review.text
    assert next(x for x in evaluate(pid)['items'] if x['code']=='EXPLANATION_SEMANTICS')['result']=='pass'
    paragraph['content'][0]['text']='修改说明后必须重新核对。'
    changed=client.put(f'/api/v1/sections/{sid}/content',json={'document':document,'base_revision_id':rev['id']})
    assert changed.status_code==200
    assert next(x for x in evaluate(pid)['items'] if x['code']=='EXPLANATION_SEMANTICS')['result']=='unknown'


def test_link_existing_paragraph_uses_optimistic_version_and_project_scope(client):
    pid=project(client);sid=section(client,pid)
    asset=create_visual(pid,{'spec':TEMPLATES['flowchart']})
    doc={'type':'doc','content':[asset['node'],*documents.text_document('先校验输入，再保存有效记录；无效输入返回修正。')['content']]}
    rev=client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None}).json();nodes=db.loads(rev['document'])['content']
    body={'base_revision_id':rev['id'],'figure_node_id':nodes[0]['attrs']['nodeId'],'paragraph_node_id':nodes[1]['attrs']['nodeId']}
    assert client.put(f'/api/v1/sections/{sid}/figure-links',json=body).status_code==200
    assert client.put(f'/api/v1/sections/{sid}/figure-links',json=body).status_code==409
    other=project(client,'其他项目');other_sid=section(client,other)
    forged={'type':'doc','content':[{'type':'paragraph','content':[{'type':'crossReference','attrs':{'targetSectionId':other_sid,'targetId':nodes[0]['attrs']['nodeId']}}]}]}
    latest=security.require_section(sid)['current_revision_id']
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':forged,'base_revision_id':latest}).status_code==422


def test_figure_without_paragraph_exposes_revision_for_metadata_edit(client):
    pid=project(client);sid=section(client,pid)
    asset=create_visual(pid,{'spec':TEMPLATES['architecture']})
    rev=client.put(f'/api/v1/sections/{sid}/content',json={'document':{'type':'doc','content':[asset['node']]},'base_revision_id':None}).json()
    links=client.get(f'/api/v1/projects/{pid}/figure-links').json()
    assert not links['paragraphs']
    figure=links['figures'][0]
    assert figure['revision_id']==rev['id']
    endpoint=f'/api/v1/sections/{sid}/figures/{figure["node_id"]}/metadata'
    body={'revision_id':figure['revision_id'],'figure_purpose':'design_schematic','caption':'已核对的分层架构图'}
    response=client.put(endpoint,json=body)
    assert response.status_code==200,response.text
    assert db.loads(response.json()['document'])['content'][0]['attrs']['caption']==body['caption']
    assert client.put(endpoint,json=body).status_code==409
    assert any(item['code']=='EXPLANATION_MISSING' for item in evaluate(pid)['items'])


@pytest.mark.parametrize('purpose',['original_result','experimental_result','interface_screenshot','test_capture'])
def test_ai_original_results_and_screenshots_are_blocked_before_key_or_request(client,monkeypatch,purpose):
    pid=project(client)
    monkeypatch.setattr(image_generation,'request_image',lambda *a,**k:pytest.fail('protected image must not reach API'))
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json={'kind':'architecture','instruction':'绘制结果','figure_purpose':purpose})
    assert response.status_code==403,response.text
    assert not db.rows('SELECT * FROM generation_runs') and not db.rows('SELECT * FROM tasks')


def test_forbidden_project_policy_and_research_exception_require_evidence(client):
    pid=project(client);save_policy(pid,{'policy':{'ai_image_policy':'forbidden'}})
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json={'kind':'sequence','instruction':'绘制时序'})
    assert response.status_code==403
    response=client.put(f'/api/v1/projects/{pid}/figure-standards',json={'policy':{'ai_image_policy':'research_method'}})
    assert response.status_code==422 and '可复现' in response.text


def test_required_chapter_diagrams_and_dangling_refs_are_reported(client):
    pid=project(client);sid=section(client,pid)
    save_policy(pid,{'policy':{'required_diagrams':[{'section_id':sid,'kind':'architecture'}]}})
    assert any(x['code']=='PLANNED_DIAGRAM_MISSING' for x in evaluate(pid)['items'])
    doc=documents.text_document('参见');doc['content'][0]['content'].append({'type':'crossReference','attrs':{'targetId':'missing','targetSectionId':sid}})
    response=client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None});assert response.status_code==200
    assert any(x['code']=='REFERENCE_DANGLING' and x['severity']=='blocker' for x in evaluate(pid)['items'])


def test_chapter_numbering_word_seq_ref_directories_and_markdown_agree(client,monkeypatch):
    pid=project(client)
    sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'第三章 系统设计','kind':'system_design'}).json()['id']
    next_sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'第四章 系统实现','kind':'implementation'}).json()['id']
    save_policy(pid,{'policy':{'numbering':'chapter','caption_space':False,'include_directory':True}})
    asset,rev=adopted(client,pid,sid)
    second,other=adopted(client,pid,next_sid,'sequence','用户向前端发出请求，前端调用后端并将返回结果交给用户。')
    doc=db.loads(other['document']);doc['content'].append({'type':'paragraph','content':[{'type':'text','text':'总体设计见'},{'type':'crossReference','attrs':{'targetId':asset['node']['attrs']['nodeId'],'targetSectionId':sid}}]})
    assert client.put(f'/api/v1/sections/{next_sid}/content',json={'document':doc,'base_revision_id':other['id']}).status_code==200
    rows=inventory(pid);assert [r['label'] for r in rows]==['图3-1','图4-1']
    monkeypatch.setattr(exports,'renderer',lambda:None)
    result=exports.export_project(pid,{'mode':'draft','pdf':False});folder=exports.DATA/'exports'/result['export_id']
    exported=Document(folder/'论文.docx');text='\n'.join(p.text for p in exported.paragraphs)
    assert '图3-1 分层架构设计示例' in text and '图4-1 业务请求时序示例' in text
    assert '总体设计见图3-1' in text and '插图目录' in text
    with zipfile.ZipFile(folder/'论文.docx') as z:
        xml=z.read('word/document.xml').decode()
        assert 'SEQ Figure' in xml and ' REF fig_' in xml and 'bookmarkStart' in xml and '\\c "Figure"' in xml
    markdown=(folder/'论文.md').read_text(encoding='utf-8')
    assert '图3-1' in markdown and '图4-1' in markdown and '总体设计见图3-1' in markdown


def test_reference_and_explanation_checks_do_not_invent_semantic_verification(client):
    pid=project(client);sid=section(client,pid);asset,rev=adopted(client,pid,sid)
    report=evaluate(pid)
    assert next(x for x in report['items'] if x['code']=='EXPLANATION_SEMANTICS')['result']=='unknown'
    assert report['source_status']=='summary_requires_original_verification'


def test_writing_preserves_linked_explanation_and_cross_reference():
    from app.writing_agent.drafting import document
    original=documents.text_document('待改写段落')
    explanation={'type':'paragraph','attrs':{'nodeId':'explain','describesFigureId':'figure'},'content':[{'type':'text','text':'图中元素与关系的说明。'}]}
    reference={'type':'paragraph','attrs':{'nodeId':'reference'},'content':[{'type':'text','text':'参见'},{'type':'crossReference','attrs':{'nodeId':'xref','targetId':'figure','targetSectionId':'chapter'}}]}
    original['content'] += [explanation,reference]
    rewritten,_=document({'paragraphs':[{'local_id':'p1','text':'已改写段落','reference_ids':[],'evidence_ids':[]}],'missing_materials':[]},current=original)
    assert explanation in rewritten['content'] and reference in rewritten['content']
    assert '[figref:chapter:figure]' in documents.plain(reference)


def test_manual_review_expires_when_layout_settings_change(client):
    pid=project(client);sid=section(client,pid);asset,rev=adopted(client,pid,sid)
    node=asset['node']['attrs']['nodeId']
    assert client.post(f'/api/v1/sections/{sid}/figures/{node}/review',json={'revision_id':rev['id'],'note':'核对图中元素与说明和当前版式一致。'}).status_code==200
    db.set_setting('format:'+pid,{'font_size':14})
    assert next(x for x in evaluate(pid)['items'] if x['code']=='EXPLANATION_SEMANTICS')['result']=='unknown'


def test_annotation_retry_does_not_duplicate_or_reuse_original_save_id(client):
    pid=project(client);asset=create_visual(pid,{'spec':TEMPLATES['architecture'],'request_id':'source-request'})
    body={'explanation':'表现层、业务层和数据层通过有名称的请求与读写交互。','request_id':'annotation-request'}
    first=client.post(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/annotate',json=body).json()
    second=client.post(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/annotate',json=body).json()
    assert first['id']==second['id'] and first['id']!=asset['id']
    assert create_visual(pid,{'spec':TEMPLATES['architecture'],'request_id':'source-request'})['id']==asset['id']


def test_research_method_exception_requires_body_statement_and_real_material(client):
    pid=project(client);sid=section(client,pid)
    statement='本研究使用生成式图像模型作为研究方法，保存输入、参数与输出以便复核。'
    save(client,sid,'普通设计说明，不包含所登记的方法正文。')
    uploaded=client.post(f'/api/v1/projects/{pid}/artifacts',files={'file':('method.py',b'print("reproduce")','text/plain')},data={'kind':'code'}).json()
    body={'policy':{'ai_image_policy':'research_method','method_section_id':sid,'method_statement':statement,'reproducibility_artifact_ids':[uploaded['id']]}}
    assert client.put(f'/api/v1/projects/{pid}/figure-standards',json=body).status_code==422
    current=security.require_section(sid)['current_revision_id'];save(client,sid,statement,current)
    assert client.put(f'/api/v1/projects/{pid}/figure-standards',json=body).status_code==200
