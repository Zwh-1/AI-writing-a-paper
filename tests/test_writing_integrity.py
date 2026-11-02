import io
import json
import zipfile
from pathlib import Path
import pytest
from docx import Document
from docx.oxml.ns import qn
from test_workbench import client,project,section,manual_package,save,file
from test_writing_agent import env
from app import db,documents,exports,provider,tasks,security,rules,knowledge_service
from app import writing_live
from app.paper_format import resolve


def no_literature(monkeypatch):
    monkeypatch.setattr(knowledge_service,'prepare_writing',lambda *a,**k:{'sources':[],'knowledge_units':[],
        'selected_unit_ids':[],'gaps':[],'document_readings':[]})


def generate(env,monkeypatch,visual=False):
    # This fixture never contacts a paid provider; budget mechanics are tested
    # separately from the application's invoice-evidence gate.
    db.set_setting('expense_policy:'+env['pid'],{'require_pricing_basis':False,'block_on_unresolved':False})
    no_literature(monkeypatch)
    captured={}
    def call(system,user,images=None):
        c=db.loads(user);captured.update(c)
        ids=[e['id'] for e in c['project_evidence']]
        data={'paragraphs':[{'text':'只有发行方且开关启用时才允许执行操作，源码观察不代表测试已经通过。','evidence_ids':ids}]}
        if visual:
            data['visuals']=[{'after_paragraph':0,'evidence_ids':ids,'explanation':'表中条件来自当前源码的合取判定，不能据此声称测试执行成功。',
                'spec':{'kind':'three_line_table','title':'操作授权条件','headers':['主体','必要条件'],
                        'rows':[['发行方','开关启用']],'note':'静态实现观察，尚未证明运行结果'}}]
        return {'text':db.dumps(data),'usage':{},'cost':0,'remote_id':'fixture','request_hash':'fixture'}
    monkeypatch.setattr(provider,'generate',call)
    task=tasks.enqueue(env['pid'],'write',{'section_id':env['sid'],'base_revision_id':None,'operation':'generate'})
    assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='succeeded',saved['error']
    return saved,db.loads(saved['result']),captured


def test_ordinary_writer_receives_bound_project_code_and_persists_evidence_links(env,monkeypatch):
    task,result,context=generate(env,monkeypatch)
    assert context['thesis_core']['research_question']=='如何准确描述功能的授权条件？'
    item=next(e for e in context['project_evidence'] if e['id']==env['eid'])
    assert 'issuer && enabled' in item['payload']['text']
    assert env['eid'] in result['document']['content'][0]['attrs']['evidenceIds']
    accepted=env['client'].post(f'/api/v1/sections/{env["sid"]}/accept',json={'task_id':task['id'],'base_revision_id':None})
    assert accepted.status_code==200,accepted.text
    assert db.one("SELECT * FROM dependencies WHERE revision_id=? AND upstream_type='evidence'",(accepted.json()['id'],))
    assert db.one("SELECT * FROM object_dependencies WHERE upstream_type='evidence' AND upstream_id=?",(env['eid'],))


def test_project_source_and_format_changes_invalidate_a_prepared_candidate(env,monkeypatch):
    task,result,_=generate(env,monkeypatch)
    db.set_setting('format:'+env['pid'],{'font_size':14})
    response=env['client'].post(f'/api/v1/sections/{env["sid"]}/accept',json={'task_id':task['id'],'base_revision_id':None})
    assert response.status_code==409
    assert not db.rows('SELECT * FROM revisions')


def test_reference_edits_invalidate_ordinary_candidates_and_existing_support(env,monkeypatch):
    ref=env['client'].post(f'/api/v1/projects/{env["pid"]}/references',json={'metadata':{'title':'原文题名','year':'2025'}}).json()['id']
    task,result,_=generate(env,monkeypatch)
    assert env['client'].patch(f'/api/v1/references/{ref}',json={'metadata':{'title':'已修改题名','year':'2026'}}).status_code==200
    rejected=env['client'].post(f'/api/v1/sections/{env["sid"]}/accept',json={'task_id':task['id'],'base_revision_id':None})
    assert rejected.status_code==409
    doc=documents.text_document('已经采用的引文。');doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':ref}})
    current=documents.save(security.require_section(env['sid']),doc,None)
    db.execute("UPDATE citations SET support='human_confirmed' WHERE revision_id=?",(current['id'],))
    assert env['client'].patch(f'/api/v1/references/{ref}',json={'metadata':{'title':'再次修改原件信息','year':'2026'}}).status_code==200
    assert db.one('SELECT support FROM citations WHERE revision_id=?',(current['id'],))['support']=='unknown'
    assert security.require_section(env['sid'])['stale']


