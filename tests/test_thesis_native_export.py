"""Native heading/list semantics and page-aware chapter headers are export data."""
import io
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from test_workbench import client,project,manual_package
from app import db,exports,documents
from app.format_sections import body_headers,heading_text,toc_styles
from app.docx_vector import attach_svg


def test_native_heading_profile_numbers_three_levels_without_typing_numbers(client):
    pid=project(client);manual_package(client,pid)
    db.set_setting('format:'+pid,{'document_policy':{'heading_numbering':'native_multilevel','chapter_headers':True,'frontmatter_numbering':'upperRoman'}})
    sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'第一章 绪论','kind':'body'}).json()['id']
    data=documents.text_document('有实质内容的章正文。')
    data['content'] += [
        {'type':'heading','attrs':{'level':2},'content':[{'type':'text','text':'1.1 研究背景'}]},
        {'type':'paragraph','content':[{'type':'text','text':'二级标题包含相关问题的具体说明。'}]},
        {'type':'heading','attrs':{'level':3},'content':[{'type':'text','text':'1.1.1 档案流转问题'}]},
        {'type':'paragraph','content':[{'type':'text','text':'三级标题对应实质说明。'}]},
    ]
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':data,'base_revision_id':None}).status_code==200
    result=exports.export_project(pid,{'include_toc':True})
    path=exports.DATA/'exports'/result['export_id']/'论文.docx';doc=Document(path)
    for text,level in [('绪论',1),('研究背景',2),('档案流转问题',3)]:
        para=next(p for p in doc.paragraphs if p.text==text)
        assert para.style.name=='Heading '+str(level)
        assert para._p.pPr.numPr.ilvl.val==level-1
        assert para._p.pPr.numPr.numId.val>0
    bibliography=next(p for p in doc.paragraphs if p.text=='参考文献')
    assert bibliography._p.pPr.numPr.numId.val==0
    with zipfile.ZipFile(path) as archive:
        numbering=archive.read('word/numbering.xml').decode()
        assert '%1.%2.%3' in numbering and 'multilevel' in numbering
        body=archive.read('word/document.xml').decode()
        assert 'TOC \\o "1-3"' in body
    assert doc.sections[-1].header.paragraphs[0].text=='测试论文'
    assert not any('STYLEREF' in (n.text or '') for n in doc.sections[-1].header._element.iter(qn('w:instrText')))
    assert any(' STYLEREF 1 ' in (n.text or '') for n in doc.sections[-1].even_page_header._element.iter(qn('w:instrText')))


def test_headers_use_primary_odd_and_even_stories_without_number_switch():
    doc=Document()
    body_headers(doc,doc.sections[0],{'chapter_headers':True},'论文总题名',exports.field,'系统设计')
    even=' '.join(n.text or '' for n in doc.sections[0].even_page_header._element.iter(qn('w:instrText')))
    assert even==' STYLEREF 1 ' and '\\n' not in even
    assert doc.sections[0].header.paragraphs[0].text=='论文总题名'
    assert doc.sections[0].even_page_header.paragraphs[0].text=='系统设计'
    assert doc.settings.element.find(qn('w:evenAndOddHeaders')) is not None


@pytest.mark.parametrize('value,level,expected',[
    ('第四章 系统设计',1,'系统设计'),('4 系统设计',1,'系统设计'),
    ('4.1 总体架构',2,'总体架构'),('4.1.1 分层职责',3,'分层职责'),
    ('ZKP 证明边界',2,'ZKP 证明边界'),
])
def test_heading_text_removes_only_heading_number_prefix(value,level,expected):
    assert heading_text(value,level)==expected


