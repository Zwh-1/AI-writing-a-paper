"""End-to-end boundaries missing from the first official-plan implementation."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import pytest
from fastapi import HTTPException
from test_workbench import client,project,manual_package,save,file
from test_writing_continuity import chapter
from app import db,security,documents,tasks,provider
from app.writing_continuity import collect
from app import chapter_reading,skill_runtime
from test_writing_flow_integration import writing_project,response

CASES=json.loads((Path(__file__).parent/'fixtures/context-mode-cases.v1.json').read_text(encoding='utf-8'))['cases']


def running(pid,kind='write',**payload):
    task=tasks.enqueue(pid,kind,payload)
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    return db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))


def test_reading_uses_exact_paragraph_ids_and_keeps_unread_scope(client):
    pid=project(client);first=chapter(client,pid,'背景','不请求的原文。\n样本数为120人。');sid=chapter(client,pid,'讨论','当前正文。')
    context,manifest,_=collect(pid,sid,mode='hierarchical_v1')
    rev=documents.current(security.require_section(first));node=db.loads(rev['document'])['content'][1]['attrs']['nodeId']
    rows=chapter_reading.expand(pid,context,manifest,[{'section_id':first,'revision_id':rev['id'],'node_ids':[node],'reason':'核对样本量'}])
    assert rows[0]['text']=='样本数为120人。' and rows[0]['paragraphs'][0]['paragraph_index']==1
    assert rows[0]['partial'] and not context['paper_context']['full_manuscript_provided']
    assert security.safe_object(manifest['chapter_readings'][0]['content_hash']).read_text(encoding='utf-8')==rows[0]['text']
    assert context['manuscript_anchor_index']['entries'][0]['mentions'][0]['value']=='120'
    bad=deepcopy(context);m=deepcopy(manifest)
    with pytest.raises(HTTPException):chapter_reading.expand(pid,bad,m,[{'section_id':first,'revision_id':rev['id'],'node_ids':['outside'],'reason':'读取'}])
    assert bad==context and m==manifest
    assert save(client,first,'新正文。',rev['id']).status_code==200
    with pytest.raises(HTTPException):chapter_reading.expand(pid,context,manifest,[{'section_id':first,'revision_id':rev['id'],'reason':'旧版本'}])


def test_chapter_reader_revalidates_permission_and_rejects_other_project(client,monkeypatch):
    from app.writing_agent import provenance
    pid=project(client);a=chapter(client,pid,'方法','完整原文');b=chapter(client,pid,'结果','结果')
    context,manifest,_=collect(pid,b,mode='hierarchical_v1');revision=security.require_section(a)['current_revision_id']
    def revoked(*args):raise HTTPException(403,'撤权')
    monkeypatch.setattr(provenance,'revision_permissions',revoked)
    with pytest.raises(HTTPException) as error:chapter_reading.expand(pid,context,manifest,[{'section_id':a,'revision_id':revision,'reason':'核对'}])
    assert error.value.status_code==403 and not context.get('chapter_readings')
    other=project(client,'其他项目');outside=chapter(client,other,'外部','其他正文')
    with pytest.raises(HTTPException):chapter_reading.expand(pid,context,manifest,[{'section_id':outside,'revision_id':revision,'reason':'越界'}])


def test_reading_phase_is_bounded_and_budgeted_before_draft(client,monkeypatch):
    from app import writing_flow
    pid=project(client);a=chapter(client,pid,'背景','完整原文。'*30);b=chapter(client,pid,'讨论','当前正文。')
    task=running(pid,section_id=b);context,manifest,_=collect(pid,b,mode='hierarchical_v1')
    requests=[{'section_id':a,'revision_id':security.require_section(a)['current_revision_id'],'reason':'核对方法边界'}]
    calls=[]
    def response(*args,**kw):
        calls.append(kw['index'])
        return {'text':db.dumps({'chapter_read_requests':requests if len(calls)==1 else []}),'run_id':'fixture'}
    monkeypatch.setattr(writing_flow,'_cached_call',response)
    monkeypatch.setattr(writing_flow,'_stage_budget',lambda *a:{'input_upper_bound':100,'reserved_output':100,'context_limit':10000})
    state={'context':context,'manifest':manifest,'system':'写作','format_repair_input_reserve':100}
    result=chapter_reading.prepare(task,security.require_section(b),state)
    assert calls==[0,1] and result['context']['chapter_readings'][0]['text']=='完整原文。'*30
    assert len(result['manifest']['chapter_reading_trace'])==2
    context,manifest,_=collect(pid,b,mode='hierarchical_v1');calls.clear()
    monkeypatch.setattr(writing_flow,'_stage_budget',lambda *a:{'input_upper_bound':10000,'reserved_output':100,'context_limit':10000})
    with pytest.raises(HTTPException) as error:chapter_reading.prepare(task,security.require_section(b),{**state,'context':context,'manifest':manifest})
    assert error.value.detail['code']=='CHAPTER_READING_CONTEXT_EXCEEDED' and calls==[0]


def test_non_adjacent_numeric_claims_get_cross_chapter_originals(client):
    from app.manuscript_review import prepare,cross_section_windows
    pid=project(client);manual_package(client,pid)
    a=chapter(client,pid,'方法','样本数为120人。');chapter(client,pid,'设计','本章只讨论设计。');chapter(client,pid,'实现','本章只讨论实现。');b=chapter(client,pid,'结果','样本数为150人。')
    chunks,_=prepare(pid,100000,1000)
    windows=cross_section_windows(chunks,100000,1000)
    pair=next(w for w in windows if {r['section']['id'] for r in w['sections']}=={a,b})
    assert {'样本数为120人。','样本数为150人。'}=={p['text'] for s in pair['sections'] for p in s['paragraphs']}


def test_navigation_preserves_indices_after_empty_and_nonparagraph_blocks():
    from app.project_memory import chapter_navigation
    document={'content':[{'type':'paragraph','attrs':{'nodeId':'empty'},'content':[]},
        {'type':'table','content':[]},{'type':'paragraph','attrs':{'nodeId':'p2'},'content':[{'type':'text','text':'样本数为120人。'}]}]}
    nav=chapter_navigation({'id':'s','title':'章'},{'id':'r','text':'样本数为120人。','document':db.dumps(document)})
    assert nav['extracts']==[{'paragraph_index':2,'node_id':'p2','text':'样本数为120人。'}]


def test_skill_resources_reuse_prompt_and_freeze_content_and_standards(client):
    from app.ai_runtime.policy import frozen
    pid=project(client);task=running(pid,'support_check')
    snap=frozen(task)['skills'];loaded=skill_runtime.load(snap,'academic-expression','references/expression.txt')
    from app.config import ROOT
    assert loaded['text']==(ROOT/'prompts'/'academic-expression.v1.txt').read_text(encoding='utf-8')
    system,manifest=skill_runtime.enrich(task,'system','',{'stage':'draft'})
    row=next(r for r in manifest['skills_usage'] if r['skill_id']=='academic-expression')
    assert loaded['text'] in system and row['resources'][0]['hash']==loaded['content_hash']
    assert row['project_standards']['version_hash'] and row['validators']['section_candidate']=='schemas.SectionCandidate'
    assert skill_runtime.validate_output(task,{'paragraphs':[{'text':'正文。'}]},'section_candidate')['status']=='pass'
    with pytest.raises(ValueError):skill_runtime.validate_output(task,{'paragraphs':[]},'section_candidate')


def data_file(client,pid,contents):
    result=client.post(f'/api/v1/projects/{pid}/files',files={'file':('data.csv',contents.encode(),'text/csv')})
    assert result.status_code==200,result.text
    return result.json()['file']['id']


def test_grouped_histogram_shared_bins_and_frozen_recalculation(client):
    pid=project(client);fid=data_file(client,pid,'value,group\n1,A\n2,A\n100,B\n120,B\nbad,B\ninf,B\n')
    result=client.post(f'/api/v1/projects/{pid}/charts',json={'file_id':fid,'column':'value','group_column':'group','bins':3})
    assert result.status_code==200,result.text
    asset=result.json();meta=asset['metadata'];hist=list(meta['data_processing']['histograms'].values())
    assert hist[0]['bin_edges']==hist[1]['bin_edges'] and sum(sum(r['counts']) for r in hist)==4
    source=client.get(f'/api/v1/projects/{pid}/charts/{asset["id"]}/download?format=source').json()
    assert [r['data_row'] for r in source['recipe']['rows']]==[1,2,3,4]
    assert [r['data_row'] for r in source['recipe']['excluded_rows']]==[5,6]
    assert meta['font_provenance']['files'][0]['sha256'] and meta['font_provenance']['files'][0]['version']
    assert client.get(f'/api/v1/projects/{pid}/charts/{asset["id"]}/verification').json()['histogram_counts_match']
    replay=client.post(f'/api/v1/projects/{pid}/charts/{asset["id"]}/reproduce',json={})
    assert replay.status_code==200,replay.text
    assert replay.json()['metadata']['recipe_sha256']==meta['recipe_sha256']
    assert replay.json()['metadata']['data_processing']==meta['data_processing']
    assert client.post(f'/api/v1/projects/{project(client,"其他")}/charts/{asset["id"]}/reproduce',json={}).status_code==404
    if os.environ.get('OFFICIAL_CONTEXT_REPORT'):
        folder=Path(os.environ['OFFICIAL_CONTEXT_REPORT']);folder.mkdir(parents=True,exist_ok=True)
        (folder/'grouped-histogram.png').write_bytes(security.safe_object(asset['sha256']).read_bytes())
        (folder/'grouped-histogram-source.json').write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding='utf-8')


def test_categorical_bar_and_intervals_do_not_silently_change_meaning(client):
    pid=project(client);fid=data_file(client,pid,'category,y,g,lo,hi\n猫,3,A,2,4\n狗,8,A,7,9\n猫,6,B,5,7\n狗,9,B,8,10\n')
    base={'file_id':fid,'column':'y','x_column':'category','group_column':'g','kind':'bar','lower_column':'lo','upper_column':'hi'}
    result=client.post(f'/api/v1/projects/{pid}/charts',json=base)
    assert result.status_code==200,result.text
    source=client.get(f'/api/v1/projects/{pid}/charts/{result.json()["id"]}/download?format=source').json()
    assert [r['x'] for r in source['recipe']['rows']]==['猫','狗','猫','狗']
    assert source['recipe']['rows'][1]['upper']==9
    assert client.post(f'/api/v1/projects/{pid}/charts',json={**base,'kind':'histogram'}).status_code==422
    assert client.post(f'/api/v1/projects/{pid}/charts',json={**base,'kind':'scatter'}).status_code==422
    fid2=data_file(client,pid,'category,y\n猫,3\n猫,5\n')
    assert client.post(f'/api/v1/projects/{pid}/charts',json={'file_id':fid2,'column':'y','x_column':'category','kind':'bar'}).status_code==422


def test_xlsx_sheet_and_selected_source_rows_are_frozen(client):
    import pandas as pd
    pid=project(client);stream=io.BytesIO()
    with pd.ExcelWriter(stream,engine='openpyxl') as writer:
        pd.DataFrame({'y':[999]}).to_excel(writer,sheet_name='wrong',index=False)
        pd.DataFrame({'y':[10,20,30,40]}).to_excel(writer,sheet_name='chosen',index=False)
    fid=client.post(f'/api/v1/projects/{pid}/files',files={'file':('sheets.xlsx',stream.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}).json()['file']['id']
    result=client.post(f'/api/v1/projects/{pid}/charts',json={'file_id':fid,'column':'y','sheet_name':'chosen','row_start':2,'row_end':3})
    assert result.status_code==200,result.text
    source=client.get(f'/api/v1/projects/{pid}/charts/{result.json()["id"]}/download?format=source').json()['recipe']
    assert source['sheet_name']=='chosen' and [r['y'] for r in source['rows']]==[20,30] and [r['data_row'] for r in source['rows']]==[2,3]


def test_visual_receipt_replays_asset_despite_new_document_node_ids(client,monkeypatch):
    from app import writing_visuals,paper_visuals
    pid=project(client);sid=chapter(client,pid,'方法','正文')
    task=running(pid,section_id=sid);section=security.require_section(sid)
    proposal={'after_paragraph':0,'spec':deepcopy(paper_visuals.TEMPLATES['flowchart']),'explanation':'流程说明。','evidence_ids':['e1']}
    evidence=[{'id':'e1','text':'已提供的流程设计依据'}]
    def content():return [{'type':'paragraph','attrs':{'nodeId':db.uid()},'content':[{'type':'text','text':'方法说明。'}]}]
    first,ids=writing_visuals.compile_proposals(task,section,[proposal],content(),evidence)
    assert len(ids)==1
    monkeypatch.setattr(paper_visuals,'create_visual',lambda *a,**k:pytest.fail('receipt must avoid rendering again'))
    second,again=writing_visuals.compile_proposals(task,section,[proposal],content(),evidence)
    assert again==ids
    assert db.one("SELECT count(*) n FROM artifacts WHERE project_id=?",(pid,))['n']==1
    with pytest.raises(HTTPException) as error:writing_visuals.compile_proposals(task,section,[proposal],content(),[{'id':'e1','text':'来源实际内容已改变'}])
    assert error.value.detail['code']=='AI_NODE_INPUT_CHANGED'


def test_experiment_freezes_both_modes_and_enforces_shared_budget(client,monkeypatch):
    from app.ai_runtime.context_experiments import create,guard
    from app.ai_runtime.policy import frozen
    from app import budget_usage
    from app.summary_billing import BudgetExceeded
    pid=project(client);manual_package(client,pid);sid=chapter(client,pid,'方法','样本数为120人。')
    value=create(pid,{'section_id':sid,'budget_limit':.5})
    paired=[db.one('SELECT * FROM tasks WHERE id=?',(identity,)) for identity in value['task_ids']]
    policies=[frozen(t) for t in paired]
    assert {p['writing_flow_policy']['context_mode'] for p in policies}=={'full_manuscript','hierarchical_v1'}
    policies[0]['writing_flow_policy'].pop('context_mode');policies[1]['writing_flow_policy'].pop('context_mode')
    assert policies[0]==policies[1]
    guard(paired[0],.2)
    monkeypatch.setattr(budget_usage,'charge_tasks',lambda *a,**k:.4)
    with pytest.raises(BudgetExceeded):guard(paired[1],.2)
    rev=security.require_section(sid)['current_revision_id'];save(client,sid,'正文改变。',rev)
    with pytest.raises(HTTPException) as error:guard(paired[0])
    assert error.value.detail['code']=='CONTEXT_EXPERIMENT_INPUT_CHANGED'


def test_human_assessment_is_version_bound_and_unknown_is_not_zero(client):
    from app.ai_runtime.context_experiments import assess,assessment
    from app.ai_request_cache import digest
    pid=project(client);task=tasks.enqueue(pid,'write',{'section_id':'fixture'})
    result={'text':'待人工评价的正文。'}
    db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",(db.dumps(result),task['id']))
    body={'task_id':task['id'],'result_hash':digest(result),'assessor':'本地测试评价者','supported_citations':0,
          'checked_citations':0,'omission_count':2,'author_edit_characters':50}
    saved=assess(pid,body)
    assert saved['citation_support_rate'] is None and saved['omission_count']==2
    assert assessment(pid,task['id'],result)==saved
    assert assessment(pid,task['id'],{'text':'已修改正文。'}) is None
    with pytest.raises(HTTPException):assess(pid,{**body,'supported_citations':1})
    with pytest.raises(HTTPException):assess(pid,{**body,'result_hash':'changed'})


def test_word_continuation_preserves_data_and_repeats_title_and_headers(tmp_path):
    from docx import Document
    from app.table_continuation import split_word,validate_breaks
    from app.academic_standards import check_docx,resolve
    doc=Document();doc.add_paragraph('表 1 测试表')
    table=doc.add_table(rows=6,cols=1)
    for i,row in enumerate(table.rows):row.cells[0].text='表头' if i==0 else '数据'+str(i)
    source=[{'type':'tableRow','content':[{'type':'tableHeader' if i==0 else 'tableCell','attrs':{}}]} for i in range(6)]
    split_word(doc,table,source,[3,5],'表 1 测试表')
    path=tmp_path/'continued.docx';doc.save(path)
    loaded=Document(path)
    assert [[c.text for row in t.rows for c in row.cells] for t in loaded.tables]==[['表头','数据1','数据2'],['表头','数据3','数据4'],['表头','数据5']]
    assert [p.text for p in loaded.paragraphs].count('表 1 测试表（续）')==2
    source[2]['content'][0]['attrs']['rowspan']=2
    with pytest.raises(HTTPException):validate_breaks(source,[3])
    # A missing figure caption must produce a finding, not disappear.
    from app.paper_visuals import TEMPLATES
    from app.paper_visual_render import render_diagram
    image=tmp_path/'figure.png';image.write_bytes(render_diagram(TEMPLATES['flowchart'])[0]);doc.add_picture(str(image));doc.save(path)
    assert any(c['kind']=='figure_caption_below' and c['status']=='fail' for c in check_docx(path,resolve())['checks'])


def test_actual_pdf_splits_repeat_continuation_caption(tmp_path):
    from app.table_continuation import ContinuedTable
    from reportlab.platypus import SimpleDocTemplate,Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from app.paper_visual_render import _font
    from pypdf import PdfReader
    styles=getSampleStyleSheet();table=ContinuedTable([['Header'],*[[f'Row {i}'] for i in range(100)]],colWidths=[200],repeatRows=1)
    pdfmetrics.registerFont(TTFont('ContinuationTestChinese',str(_font(19).path),subfontIndex=0))
    caption_style=ParagraphStyle('ContinuationCaption',parent=styles['Normal'],fontName='ContinuationTestChinese')
    table.continuation_caption='Table 1 Values';table.caption_style=caption_style
    output=tmp_path/'continued.pdf';SimpleDocTemplate(str(output),pagesize=(300,400),topMargin=20,bottomMargin=20).build([Paragraph('Table 1 Values',styles['Normal']),table])
    pdf=PdfReader(output)
    assert len(pdf.pages)>2
    assert 'Table 1 Values' in pdf.pages[0].extract_text()
    assert all('Table 1 Values' in page.extract_text() and 'Header' in page.extract_text() for page in pdf.pages[1:])
    assert all('（续）' in page.extract_text() for page in pdf.pages[1:])
    if os.environ.get('OFFICIAL_CONTEXT_REPORT'):
        folder=Path(os.environ['OFFICIAL_CONTEXT_REPORT']);folder.mkdir(parents=True,exist_ok=True)
        (folder/'continued-table.pdf').write_bytes(output.read_bytes())
        import pypdfium2 as pdfium
        rendered=pdfium.PdfDocument(str(output))
        try:
            for index in range(len(rendered)):
                page=rendered[index];bitmap=page.render(scale=1.5)
                bitmap.to_pil().save(folder/f'continued-table-{index+1}.png')
                bitmap.close();page.close()
        finally:rendered.close()


def test_real_pipeline_pair_reads_frozen_chapter_and_compares_extra_calls(writing_project,monkeypatch):
    from app.ai_runtime.context_experiments import create
    from app.ai_runtime.evaluation import compare
    e=writing_project;pid=e['pid'];other=chapter(e['client'],pid,'另章','本章样本数为120人，保持原范围。')
    read_calls=[]
    def transport(system,user,images=None):
        if 'chapter_read_requests' in system:
            context=db.loads(user);read_calls.append(context)
            return response({'chapter_read_requests':[] if context.get('chapter_readings') else [
                {'section_id':other,'revision_id':security.require_section(other)['current_revision_id'],'reason':'核对跨章范围'}]})
        return e['transport'](system,user,images)
    monkeypatch.setattr(provider,'generate',transport)
    experiment=create(pid,{'section_id':e['sid'],'budget_limit':1})
    assert tasks.run_one() and tasks.run_one()
    stored=[db.one('SELECT * FROM tasks WHERE id=?',(identity,)) for identity in experiment['task_ids']]
    assert all(t['status']=='succeeded' for t in stored),[t['error'] for t in stored]
    hierarchical=db.loads(stored[1]['result'])
    assert hierarchical['manifest']['chapter_readings'][0]['section_id']==other
    draft=next(p for _,p in e['sent'] if p.get('chapter_readings') and p.get('operation')=='generate')
    assert draft['chapter_readings'][0]['text']=='本章样本数为120人，保持原范围。'
    assert not draft['paper_context']['full_manuscript_provided'] and not draft['paper_context']['complete']
    assert len(read_calls)==2
    actions={r['action_id'] for r in db.rows('SELECT action_id FROM ai_node_receipts WHERE task_id=?',(stored[1]['id'],))}
    assert {'retrieve_and_read_materials','chapter_reading','draft','source_and_numeric_checks'}<=actions
    from app.ai_runtime import evaluation
    original_digest=evaluation.digest;bases=[]
    def capture(value):
        if isinstance(value,dict) and set(value)=={'kind','parameters','sources'}:bases.append(deepcopy(value))
        return original_digest(value)
    monkeypatch.setattr(evaluation,'digest',capture)
    report=compare(pid,{'task_ids':experiment['task_ids'],'comparison_axis':'context_mode'})
    def differences(a,b,path=''):
        if type(a)!=type(b):return [(path,a,b)]
        if isinstance(a,dict):return [d for k in a.keys()|b.keys() for d in differences(a.get(k),b.get(k),path+'/'+str(k))]
        if isinstance(a,list) and len(a)==len(b):return [d for i,(x,y) in enumerate(zip(a,b)) for d in differences(x,y,path+'/'+str(i))]
        return [(path,a,b)] if a!=b else []
    assert len(report['paired_input_groups'])==1,differences(*bases)
    assert report['paired_input_groups'][0]['ready_for_human_comparison'],report['paired_input_groups']


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_frozen_context_acceptance_cases(client,case):
    from app.source_payload import token_upper_bound
    from app import project_memory as memory
    from app.writing_continuity import selection_context
    from app.writing_argument import scoped_context
    pid=project(client);source=chapter(client,pid,'既有章节',case['text']*case['repeat'])
    sid=chapter(client,pid,'本次章节',case.get('current_text','前文限定。\n只修改本段。\n后文限定。'))
    task=None
    if case['id']=='stale_navigation':
        memory.rebuild(pid);task=tasks.enqueue(pid,'write',{'section_id':sid})
        rev=security.require_section(source)['current_revision_id'];save(client,source,'新的研究范围。',rev)
    measurements={}
    for mode in ('full_manuscript','hierarchical_v1'):
        if case['id']=='revoked':
            with pytest.raises(HTTPException) as error:collect(pid,sid,mode=mode,permission_check=lambda rev:(False,set()))
            assert error.value.status_code==403
            measurements[mode]={'state':'permission_blocked','provider_input_tokens':None};continue
        payload,manifest,_=collect(pid,sid,mode=mode,task=task)
        assert manifest['related_revisions'][source]==security.require_section(source)['current_revision_id']
        if case['id']=='stale_navigation':assert all(source not in item.get('section_versions',{}) for item in payload.get('project_memory',{}).get('items',[]))
        if case['id']=='targeted_revision':
            revision=documents.current(security.require_section(sid));document=db.loads(revision['document']);target=document['content'][1]['attrs']['nodeId']
            boundary=selection_context(document,[target]);assert boundary['before'][0]['text']=='前文限定。' and boundary['after'][0]['text']=='后文限定。'
        if mode=='hierarchical_v1':
            assert source in {r['section_id'] for r in payload['section_catalogue']}
            assert not scoped_context(payload,mode)[0]['paper_context']['complete']
        measurements[mode]={'bytes':len(db.dumps(payload).encode()),'input_upper_bound':token_upper_bound('',db.dumps(payload)),
            'provided_section_ids':payload['paper_context'].get('provided_section_ids',[source,sid]),
            'provider_input_tokens':None,'human_omissions':None,'human_citation_support_rate':None,'author_edit_characters':None}
    if case['id']=='long':assert measurements['hierarchical_v1']['bytes']<measurements['full_manuscript']['bytes']
    output=os.environ.get('OFFICIAL_CONTEXT_REPORT')
    if output:
        folder=Path(output);folder.mkdir(parents=True,exist_ok=True)
        (folder/(case['id']+'.json')).write_text(json.dumps({'case':case,'measurements':measurements},ensure_ascii=False,indent=2),encoding='utf-8')