def test_model_visual_becomes_an_editable_source_linked_table(env,monkeypatch):
    task,result,_=generate(env,monkeypatch,visual=True)
    tables=[n for n in result['document']['content'] if n['type']=='table']
    assert len(tables)==1 and tables[0]['attrs']['tableStyle']=='three-line'
    asset=db.one('SELECT * FROM artifacts WHERE id=?',(tables[0]['attrs']['artifactId'],))
    meta=db.loads(asset['metadata'])
    assert meta['writing_task_id']==task['id'] and env['eid'] in meta['evidence_ids']
    response=env['client'].post(f'/api/v1/sections/{env["sid"]}/accept',json={'task_id':task['id'],'base_revision_id':None})
    assert response.status_code==200,response.text
    assert 'table' in {n['type'] for n in documents.nodes(db.loads(response.json()['document']))}


def test_same_length_material_edit_is_visible_across_windows(client):
    pid=project(client);f=file(client,pid,'研究材料甲，保留其条件。')
    first=client.get(f'/api/v1/projects/{pid}/writing-live?stamp=true').json()['version']
    source=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    db.execute('UPDATE sources SET text=? WHERE id=?',(source['text'].replace('甲','乙'),source['id']))
    second=client.get(f'/api/v1/projects/{pid}/writing-live?stamp=true').json()['version']
    assert first!=second
    other=project(client,'另一个项目')
    before=client.get(f'/api/v1/projects/{other}/writing-live?stamp=true').json()['version']
    db.execute('UPDATE sources SET text=? WHERE id=?',(source['text'],source['id']))
    assert client.get(f'/api/v1/projects/{other}/writing-live?stamp=true').json()['version']==before


def test_repository_refresh_reads_current_files_without_expanding_authorized_scope(env):
    original=env['snapshot']['id']
    (env['root']/'gate.ts').write_text('export const updatedGate = true;\n',encoding='utf-8')
    assert writing_live.repository_status(env['pid'])['changed']
    result=writing_live.refresh_repository(env['pid'])
    assert result['refreshed'] and result['snapshot_id']!=original
    assert db.one('SELECT target_snapshot_id FROM writing_project_state WHERE project_id=?',(env['pid'],))['target_snapshot_id']==result['snapshot_id']
    assert not db.one("SELECT relative_path FROM repository_snapshot_files WHERE snapshot_id=? AND relative_path='.env'",(result['snapshot_id'],))
    assert not writing_live.repository_status(env['pid'])['changed']