def test_svg_is_embedded_as_vector_with_png_fallback(tmp_path):
    fallback=tmp_path/'fallback.png';Image.new('RGB',(20,20),'white').save(fallback)
    doc=Document();shape=doc.add_picture(str(fallback))
    attach_svg(doc,shape,b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><path d="M0 0L20 20"/></svg>')
    out=io.BytesIO();doc.save(out)
    with zipfile.ZipFile(out) as archive:
        assert any(n.endswith('.svg') for n in archive.namelist())
        assert any(n.endswith('.png') for n in archive.namelist())
        assert 'svgBlip' in archive.read('word/document.xml').decode()


def test_external_svg_resources_are_rejected(tmp_path):
    fallback=tmp_path/'fallback.png';Image.new('RGB',(20,20),'white').save(fallback)
    doc=Document();shape=doc.add_picture(str(fallback))
    with pytest.raises(ValueError):
        attach_svg(doc,shape,b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/image.png"/></svg>')


def test_real_builtin_toc_styles_receive_verified_two_four_char_indents():
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Pt
    doc=Document();fmt={'font':'宋体','latin_font':'Times New Roman','font_size':12}
    components={f'toc_{i}':{'font_size':14 if i==1 else 12,'left_indent_chars':2*(i-1),'line_spacing_points':22} for i in (1,2,3)}
    for i in (1,2,3):
        style=doc.styles.add_style('toc '+str(i),WD_STYLE_TYPE.PARAGRAPH,builtin=True)
        style.font.size=Pt(components[f'toc_{i}']['font_size'])
    toc_styles(doc,150,fmt,components)
    for i in (1,2,3):
        style=doc.styles['toc '+str(i)]
        assert style.element.pPr.ind.get(qn('w:leftChars'))==str((i-1)*200)
        assert style.element.pPr.ind.get(qn('w:left'))==str((i-1)*480)
        assert style.paragraph_format.line_spacing.pt==22
        assert style.element.pPr.tabs is not None


def test_revision_component_overrides_and_bibliography_wrapping_are_exported(client):
    pid=project(client);manual_package(client,pid)
    db.set_setting('format:'+pid,{'component_styles':{'heading_1':{'font':'宋体','font_size':16},'references_body':{'left_indent_chars':2,'hanging_chars':2}}})
    sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'系统设计','kind':'system_design'}).json()['id']
    rid=client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'原文题名','authors':['作者'],'year':'2024','citation_raw':'作者.  题名[J].\n期刊, 2024. DOI:10.1234/test'}}).json()['id']
    data=documents.text_document('具体论述');data['content'][0]['content'].append({'type':'citation','attrs':{'citationId':rid}})
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':data,'base_revision_id':None}).status_code==200
    result=exports.export_project(pid,{})
    doc=Document(exports.DATA/'exports'/result['export_id']/'论文.docx')
    assert doc.styles['Heading 1'].font.size.pt==16
    assert doc.styles['Heading 1'].element.rPr.rFonts.get(qn('w:eastAsia'))=='宋体'
    reference=next(p for p in doc.paragraphs if p.text.startswith('[1] 作者.'))
    assert reference.alignment==0 and '\n' not in reference.text and '  ' not in reference.text
    assert reference._p.pPr.ind.get(qn('w:hangingChars'))=='200'
    assert reference._p.pPr.find(qn('w:wordWrap')).get(qn('w:val'))=='true'
    assert reference.runs[0]._r.rPr.find(qn('w:spacing')).get(qn('w:val'))=='0'


def test_toc_tab_setup_preserves_verified_typography_and_two_character_indents():
    from docx.shared import Pt
    from docx.enum.style import WD_STYLE_TYPE
    doc=Document()
    for level in (1,2,3):doc.styles.add_style('TOC '+str(level),WD_STYLE_TYPE.PARAGRAPH)
    doc.styles['TOC 1'].font.size=Pt(14);doc.styles['TOC 1'].font.bold=True
    toc_styles(doc,155,{'font':'宋体','latin_font':'Times New Roman','font_size':12})
    # The legacy custom style is normalized to Word's canonical TOC style.
    assert doc.styles['toc 1'].font.size.pt==14 and doc.styles['toc 1'].font.bold is True
    assert doc.styles['toc 2'].paragraph_format.left_indent.pt==24
    assert doc.styles['toc 3'].paragraph_format.left_indent.pt==48


def test_verified_school_reference_override_precedes_default_hanging_format(client):
    pid=project(client);manual_package(client,pid)
    db.set_setting('format:'+pid,{'document_policy':{'heading_numbering':'native_multilevel'},'component_styles':{
        'heading_1':{'font':'宋体','font_size':16},
        'reference_heading':{'font':'宋体','font_size':18,'bold':True,'alignment':'center','line_spacing_points':22,'space_before_lines':0.5,'space_after_lines':0.5},
        'references_body':{'font_size':10.5,'line_spacing_points':18,'alignment':'justify','left_indent_chars':0,'first_line_chars':2,'hanging_chars':0}}})
    sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'系统设计','kind':'system_design'}).json()['id']
    rid=client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'原文题名','authors':['作者'],'year':'2024'}}).json()['id']
    data=documents.text_document('具体论述');data['content'][0]['content'].append({'type':'citation','attrs':{'citationId':rid}})
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':data,'base_revision_id':None}).status_code==200
    result=exports.export_project(pid,{})
    doc=Document(exports.DATA/'exports'/result['export_id']/'论文.docx')
    heading=next(p for p in doc.paragraphs if p.text=='参考文献')
    assert heading.style.name=='Heading 1' and heading._p.pPr.numPr.numId.val==0
    assert heading.alignment==1 and heading.runs[0].font.size.pt==18 and heading.runs[0].bold is True
    assert heading.paragraph_format.line_spacing.pt==22
    assert heading.paragraph_format.space_before.pt==11 and heading.paragraph_format.space_after.pt==11
    reference=next(p for p in doc.paragraphs if p.text.startswith('[1] 作者.'))
    assert reference.alignment==3 and reference.runs[0].font.size.pt==10.5
    assert reference.paragraph_format.line_spacing.pt==18
    assert reference._p.pPr.ind.get(qn('w:firstLineChars'))=='200'
    assert reference._p.pPr.ind.get(qn('w:hangingChars')) is None
    assert reference._p.pPr.ind.get(qn('w:hanging')) is None


