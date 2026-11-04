"""The revised-delivery gate cannot inherit old hashes or fake superscripts."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from docx import Document

from app import exports
from app.format_sections import native_heading_numbering,number_heading,body_headers
from scripts.export_revised_candidate import manuscript_hash


@pytest.fixture
def verifier():
    path=Path(__file__).resolve().parents[1]/'scripts/verify_revised_delivery.py'
    spec=importlib.util.spec_from_file_location('revised_delivery_gate',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def synthetic_prepared():
    sections=[{'id':'abstract','title':'摘要','kind':'abstract','position':0},
              {'id':'english','title':'Abstract','kind':'abstract_en','position':1}]
    sections += [{'id':'s'+str(i),'title':f'第{i}章 章节{i}','kind':'body','position':i+1} for i in range(1,8)]
    references={'r'+str(i):{'id':'r'+str(i),'metadata':json.dumps({'title':f'真实身份测试题名{i}','year':'2024'})} for i in range(1,32)}
    revisions={s['id']:{'document':json.dumps({'type':'doc','content':[]})} for s in sections}
    revisions['s1']['document']=json.dumps({'type':'doc','content':[{'type':'paragraph','content':[
        {'type':'citation','attrs':{'citationId':'r'+str(i)}} for i in range(1,32)]}]})
    snapshot={'sections':sections,'references':references,'revisions':revisions}
    return {'snapshot':snapshot,'revision':{'paper_hash':manuscript_hash(snapshot)}}


def test_snapshot_uses_bibliography_identity_and_rejects_dangling_and_unused(verifier):
    prepared=synthetic_prepared();errors=[]
    frozen,metrics=verifier.snapshot_metrics(prepared,errors)
    assert not errors and metrics['references']==31 and metrics['citations']==31
    document=json.loads(frozen['revisions']['s1']['document']);document['content'][0]['content'][0]['attrs']['citationId']='file-id-r1'
    frozen['revisions']['s1']['document']=json.dumps(document)
    errors=[];verifier.snapshot_metrics(prepared,errors)
    assert any('悬空引用' in e for e in errors) and any('未在正文引用' in e for e in errors)


def test_docx_rejects_visual_text_that_is_not_true_superscript(verifier,tmp_path):
    prepared=synthetic_prepared();errors=[];frozen,metrics=verifier.snapshot_metrics(prepared,errors)
    metrics['citation_ids']=['r'+str(i) for i in range(1,32)]
    doc=Document();number=native_heading_numbering(doc)
    exports.field(doc.add_paragraph(),' TOC \\o "1-3" \\h \\z \\u ','目录缓存')
    for i in range(1,8):number_heading(doc.add_heading(f'章节{i}',1),number,1)
    citations=doc.add_paragraph()
    for i in range(1,32):citations.add_run(f'[{i}]').font.superscript=True
    doc.add_heading('参考文献',1)
    for i in range(1,32):doc.add_paragraph(f'[{i}] 作者. 文献{i}. 2024.')
    body_headers(doc,doc.sections[0],{'chapter_headers':True},'题名',exports.field,'章节1')
    path=tmp_path/'论文.docx';doc.save(path)
    result=verifier.inspect_docx(path,metrics,errors)
    assert not errors and result['native_superscript_markers']==31
    citations.runs[0].font.superscript=False;doc.save(path);errors=[]
    verifier.inspect_docx(path,metrics,errors)
    assert any('非上标' in e for e in errors) and any('数量/编号' in e for e in errors)


@pytest.mark.parametrize('footer,expected',[('I',('roman',1)),('IV',('roman',4)),('12',('decimal',12)),('',(None,None))])
def test_logical_page_numbers_are_not_physical_pdf_indices(verifier,footer,expected):
    assert verifier.logical_number(footer)==expected


def test_pdf_odd_header_check_uses_logical_page_number_after_frontmatter(verifier,monkeypatch,tmp_path):
    data=[{'header':'','footer':'','middle':'封面'}, {'header':'','footer':'I','middle':'摘要'}]
    for i in range(1,8):data.append({'header':f'章节{i}','footer':str(i),'middle':f'{i} 章节{i}\n'+''.join(f'[{n}]' for n in range(1,32))})
    data.append({'header':'参考文献','footer':'8','middle':'参考文献\n[1] 文献条目'})
    class TextPage:
        def __init__(self,row):self.row=row
        def get_text_range(self):return '\n'.join(self.row.values())
        def get_text_bounded(self,left,bottom,right,top):return self.row['footer' if bottom==0 else 'header' if bottom>700 else 'middle']
        def close(self):pass
    class Page:
        def __init__(self,row):self.row=row
        def get_size(self):return 595,842
        def get_textpage(self):return TextPage(self.row)
        def close(self):pass
    class PDF:
        def __init__(self,path):pass
        def __len__(self):return len(data)
        def __getitem__(self,index):return Page(data[index])
        def close(self):pass
    monkeypatch.setattr(verifier.pdfium,'PdfDocument',PDF)
    prepared=synthetic_prepared();errors=[];frozen,metrics=verifier.snapshot_metrics(prepared,errors)
    result=verifier.inspect_pdf(tmp_path/'论文.pdf',frozen,metrics,errors,[])
    assert not errors and result['page_checks'][2]['logical_page']==1
    # The PDF content stream places each next page's header/footer between the
    # two halves; body-region joining must still preserve the source passage.
    first='这是一段跨页的完整正文，其来源文字需要在两个页面之间连续核验，'
    second='而页眉和页脚不能插入正文造成虚假的内容缺失告警。'
    metrics['long_prose']=[first+second]
    data[2]['middle']+='\n'+first
    data[3]['middle']=second+'\n'+data[3]['middle'];errors=[]
    result=verifier.inspect_pdf(tmp_path/'论文.pdf',frozen,metrics,errors,[])
    assert not errors and not result['missing_prose']
    data[2]['header']='1 章节1';errors=[]
    verifier.inspect_pdf(tmp_path/'论文.pdf',frozen,metrics,errors,[])
    assert any('逻辑奇数页1' in e for e in errors)


def test_zip_rejects_stale_members_and_missing_delivery_files(verifier,tmp_path):
    (tmp_path/'论文.docx').write_bytes(b'docx');(tmp_path/'论文.pdf').write_bytes(b'pdf')
    (tmp_path/'修改报告.md').write_bytes(b'report')
    bundle=tmp_path/'论文交付包.zip'
    with zipfile.ZipFile(bundle,'w') as archive:
        archive.writestr('论文.docx',b'old docx');archive.writestr('论文.pdf',b'pdf')
    errors=[];verifier.inspect_zip(tmp_path,bundle,errors)
    assert any('磁盘最终版本不同' in e for e in errors) and any('未包含交付目录文件' in e for e in errors)


def test_visual_receipt_and_manuscript_hash_must_match_current_files(verifier,tmp_path,monkeypatch):
    prepared=synthetic_prepared();snapshot=tmp_path/'snapshot.json';snapshot.write_text(json.dumps(prepared),encoding='utf-8')
    delivery=tmp_path/'delivery';delivery.mkdir()
    for name in ('论文.docx','论文.pdf'):(delivery/name).write_bytes(name.encode())
    monkeypatch.setattr(verifier,'inspect_docx',lambda *args:{})
    monkeypatch.setattr(verifier,'inspect_pdf',lambda *args:{'pages':1})
    monkeypatch.setattr(verifier,'inspect_zip',lambda *args:{})
    receipt=tmp_path/'visual.json'
    receipt.write_text(json.dumps({'paper_hash':prepared['revision']['paper_hash'],'docx_sha256':verifier.sha(delivery/'论文.docx'),
        'pdf_sha256':verifier.sha(delivery/'论文.pdf'),'pages':[{'page':1,'inspected':True,'clean':True}]}),encoding='utf-8')
    args=SimpleNamespace(snapshot=snapshot,delivery_dir=delivery,visual_receipt=receipt,output=tmp_path/'audit.json')
    assert verifier.audit(args)['ready_for_review_delivery'] is True
    (delivery/'论文.docx').write_bytes(b'revised after visual check')
    result=verifier.audit(args)
    assert result['ready_for_review_delivery'] is False and any('docx_sha256' in e for e in result['errors'])
    prepared['snapshot']['sections'][2]['title']='未更新哈希的新标题';snapshot.write_text(json.dumps(prepared),encoding='utf-8')
    result=verifier.audit(args)
    assert any('重算结果不同' in e for e in result['errors'])


def test_visual_path_and_sha256_aliases_are_checked(verifier,tmp_path,monkeypatch):
    prepared=synthetic_prepared();snapshot=tmp_path/'snapshot.json';snapshot.write_text(json.dumps(prepared),encoding='utf-8')
    delivery=tmp_path/'delivery';delivery.mkdir()
    for name in ('论文.docx','论文.pdf'):(delivery/name).write_bytes(name.encode())
    monkeypatch.setattr(verifier,'inspect_docx',lambda *args:{})
    monkeypatch.setattr(verifier,'inspect_pdf',lambda *args:{'pages':1})
    monkeypatch.setattr(verifier,'inspect_zip',lambda *args:{})
    image=tmp_path/'page-1.png';image.write_bytes(b'synthetic image bytes')
    receipt=tmp_path/'visual.json'
    value={'paper_hash':prepared['revision']['paper_hash'],'docx_sha256':verifier.sha(delivery/'论文.docx'),
        'pdf_sha256':verifier.sha(delivery/'论文.pdf'),'pages':[{'page':1,'inspected':True,'clean':True,'path':str(image),'sha256':verifier.sha(image)}]}
    receipt.write_text(json.dumps(value),encoding='utf-8')
    args=SimpleNamespace(snapshot=snapshot,delivery_dir=delivery,visual_receipt=receipt,output=tmp_path/'audit.json')
    assert verifier.audit(args)['ready_for_review_delivery'] is True
    image.write_bytes(b'changed image')
    result=verifier.audit(args)
    assert any('图像缺失或哈希不同' in e for e in result['errors'])