def test_live_reference_numbers_include_table_cells_in_document_order(client):
    pid=project(client);sid=section(client,pid)
    refs=[client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':t}}).json()['id'] for t in ('文献甲','文献乙')]
    doc=documents.text_document('正文先引文献乙。')
    doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':refs[1]}})
    doc['content'].append({'type':'table','content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'citation','attrs':{'citationId':refs[0]}}]}]}]}]})
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None}).status_code==200
    live=client.get(f'/api/v1/projects/{pid}/writing-live').json()
    assert live['citation_numbers']=={refs[1]:1,refs[0]:2}


def test_published_format_rules_are_resolved_without_a_separate_apply_click(client):
    pid=project(client);manual_package(client,pid)
    client.post(f'/api/v1/projects/{pid}/rules',json={'statement':'正文14pt','category':'format','modality':'mandatory','scope':'global','checker':'format_value','value':{'key':'font_size','expected':14}})
    rules.publish(pid)
    assert resolve(pid)['config']['font_size']==14
    assert client.get(f'/api/v1/projects/{pid}/writing-live').json()['format']['config']['font_size']==14
    assert exports.format_config(pid)['font_size']==14


@pytest.mark.parametrize('values',[{'font_size':0},{'line_spacing':22},{'font_size':14,'line_spacing_points':10},{'margin_left':100,'margin_right':100}])
def test_format_editor_rejects_values_that_hide_or_clip_content(client,values):
    pid=project(client)
    assert client.put(f'/api/v1/projects/{pid}/format',json=values).status_code==422


def test_textual_format_rules_apply_to_their_objects_and_create_numbered_sections(client):
    pid=project(client);manual_package(client,pid)
    specifications=[('global','论文正文内容两端对齐，中文字体宋体小四号、西文字体Times New Roman小四号，固定行距22pt，段首缩进2字符。','只针对正文'),
        ('global','每章标题使用大纲级别一级，居中对齐，小三号宋体加粗。','只针对章标题'),
        ('global','从摘要页到目录结束页，页码使用罗马数字Ⅰ、Ⅱ、Ⅲ居中。正文页码使用阿拉伯数字1、2、3居中。','仅前置页与正文页码'),
        ('global','摘要不上目录；现在我们统一是阿拉伯数字（空格）标题名。','只针对目录及标题编号'),
        ('abstract','中文摘要内容使用小四号、楷体、两端对齐，首行缩进2字符，固定行距22pt。','仅针对中文摘要正文'),
        ('abstract','“摘要”两字使用小三号、黑体、居中对齐。','仅针对中文摘要标题'),
        ('abstract','“【关键词】”三个字使用小四号黑体加方括号，段首缩进2字符。','只针对关键词标签')]
    for scope,statement,note in specifications:
        response=client.post(f'/api/v1/projects/{pid}/rules',json={'statement':statement,'category':'format','scope':scope,'modality':'recommended','conditions':{'field':'operation','op':'in','value':['generate','revise','polish','expand','compress','translate'],'scope_note':note}})
        assert response.status_code==200,response.text
    rules.publish(pid)
    abstract=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'摘要','kind':'abstract'}).json()['id']
    body=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'第一章 功能设计','kind':'system_design'}).json()['id']
    assert save(client,abstract,'只用于验证排版对象。\n关键词：资料；图表').status_code==200
    assert save(client,body,'本段是正文样例。').status_code==200
    assert resolve(pid)['config']['font']=='宋体'
    assert resolve(pid,section=security.require_section(abstract))['config']['font']=='楷体'
    result=exports.export_project(pid,{})
    path=exports.DATA/'exports'/result['export_id']/'论文.docx';doc=Document(path)
    assert len(doc.sections)==3
    assert doc.sections[1]._sectPr.find(qn('w:pgNumType')).get(qn('w:fmt'))=='upperRoman'
    assert doc.sections[2]._sectPr.find(qn('w:pgNumType')).get(qn('w:start'))=='1'
    assert doc.styles['Normal'].paragraph_format.line_spacing.pt==22
    heading=next(p for p in doc.paragraphs if p.text=='摘要')
    assert heading.style.name=='Abstract Heading' and heading.alignment==1
    paragraph=next(p for p in doc.paragraphs if p.text=='只用于验证排版对象。')
    assert paragraph.runs[0]._r.get_or_add_rPr().find(qn('w:rFonts')).get(qn('w:eastAsia'))=='楷体'
    keywords=next(p for p in doc.paragraphs if p.text.startswith('【关键词】'))
    assert keywords.runs[0]._r.get_or_add_rPr().find(qn('w:rFonts')).get(qn('w:eastAsia'))=='黑体'
    assert keywords.runs[1]._r.get_or_add_rPr().find(qn('w:rFonts')).get(qn('w:eastAsia'))=='楷体'
    assert any(p.text=='1 功能设计' for p in doc.paragraphs)


