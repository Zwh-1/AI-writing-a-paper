"""Bibliography navigation and ordered manuscript submission copies."""
from pathlib import Path
from zipfile import ZipFile
import json
import pytest
from lxml import etree as E
from docx import Document
from docx.oxml.ns import qn
from test_workbench import client,project,manual_package
from app import db,documents,exports
from app.docx_references import reference_anchor,navigation_report

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


@pytest.mark.parametrize('style',['numeric','author_year'])
def test_citations_link_to_exact_bookmarked_reference_and_keep_readable_text(client,monkeypatch,style):
    monkeypatch.setattr(exports,'renderer',lambda:None)
    pid=project(client);manual_package(client,pid)
    db.set_setting('format:'+pid,{'document_policy':{'heading_numbering':'native_multilevel'},'font_size':13})
    sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'第一章 研究背景','kind':'body'}).json()['id']
    refs=[client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':t,'authors':[a],'year':'2024'}}).json()['id']
          for t,a in [('档案治理','甲作者'),('凭证核验','乙作者')]]
    doc=documents.text_document('第一段：申请进入系统。')
    doc['content'][0]['content'] += [{'type':'citation','attrs':{'citationId':r}} for r in (refs[1],refs[0],refs[1])]
    doc['content'] += [
        {'type':'heading','attrs':{'level':2},'content':[{'type':'text','text':'1.1 授权核验'}]},
        {'type':'paragraph','content':[{'type':'text','text':'第二段：授权在返回前复核。'}]},
    ]
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None}).status_code==200
    result=exports.export_project(pid,{'citation_style':style})
    folder=exports.DATA/'exports'/result['export_id']
    with ZipFile(folder/'论文.docx') as archive:body=E.fromstring(archive.read('word/document.xml'))
    links=body.xpath('//w:hyperlink[starts-with(@w:anchor,"WBRef")]',namespaces=NS)
    assert [n.get(qn('w:anchor')) for n in links]==[reference_anchor(r) for r in (refs[1],refs[0],refs[1])]
    for i,node in enumerate(links):
        value=''.join(node.xpath('.//w:t/text()',namespaces=NS))
        if style=='numeric':assert value==('[2]' if i==1 else '[1]')
        else:assert value.startswith('（') and '2024' in value
        assert node.xpath('.//w:sz[@w:val="26"]',namespaces=NS)
        if style=='numeric':assert node.xpath('.//w:vertAlign[@w:val="superscript"]',namespaces=NS)
        anchor=node.get(qn('w:anchor'))
        start=body.xpath('//w:bookmarkStart[@w:name=$anchor]',namespaces=NS,anchor=anchor)
        assert len(start)==1
        bibliography=''.join(start[0].getparent().xpath('.//w:t/text()',namespaces=NS))
        assert ('甲作者' if i==1 else '乙作者') in bibliography
    report=navigation_report(folder/'论文.docx')
    assert report['citation_links']==3 and report['bibliography_bookmarks']==2 and not report['dangling_targets']
    # The simplified file exposes actual heading text without interpreting fields
    # or Word's automatic list numbering, and preserves both citation targets.
    with ZipFile(folder/'论文_检测提交版.docx') as archive:simple=E.fromstring(archive.read('word/document.xml'))
    alltext=''.join(simple.xpath('//w:t/text()',namespaces=NS))
    assert alltext.index('第一段：')<alltext.index('第二段：')<alltext.index('参考文献')
    assert '1 研究背景' in alltext and '1.1 授权核验' in alltext
    assert not simple.xpath('//w:instrText|//w:fldChar|//w:pPr/w:numPr',namespaces=NS)
    assert navigation_report(folder/'论文_检测提交版.docx')['citation_links']==3
    text=(folder/'论文_检测提交版.txt').read_text('utf-8')
    assert all(token in text for token in ('第一段：申请进入系统。','第二段：授权在返回前复核。','甲作者','乙作者'))
    metadata=json.loads((folder/'检查报告.json').read_text('utf-8'))
    assert metadata['submission_copy']['platform_detection_result']=='not_run'
    with ZipFile(folder/'论文交付包.zip') as archive:
        assert archive.read('论文_检测提交版.docx')==(folder/'论文_检测提交版.docx').read_bytes()
        assert archive.read('论文_检测提交版.txt')==(folder/'论文_检测提交版.txt').read_bytes()


