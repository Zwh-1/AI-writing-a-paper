import io,json,zipfile
from pathlib import Path
import pytest
from PIL import Image
from test_workbench import client,project,file
from app import db,parsers,security,local_ocr,material_quality,provider,tasks

A=parsers.NS['a'];P=parsers.NS['p'];R=parsers.NS['r']

def deck(image=None,table=False,inherited=False):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('ppt/presentation.xml',f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldSz cx="9144000" cy="6858000"/><p:sldIdLst><p:sldId r:id="s1"/></p:sldIdLst></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels','<Relationships><Relationship Id="s1" Target="slides/slide3.xml"/></Relationships>')
        native=f'<p:sp><p:txBody><a:p><a:r><a:t>论文要求</a:t></a:r></a:p></p:txBody></p:sp>'
        if table:native+='<a:tbl>'+''.join('<a:tr>'+''.join(f'<a:tc><a:txBody><a:p><a:r><a:t>{v}</a:t></a:r></a:p></a:txBody></a:tc>' for v in row)+'</a:tr>' for row in [['项目','数量'],['参考文献','20']])+'</a:tbl>'
        if image is not None and not inherited:native+='<p:pic><a:blip r:embed="im1"/></p:pic>'
        z.writestr('ppt/slides/slide3.xml',f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>{native}</p:spTree></p:cSld></p:sld>')
        if image is not None:
            z.writestr('ppt/media/image1.png',image)
            if inherited:
                z.writestr('ppt/slides/_rels/slide3.xml.rels','<Relationships><Relationship Id="l1" Target="../slideLayouts/slideLayout1.xml" Type="test/slideLayout"/></Relationships>')
                z.writestr('ppt/slideLayouts/slideLayout1.xml',f'<p:sldLayout xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><a:blip r:embed="im1"/></p:sldLayout>')
                z.writestr('ppt/slideLayouts/_rels/slideLayout1.xml.rels','<Relationships><Relationship Id="im1" Target="../media/image1.png"/></Relationships>')
            else:z.writestr('ppt/slides/_rels/slide3.xml.rels','<Relationships><Relationship Id="im1" Target="../media/image1.png"/></Relationships>')
    return stream.getvalue()

def picture():
    s=io.BytesIO();Image.new('RGB',(500,200),'white').save(s,format='PNG');return s.getvalue()

def fake_ocr(monkeypatch,text='参考文献不得少于二十篇，图片内容必须核对。'):
    calls=[]
    monkeypatch.setattr(local_ocr,'capabilities',lambda:{'available':True})
    async def recognize(path,language='zh-Hans-CN'):
        calls.append(str(path))
        return {'text':text,'lines':[{'text':text,'raw_text':text,'words':[{'text':text,'box':{'x':5,'y':5,'width':300,'height':20}}]}],'language':language}
    monkeypatch.setattr(local_ocr,'recognize_path',recognize)
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:pytest.fail('Local parsing must never call the model provider'))
    return calls

def test_import_runs_local_ocr_and_keeps_candidate_provenance(client,monkeypatch):
    calls=fake_ocr(monkeypatch);pid=project(client)
    f,_=parsers.register_file(pid,'图片规范.pptx',deck(picture()),'rules')
    result=parsers.parse_file(f['id']);assert calls and result['external_requests'] is False
    assert result['quality']['ocr_completed_pages']==1
    source=db.one("SELECT * FROM sources WHERE method='windows_ocr'")
    assert '二十篇' in source['text'] and source['verified']==0
    locator=db.loads(source['locator']);assert locator['page']==1 and locator['asset_sha256']
    assert db.one('SELECT review_state FROM pages')['review_state']=='unreviewed'
    assert client.get('/api/v1/projects/'+pid+'/files').json()[0]['parse_quality']['state']=='extracted'

def test_legacy_ready_is_not_reported_as_fully_extracted(client,monkeypatch):
    pid=project(client);f,_=parsers.register_file(pid,'旧文件.pptx',deck(picture()),'rules')
    page=db.uid();db.execute('INSERT INTO pages(id,file_id,page_number,text,images,has_visual) VALUES(?,?,?,?,?,1)',(page,f['id'],1,'只有标题','[]'))
    db.execute("UPDATE files SET status='ready',pages=1 WHERE id=?",(f['id'],))
    report=client.get('/api/v1/files/'+f['id']+'/parse-report').json()
    assert report['state']=='ocr_pending' and report['ocr_pending_pages']==1 and report['repair_needed']
    assert client.post('/api/v1/projects/'+pid+'/materials/repair').json()['count']==1

def test_repair_preserves_ids_manual_content_and_reuses_asset_cache(client,monkeypatch):
    calls=fake_ocr(monkeypatch);pid=project(client);f,_=parsers.register_file(pid,'图片规范.pptx',deck(picture()),'rules')
    parsers.parse_file(f['id']);page=db.one('SELECT * FROM pages');old_sources=db.rows('SELECT * FROM sources ORDER BY id')
    db.execute("UPDATE pages SET text=text||?,review_note='用户备注' WHERE id=?",('\n人工补充的原始记录',page['id']))
    parsers.parse_file(f['id'],repair=True)
    assert len(calls)==1
    assert db.one('SELECT * FROM pages')['id']==page['id']
    assert '人工补充的原始记录' in db.one('SELECT text FROM pages')['text']
    assert db.one('SELECT review_note FROM pages')['review_note']=='用户备注'
    assert db.rows('SELECT * FROM sources ORDER BY id')==old_sources

def test_recognition_failure_is_visible_and_retry_recovers(client,monkeypatch):
    fake_ocr(monkeypatch);pid=project(client);f,_=parsers.register_file(pid,'失败规范.pptx',deck(picture()),'rules')
    async def broken(*args,**kwargs):raise RuntimeError('OCR engine fixture failure')
    monkeypatch.setattr(local_ocr,'recognize_path',broken)
    monkeypatch.setattr(__import__('app.office_renderer',fromlist=['render_file']),'render_file',lambda *a:(_ for _ in ()).throw(ValueError('No Office fixture')))
    result=parsers.parse_file(f['id']);assert result['quality']['state']=='partial'
    report=client.get('/api/v1/files/'+f['id']+'/parse-report').json()
    assert report['ocr_failed_pages']==1 and 'OCR engine fixture failure' in str(report['page_states'][0]['errors'])
    fake_ocr(monkeypatch)
    assert parsers.parse_file(f['id'],repair=True)['quality']['state']=='extracted'

def test_unavailable_ocr_never_reports_complete(client,monkeypatch):
    monkeypatch.setattr(local_ocr,'capabilities',lambda:{'available':False});pid=project(client)
    f,_=parsers.register_file(pid,'离线规范.pptx',deck(picture()),'rules')
    result=parsers.parse_file(f['id']);assert result['quality']['state']=='partial'
    assert result['quality']['ocr_failed_pages']==1 and result['quality']['unreviewed_pages']==1

def test_table_structure_and_inherited_images_are_kept(client):
    page=list(parsers.pptx_pages(deck(picture(),table=True,inherited=True)))[0]
    assert '项目 | 数量\n参考文献 | 20' in page['text']
    assert page['images'][0]['inherited'] is True and page['visual'] is True

def test_same_file_repair_jobs_are_deduplicated(client):
    pid=project(client);f,_=parsers.register_file(pid,'test.txt',b'hello')
    first=tasks.enqueue(pid,'parse',{'file_id':f['id']})
    second=client.post('/api/v1/files/'+f['id']+'/reparse').json()
    assert first['id']==second['id']

def test_reviewed_pages_and_sources_remain_untouched(client,monkeypatch):
    calls=fake_ocr(monkeypatch);pid=project(client);f,_=parsers.register_file(pid,'图片规范.pptx',deck(picture()),'rules')
    parsers.parse_file(f['id']);page=db.one('SELECT * FROM pages')
    db.execute("UPDATE pages SET review_state='reviewed',review_note='人工核对' WHERE id=?",(page['id'],));db.execute('UPDATE sources SET verified=1')
    before=db.rows('SELECT * FROM sources ORDER BY id');parsers.parse_file(f['id'],repair=True)
    assert db.one('SELECT review_state FROM pages')['review_state']=='reviewed'
    assert db.rows('SELECT * FROM sources ORDER BY id')==before and len(calls)==1

def test_large_image_ocr_uses_bounded_overlapping_tiles(client,monkeypatch):
    calls=fake_ocr(monkeypatch);buf=io.BytesIO();Image.new('RGB',(4500,2500),'white').save(buf,format='PNG');sha=security.put_blob(buf.getvalue())
    result=local_ocr._recognize_image(security.safe_object(sha),sha,'zh-Hans-CN')
    assert result['tiles']>=6 and len(calls)==result['tiles']
    assert result['image_size']==[4500,2500]
    again=local_ocr._recognize_image(security.safe_object(sha),sha,'zh-Hans-CN')
    assert again['cached'] is True and len(calls)==result['tiles']


def test_utf16_bom_is_decoded_before_gb18030(client):
    page=list(parsers.parse_pages('论文格式要求：参考文献不少于二十篇。'.encode('utf-16'),'.txt'))[0]
    assert page['text']=='论文格式要求：参考文献不少于二十篇。'

def test_rule_excerpt_from_split_ocr_keeps_ocr_provenance(client,monkeypatch):
    from app import rules
    fake_ocr(monkeypatch,'参考文献必须核对：'+'甲'*1050)
    pid=project(client);f,_=parsers.register_file(pid,'长规则.pptx',deck(picture()),'rules');parsers.parse_file(f['id'])
    rules.extract(pid)
    row=db.one("SELECT s.* FROM rules r JOIN sources s ON s.id=r.source_id WHERE length(r.statement)>1000")
    assert row and row['method']=='windows_ocr' and row['verified']==0
    assert db.loads(row['locator'])['parent_source_ids']


def test_explicit_human_transcript_resolves_machine_gap_without_faking_ocr(client,monkeypatch):
    monkeypatch.setattr(local_ocr,'capabilities',lambda:{'available':False});pid=project(client)
    f,_=parsers.register_file(pid,'人工核对规范.pptx',deck(picture()),'rules');parsers.parse_file(f['id'])
    page=db.one('SELECT * FROM pages')
    response=client.patch('/api/v1/pages/'+page['id'],json={'review_state':'reviewed','transcript':'已人工核对图片原文，参考文献不少于20篇。'})
    assert response.status_code==200
    report=client.get('/api/v1/files/'+f['id']+'/parse-report').json()
    assert report['state']=='extracted' and report['reviewed_pages']==1 and report['ocr_failed_pages']==0
    assert report['ocr_completed_pages']==0
    assert report['page_states'][0]['machine_ocr_state']=='unavailable'


def test_ppt_embedded_docx_text_table_and_image_are_read_without_activation(client,monkeypatch):
    from docx import Document
    document=Document();document.add_paragraph('嵌入Word中的参考文献要求不得遗漏。');table=document.add_table(rows=2,cols=2);table.cell(0,0).text='文献类型';table.cell(0,1).text='数量';table.cell(1,0).text='外文';table.cell(1,1).text='5';document.add_picture(io.BytesIO(picture()));raw=io.BytesIO();document.save(raw)
    original=deck();out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source,zipfile.ZipFile(out,'w') as z:
        for name in source.namelist():
            content=source.read(name)
            if name=='ppt/slides/slide3.xml':content=content.replace(b'</p:spTree>',b'<p:oleObj r:id="doc"/></p:spTree>')
            z.writestr(name,content)
        z.writestr('ppt/slides/_rels/slide3.xml.rels','<Relationships><Relationship Id="doc" Target="../embeddings/Document1.docx" Type="test/oleObject"/></Relationships>')
        z.writestr('ppt/embeddings/Document1.docx',raw.getvalue())
    fake_ocr(monkeypatch);pid=project(client);f,_=parsers.register_file(pid,'嵌入文档.pptx',out.getvalue(),'rules');result=parsers.parse_file(f['id'])
    assert result['quality']['state']=='extracted'
    source=db.one("SELECT * FROM sources WHERE method='embedded_native'")
    assert '参考文献要求不得遗漏' in source['text'] and '外文 | 5' in source['text']
    locator=db.loads(source['locator']);assert locator['source_part']=='ppt/embeddings/Document1.docx' and locator['embedded_sha256']
    page=db.one('SELECT * FROM pages');assert '嵌入DOCX原生内容' in page['text'] and page['has_visual']==1 and page['review_state']=='unreviewed'
    assert any('Document1.docx::' in image['source_part'] for image in db.loads(page['images']))
    before=db.rows('SELECT * FROM sources ORDER BY id');parsers.parse_file(f['id'],repair=True)
    assert db.rows('SELECT * FROM sources ORDER BY id')==before


def test_native_ocr_child_failure_is_reported_without_killing_parser(client,monkeypatch):
    import subprocess
    def crashed(*args,**kwargs):return subprocess.CompletedProcess(args[0],3221225477,stdout='',stderr='native engine fixture crash')
    monkeypatch.setattr(local_ocr.subprocess,'run',crashed)
    with pytest.raises(ValueError,match='主工作进程已保留'):local_ocr._ocr_process('ocr-image',{'path':'fixture.png'})
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired(args[0],1)
    monkeypatch.setattr(local_ocr.subprocess,'run',timeout)
    with pytest.raises(ValueError,match='子进程超时'):local_ocr._ocr_process('ocr-image',{'path':'fixture.png'})
