from pathlib import Path
import io,json,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'.runtime/python-deps')]
import httpx
from docx import Document
from docx.enum.section import WD_SECTION
from docx.shared import Mm,Pt
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image,ImageDraw
session=json.loads((ROOT/'.runtime/session-8770.json').read_text(encoding='utf-8-sig'));assert Path(session['data_directory']).name=='advanced-browser-v030'
with httpx.Client(base_url='http://127.0.0.1:8770/api/v1',timeout=60,trust_env=False) as c:
 a=c.post('/session',json={'token':session['bootstrap_token']});a.raise_for_status();c.headers['X-CSRF-Token']=a.json()['csrf']
 def call(path,body=None,method='POST'):
  r=c.request(method,path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
 p=call('/projects',{'title':'[自动化验收样例] 六项扩展，不用于论文提交','brief':{'is_test':True,'purpose':'软件功能自动化测试，无用户研究成果声明'},'target_words':1000})['id'];base='/projects/'+p
 def upload(name,raw,role):
  r=c.post(base+'/files',files={'file':(name,raw)},data={'role':role});r.raise_for_status();return r.json()['file']['id']
 txt=upload('固定测试资料.txt','本工作台每次生成前读取已发布的论文规则包。人工修改按版本保存。引用应能定位到原始文献。此文件仅为软件测试资料。'.encode(),'research');csv=upload('统计测试样本.csv',b'A,B,C\n1,3,2\n3,5,1\n2,4,4\n5,6,3\n4,9,6\n7,8,5\n8,10,8\n9,9,7\n','data');ppt=upload('自动化续表样例.pptx',(ROOT/'.runtime/advanced-fixtures/continued-table-fixture.pptx').read_bytes(),'research')
 reference=call(base+'/references',{'metadata':{'authors':'测试作者','year':'2026','title':'自动化样本文献，非真实学术文献'},'file_id':txt})['id'];s=call(base+'/sections',{'title':'规则加载机制验证','kind':'system_design'})['id'];other=call(base+'/sections',{'title':'章节衔接验证','kind':'body'})['id']
 doc={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'此段用于程序验收，正文结论尚未作为用户研究成果确认。'},{'type':'citation','attrs':{'citationId':reference}}]}]};call('/sections/'+s+'/content',{'base_revision_id':None,'document':doc},'PUT')
 d=Document();d.add_heading('学校模板集成测试',0);d.add_paragraph('仅用于验证复杂封面与分节，不是学校正式模板。');d.sections[0].header.paragraphs[0].text='封面测试页眉';d.sections[0].top_margin=Mm(35);d.sections[0].page_width=Mm(210);d.sections[0].page_height=Mm(297)
 sec=d.add_section(WD_SECTION.NEW_PAGE);sec.header.is_linked_to_previous=False;sec.header.paragraphs[0].text='正文测试页眉'
 for k,v in [('top',25),('bottom',25),('left',30),('right',25)]:setattr(sec,k+'_margin',Mm(v))
 d.add_paragraph('{{PAPER_BODY}}');tail=d.add_section(WD_SECTION.NEW_PAGE);tail.header.is_linked_to_previous=False;tail.header.paragraphs[0].text='固定尾页页眉';d.add_paragraph('本页验证模板固定尾页和分节保留。')
 d.styles['Normal'].font.name='Times New Roman';d.styles['Normal'].font.size=Pt(12);d.styles['Normal']._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'宋体');d.styles['Normal'].paragraph_format.line_spacing=1.5;indent=OxmlElement('w:ind');indent.set(qn('w:firstLineChars'),'200');d.styles['Normal']._element.get_or_add_pPr().append(indent)
 buf=io.BytesIO();d.save(buf);raw=buf.getvalue();docx=upload('复杂封面分节测试模板.docx',raw,'draft');r=c.post(base+'/template',files={'file':('template.docx',raw)});r.raise_for_status()
 call(base+'/rules',{'statement':'该项目仅用于自动化软件测试，研究结果缺失时明确待补。','modality':'mandatory','scope':'global'});call(base+'/rule-packages',{})
 fixture={'project_id':p,'section_id':s,'other_section_id':other,'text_file_id':txt,'csv_file_id':csv,'ppt_file_id':ppt,'docx_file_id':docx,'reference_id':reference,'is_test':True}
 (ROOT/'.runtime/advanced-fixtures/browser-fixture.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(fixture,ensure_ascii=False))