def test_normalize_retains_new_export_attributes_and_absent_ui_defaults():
    data={'type':'doc','content':[
        {'type':'figure','attrs':{'artifactId':'image','svgSha256':'a'*64}},
        {'type':'table','attrs':{'columnWidths':[1,2.5],'fontSize':10.5,'bodyAlignment':'left'},'content':[
            {'type':'tableRow','content':[{'type':'tableCell'},{'type':'tableCell'}]}]},
        {'type':'table','attrs':{'columnWidths':None,'fontSize':None,'bodyAlignment':None}}]}
    normalized=documents.normalize(data)
    assert normalized['content'][0]['attrs']['svgSha256']=='a'*64
    assert normalized['content'][1]['attrs']['columnWidths']==[1,2.5]
    assert normalized['content'][1]['attrs']['fontSize']==10.5
    assert normalized['content'][1]['attrs']['bodyAlignment']=='left'
    assert 'fontSize' not in normalized['content'][2]['attrs']


@pytest.mark.parametrize('typ,attrs',[
    ('figure',{'svgSha256':'not-a-sha'}),('paragraph',{'svgSha256':'a'*64}),
    ('table',{'columnWidths':[0,1]}),('table',{'columnWidths':[float('inf')]}),('table',{'columnWidths':[float('nan')]}),
    ('table',{'columnWidths':[True]}),('table',{'fontSize':5}),('table',{'fontSize':float('nan')}),('table',{'bodyAlignment':'stretch'}),
    ('paragraph',{'columnWidths':[1]}),
])
def test_normalize_rejects_invalid_export_attributes(typ,attrs):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):documents.normalize({'type':typ,'attrs':attrs})


def test_export_cover_note_is_validated_and_does_not_print_internal_mode(client):
    pid=project(client);manual_package(client,pid)
    result=exports.export_project(pid,{'cover_note':'论文修订稿　2026年11月4日'})
    doc=Document(exports.DATA/'exports'/result['export_id']/'论文.docx')
    assert any(p.text=='论文修订稿　2026年11月4日' for p in doc.paragraphs)
    assert not any('稿件用途：draft' in p.text for p in doc.paragraphs)
    with pytest.raises(ValueError):exports.export_project(pid,{'cover_note':'有换行\n说明'})


def test_custom_chapter_style_uses_its_name_while_built_in_styles_use_numeric_selector():
    doc=Document()
    body_headers(doc,doc.sections[0],{'chapter_headers':True,'chapter_style':'Paper Chapter Heading'},'题名',exports.field,'系统设计')
    codes=' '.join(n.text or '' for n in doc.sections[0].even_page_header._element.iter(qn('w:instrText')))
    assert codes==' STYLEREF "Paper Chapter Heading" '


@pytest.mark.parametrize('enabled',[True,False])
def test_bibliography_hyphenation_is_explicit_and_does_not_change_body(client,enabled):
    pid=project(client);manual_package(client,pid)
    style={'font_size':10.5,'line_spacing_points':18,'alignment':'justify','first_line_chars':2,'hanging_chars':0}
    if enabled:style['auto_hyphenation']=True
    db.set_setting('format:'+pid,{'component_styles':{'references_body':style}})
    sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'系统设计','kind':'system_design'}).json()['id']
    rid=client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'Interoperability of educational credentials','authors':['An Author'],'year':'2024'}}).json()['id']
    data=documents.text_document('This body prose keeps its existing hyphenation policy.');data['content'][0]['content'].append({'type':'citation','attrs':{'citationId':rid}})
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':data,'base_revision_id':None}).status_code==200
    result=exports.export_project(pid,{})
    doc=Document(exports.DATA/'exports'/result['export_id']/'论文.docx')
    reference=next(p for p in doc.paragraphs if p.text.startswith('[1] An Author.'))
    assert reference.alignment==3 and reference.runs[0].font.size.pt==10.5
    assert reference.paragraph_format.line_spacing.pt==18 and reference._p.pPr.ind.get(qn('w:firstLineChars'))=='200'
    setting=doc.settings.element.find(qn('w:autoHyphenation'))
    suppress=reference._p.pPr.find(qn('w:suppressAutoHyphens'))
    normal_suppress=doc.styles['Normal'].element.pPr.find(qn('w:suppressAutoHyphens'))
    if enabled:
        assert setting.get(qn('w:val'))=='true'
        assert doc.settings.element.find(qn('w:hyphenationZone')).get(qn('w:val'))=='0'
        assert suppress.get(qn('w:val'))=='0' and normal_suppress.get(qn('w:val'))=='1'
        assert reference.runs[0]._r.rPr.find(qn('w:lang')).get(qn('w:val'))=='en-US'
    else:
        assert setting is None and suppress is None and normal_suppress is None