def export_fixture(client):
    pid=project(client,'项目依据 图表引用与格式验证');manual_package(client,pid)
    db.set_setting('figure_policy:'+pid,{'numbering':'chapter'})
    sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'第三章 系统设计','kind':'system_design'}).json()['id']
    ref=client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'原始文献标题','authors':['张三'],'year':'2024'}}).json()['id']
    doc=documents.text_document('表中列出设计约束，文献引用应保留在单元格中。')
    doc['content'][0]['content'] += [{'type':'crossReference','attrs':{'targetSectionId':sid,'targetId':'table-main'}}]
    cell=lambda text,header=False:{'type':'tableHeader' if header else 'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':text}]}]}
    citation_cell=cell('保留文献与格式 ')
    citation_cell['content'][0]['content'] += [{'type':'text','text':'关键条件','marks':[{'type':'bold'},{'type':'underline'}]},
        {'type':'citation','attrs':{'citationId':ref}}]
    doc['content'].append({'type':'table','attrs':{'nodeId':'table-main','caption':'项目设计条件','tableStyle':'three-line','note':'本表为设计条件整理，不代表实际测试执行结果。'},'content':[
        {'type':'tableRow','content':[cell('设计维度',True),cell('依据与限制',True)]},
        {'type':'tableRow','content':[cell('授权约束'),citation_cell]},
        {'type':'tableRow','content':[{'type':'tableCell','attrs':{'colspan':2},'content':[{'type':'paragraph','content':[{'type':'text','text':'合并单元格内容完整保留。'}]}]}]}]})
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None}).status_code==200
    client.patch(f'/api/v1/references/{ref}',json={'metadata':{'title':'已更新的正确文献标题','authors':['张三'],'year':'2025'}})
    result=exports.export_project(pid,{})
    return pid,sid,exports.DATA/'exports'/result['export_id']/'论文.docx'


def test_export_preserves_rich_table_citations_spans_and_latest_reference_metadata(client):
    pid,sid,path=export_fixture(client)
    doc=Document(path);table=doc.tables[0]
    texts='\n'.join(p.text for row in table.rows for cell in row.cells for p in cell.paragraphs)
    assert '[1]' in texts and '[cite:' not in texts
    assert table.rows[2].cells[0]._tc is table.rows[2].cells[1]._tc
    assert any(run.bold and run.underline for row in table.rows for cell in row.cells for p in cell.paragraphs for run in p.runs)
    all_text='\n'.join(p.text for p in doc.paragraphs)
    assert '已更新的正确文献标题' in all_text and '第三章 系统设计' in all_text
    with zipfile.ZipFile(path) as z:
        xml=z.read('word/document.xml').decode()
        assert 'TOC ' in xml and ' REF fig_' in xml and '表 3-1' in xml