def test_submission_does_not_drop_table_contents_or_notes(client,monkeypatch):
    monkeypatch.setattr(exports,'renderer',lambda:None)
    pid=project(client);manual_package(client,pid)
    sid=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'系统设计','kind':'system_design'}).json()['id']
    doc={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'表格之前的正文。'}]},
      {'type':'table','attrs':{'caption':'需求与依据','note':'来源为固定版本。'},'content':[
       {'type':'tableRow','content':[{'type':'tableHeader','content':[{'type':'paragraph','content':[{'type':'text','text':'需求'}]}]},
          {'type':'tableHeader','content':[{'type':'paragraph','content':[{'type':'text','text':'依据'}]}]}]},
       {'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'返回前复核'}]}]},
          {'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'授权服务的检查分支'}]}]}]}]},
      {'type':'paragraph','content':[{'type':'text','text':'表格之后的解释。'}]}]}
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None}).status_code==200
    result=exports.export_project(pid,{})
    folder=exports.DATA/'exports'/result['export_id']
    simple=Document(folder/'论文_检测提交版.docx')
    assert len(simple.tables)==1 and simple.tables[0].cell(1,1).text=='授权服务的检查分支'
    text=(folder/'论文_检测提交版.txt').read_text('utf-8')
    assert text.index('表格之前')<text.index('需求\t依据')<text.index('返回前复核\t授权服务的检查分支')<text.index('表格之后')
    assert '注：来源为固定版本。' in text


def test_submission_copy_can_be_explicitly_disabled(client,monkeypatch):
    monkeypatch.setattr(exports,'renderer',lambda:None)
    pid=project(client);manual_package(client,pid)
    result=exports.export_project(pid,{'include_submission_copy':False})
    assert not (exports.DATA/'exports'/result['export_id']/'论文_检测提交版.docx').exists()


def test_reference_bookmark_ids_are_safe_stable_and_unique():
    ids=['文献 一 / 2','../reference','A'*500,'文献 一 / 3']
    anchors=[reference_anchor(v) for v in ids]
    assert len(set(anchors))==len(ids)
    assert all(a.isalnum() and a[0].isalpha() and len(a)<=40 for a in anchors)
    assert reference_anchor(ids[0])==anchors[0]


def test_saved_office_hyperlink_fields_remain_valid_and_missing_links_fail(tmp_path):
    from app.docx_references import bookmark_reference
    doc=Document();anchor=reference_anchor('reference')
    exports.field(doc.add_paragraph('正常正文'),f' HYPERLINK \\l "{anchor}" ', '[1]')
    bibliography=doc.add_paragraph('[1] 作者. 研究题名.');bookmark_reference(bibliography,'reference')
    path=tmp_path/'saved.docx';doc.save(path)
    report=navigation_report(path,1,1)
    assert report['citation_links']==1 and report['field_links']==1
    assert report['element_links']==0 and not report['dangling_targets']
    with pytest.raises(ValueError,match='正文引用链接数量'):navigation_report(path,2,1)


def test_linear_submission_keeps_list_numbers_and_merged_cell_content(tmp_path):
    from app.export_submission import write_submission
    def cell(value,**attrs):return {'type':'tableCell','attrs':attrs,'content':[{'type':'paragraph','content':[{'type':'text','text':value}]}]}
    document={'type':'doc','content':[
      {'type':'orderedList','attrs':{'start':3},'content':[
       {'type':'listItem','content':[{'type':'paragraph','content':[{'type':'text','text':'读取申请'}]}]},
       {'type':'listItem','content':[{'type':'paragraph','content':[{'type':'text','text':'检查授权'}]}]}]},
      {'type':'table','attrs':{'caption':'合并表格'},'content':[
       {'type':'tableRow','content':[cell('共同对象',rowspan=2),cell('第一行')]},
       {'type':'tableRow','content':[cell('第二行')]}]}]}
    report=write_submission(tmp_path,{'title':'测试'},[{'section':{'id':'s','title':'设计','kind':'body'},'document':document}],
        {},{},{},{},[],{'latin_font':'Times New Roman','font':'宋体','font_size':12},'numeric',exports.bibliography_text)
    text=(tmp_path/'论文_检测提交版.txt').read_text('utf-8')
    assert '3. 读取申请' in text and '4. 检查授权' in text
    table=Document(tmp_path/'论文_检测提交版.docx').tables[0]
    assert table.cell(1,0).text=='共同对象' and table.cell(1,1).text=='第二行'
    assert report['tables']==1
