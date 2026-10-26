"""Isolated browser fixture; synthetic documents and no remote models."""
import io,json,os,socket,sys,tempfile,threading,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend')]
OUTPUT=ROOT/'.runtime/evidence-knowledge-browser';OUTPUT.mkdir(parents=True,exist_ok=True)
data=Path(tempfile.mkdtemp(prefix='run-',dir=OUTPUT))
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
os.environ['PAPER_DATA_DIR']=str(data);os.environ['PAPER_PORT']=str(port)
from app import db,parsers,security,documents,model_install,tasks
from app import knowledge_units as units
from app.main import app
from reportlab.pdfgen import canvas
from docx import Document
original_status=model_install.status
model_install.status=lambda name:{**original_status(name),'ready':False}
db.init();pid=db.uid();stamp=db.now()
db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'证据知识库功能测试（合成资料）',stamp,stamp))
text='\n\n'.join(f'第{i+1}项验证仅适用于单用户环境。并发操作仍需额外验证，不得声称已经通过测试。样本量为{i+10}。' for i in range(25))
txt,_=parsers.register_file(pid,'边界与条件.txt',text.encode());parsers.parse_file(txt['id']);parsed=units.build(pid,txt['id'])
pdf_data=io.BytesIO();pdf=canvas.Canvas(pdf_data);pdf.drawString(40,740,'Only single-user use has been evaluated.');pdf.drawString(40,700,'Concurrent operations need further validation.');pdf.showPage();pdf.drawString(40,740,'Table 1: test definitions are not execution results.');pdf.save()
pdf_file,_=parsers.register_file(pid,'原页定位.pdf',pdf_data.getvalue());parsers.parse_file(pdf_file['id'],native_only=True);units.build(pid,pdf_file['id'])
doc=Document();doc.add_heading('方法与适用范围',1);doc.add_paragraph('仅限单用户。');table=doc.add_table(rows=2,cols=2);table.cell(0,0).text='样本';table.cell(0,1).text='数量';table.cell(1,0).text='条件组';table.cell(1,1).text='12';buf=io.BytesIO();doc.save(buf)
doc_file,_=parsers.register_file(pid,'结构化表格.docx',buf.getvalue());parsers.parse_file(doc_file['id']);units.build(pid,doc_file['id'])
refs={}
for f in (txt,pdf_file,doc_file):
 rid=db.uid();refs[f['id']]=rid;db.execute('INSERT INTO bibliography(id,project_id,metadata,file_id,created_at) VALUES(?,?,?,?,?)',(rid,pid,db.dumps({'title':f['name'],'authors':['合成测试作者'],'year':'2026'}),f['id'],stamp))
sid=db.uid();db.execute('INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)',(sid,pid,'方法验证',0))
uid=parsed['units'][0]['id'];doc={'type':'doc','content':[{'type':'paragraph','attrs':{'nodeId':'sample-paragraph','knowledgeUnitIds':[uid]},'content':[{'type':'text','text':'本测试仅验证单用户范围。'},{'type':'citation','attrs':{'nodeId':'sample-citation','citationId':refs[txt['id']]}}]}]}
documents.save(security.require_section(sid),doc,None)
from app import material_policy
dual,_=parsers.register_file(pid,'SBTCert用途测试.txt','研究文献也可用于写作示例，不能作为本项目结果。'.encode(),role='example');parsers.parse_file(dual['id'])
material_policy.set_usages(pid,dual['id'],{'usages':['example','research'],'source_kind':'research_publication','note':'浏览器合成双用途文献'})
units.build(pid,dual['id'])
deck=io.BytesIO()
with zipfile.ZipFile(deck,'w') as z:
    z.writestr('ppt/presentation.xml','<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="s1"/></p:sldIdLst></p:presentation>')
    z.writestr('ppt/_rels/presentation.xml.rels','<Relationships><Relationship Id="s1" Target="slides/slide1.xml" Type="slide"/></Relationships>')
    texts=['摘要教学','错误示例','计划实现系统。','正确示例','实现了证书哈希上链与链上验证功能。','教学正例不构成本项目成果证据。']
    shapes=''.join('<p:sp><p:txBody><a:p><a:r><a:t>'+t+'</a:t></a:r></a:p></p:txBody></p:sp>' for t in texts)
    z.writestr('ppt/slides/slide1.xml','<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'+shapes+'</p:spTree></p:cSld></p:sld>')
guide,_=parsers.register_file(pid,'摘要教学示例.pptx',deck.getvalue(),role='rules');parsers.parse_file(guide['id'])
(OUTPUT/'fixture.json').write_text(json.dumps({'port':port,'project_id':pid,'section_id':sid,'unit_id':uid,'txt_file':txt['id'],'pdf_file':pdf_file['id'],'docx_file':doc_file['id'],'dual_file':dual['id'],'guidance_file':guide['id'],'parse_id':parsed['id'],'data':str(data)},ensure_ascii=False),encoding='utf-8')
threading.Thread(target=tasks.run_forever,daemon=True).start()
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning',access_log=False)