def test_export_figure_numbering_keeps_an_unsaved_parent_chapter(client):
    from app.figure_standards import inventory
    pid=project(client);manual_package(client,pid)
    parent=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'第四章 系统设计','kind':'system_design'}).json()['id']
    child=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'4.1 约束关系','kind':'system_design','parent_id':parent}).json()['id']
    doc={'type':'doc','content':[{'type':'table','attrs':{'caption':'条件整理'},'content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'条件'}]}]}]}]}]}
    assert client.put(f'/api/v1/sections/{child}/content',json={'document':doc,'base_revision_id':None}).status_code==200
    db.set_setting('figure_policy:'+pid,{'numbering':'chapter'})
    expected=inventory(pid)[0]['label'];result=exports.export_project(pid,{})
    with zipfile.ZipFile(exports.DATA/'exports'/result['export_id']/'论文.docx') as z:
        assert '4-' in z.read('word/document.xml').decode()
    assert expected=='表 4-1'


def test_fixed_text_line_spacing_does_not_clip_inline_figures(client):
    from PIL import Image
    pid=project(client);manual_package(client,pid);sid=section(client,pid)
    db.set_setting('format:'+pid,{'line_spacing_points':22})
    data=io.BytesIO();Image.new('RGB',(300,180),'white').save(data,format='PNG');sha=security.put_blob(data.getvalue())
    asset=db.uid();db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(asset,pid,'图片行距校验','figure',sha,db.dumps({'egress':'selected_content','figure_purpose':'design_schematic'}),db.now()))
    doc=documents.text_document('本图仅用于验证图片段落的行距。');doc['content'].append({'type':'figure','attrs':{'artifactId':asset,'caption':'图片行距校验'}})
    assert client.put(f'/api/v1/sections/{sid}/content',json={'base_revision_id':None,'document':doc}).status_code==200
    result=exports.export_project(pid,{})
    exported=Document(exports.DATA/'exports'/result['export_id']/'论文.docx')
    assert exported.styles['Normal'].paragraph_format.line_spacing.pt==22
    picture=next(p for p in exported.paragraphs if p._p.find('.//'+qn('w:drawing')) is not None)
    assert picture.paragraph_format.line_spacing==1.0


def test_automatic_literature_skips_oversized_first_document_and_reads_a_complete_alternative(client):
    from app.document_preparation import prepare
    pid=project(client);sid=section(client,pid)
    long=file(client,pid,'长文不可截断。'*1000);short=file(client,pid,'短文保留完整方法、条件与局限。')
    findings={'items':[{'file_id':long['id'],'name':long['name']},{'file_id':short['id'],'name':short['name']}]}
    result=prepare(pid,'研究约束',sid,findings=findings,max_bytes=1500)
    assert any(s['file_id']==short['id'] for s in result['sources'])
    assert all(s['file_id']!=long['id'] for s in result['sources'])
    assert result['sources'][0]['text']=='短文保留完整方法、条件与局限。'


def test_reused_visual_proposal_does_not_duplicate_a_preserved_table(env,monkeypatch):
    task,result,_=generate(env,monkeypatch,visual=True)
    accepted=env['client'].post(f'/api/v1/sections/{env["sid"]}/accept',json={'task_id':task['id'],'base_revision_id':None})
    assert accepted.status_code==200,accepted.text
    next_task=tasks.enqueue(env['pid'],'write',{'section_id':env['sid'],'operation':'generate','base_revision_id':accepted.json()['id']})
    assert tasks.run_one()
    actual=db.one('SELECT * FROM tasks WHERE id=?',(next_task['id'],))
    assert actual['status']=='succeeded',actual['error']
    result=db.loads(actual['result'])
    assert sum(n['type']=='table' for n in result['document']['content'])==1
    assert db.one("SELECT COUNT(*) n FROM artifacts WHERE kind='table'")['n']==1


def test_request_scope_caches_repeated_reads_without_leaking_mutable_data(client,monkeypatch):
    from app import knowledge_units as units,knowledge_lineage as lineage
    pid=project(client);original=file(client,pid,'保留原文条件，不得假称测试通过。')
    parsed=units.build(pid,original['id']);uid=parsed['units'][0]['id']
    calls=[];uncached=units._get_uncached
    def counted(*args,**kwargs):calls.append(args[1]);return uncached(*args,**kwargs)
    monkeypatch.setattr(units,'_get_uncached',counted)
    with lineage.validation_scope():
        first=units.read(pid,uid,before=0,after=0)
        original_text=first['text'];first['text']='本次调用者的修改不能写入缓存'
        assert units.read(pid,uid,before=0,after=0)['text']==original_text
    assert calls==[uid]
    units.read(pid,uid,before=0,after=0)
    assert calls==[uid,uid]


def test_failed_permission_checks_are_rechecked_after_the_request_scope_ends(client):
    from app import knowledge_lineage as lineage
    from fastapi import HTTPException
    pid=project(client);original=file(client,pid,'明确的原文依据。')
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    with lineage.validation_scope():
        for _ in range(2):
            with pytest.raises(HTTPException):lineage.version_of(pid,'file',original['id'],remote=True)
    db.execute("UPDATE files SET egress='selected_content' WHERE id=?",(original['id'],))
    with lineage.validation_scope():assert lineage.version_of(pid,'file',original['id'],remote=True)


def test_pdf_fallback_has_real_contents_entries_and_preserves_table_citations(client,monkeypatch):
    from app.pdf_render import render_pdf
    from app.export_checks import freeze
    from app.figure_standards import inventory
    import pypdfium2 as pdfium
    pid,sid,path=export_fixture(client);frozen=freeze(pid)
    snapshots=[{'section':s,'revision':frozen['revisions'][s['id']],'document':db.loads(frozen['revisions'][s['id']]['document'])} for s in frozen['sections'] if s['id'] in frozen['revisions']]
    numbering={ref:1 for ref in frozen['references']};rows=inventory(pid,snapshots=snapshots,section_plan=frozen['sections'])
    pdf_path=path.with_name('独立PDF索引验收.pdf')
    render_pdf(pdf_path,frozen['project'],snapshots,frozen['references'],numbering,exports.format_config(pid),frozen['artifacts'],rows)
    pdf=pdfium.PdfDocument(str(pdf_path));texts=[pdf[i].get_textpage().get_text_range() for i in range(len(pdf))];pdf.close()
    combined='\n'.join(texts)
    assert '目录' in combined and combined.count('第三章 系统设计')>=2
    assert '[cite:' not in combined and '[1]' in combined
    assert '合并单元格内容完整保留' in combined
