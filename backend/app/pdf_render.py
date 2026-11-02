from html import escape
from pathlib import Path
import hashlib
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Image,Table,TableStyle
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.platypus.tableofcontents import TableOfContents
from . import documents,db
from .security import safe_object

def render_pdf(path,project,snapshots,refs,numbering,fmt,artifacts,figure_rows=None):
    from .figure_standards import inventory,resolve_reference
    figure_rows=figure_rows if figure_rows is not None else inventory(project['id'],snapshots=snapshots,artifacts=artifacts)
    active_sid=None
    filename={'宋体':'simsun.ttc','楷体':'simkai.ttf','黑体':'simhei.ttf','仿宋':'simfang.ttf'}.get(fmt.get('font','宋体'),'simsun.ttc')
    font_path=next((p for p in [Path('C:/Windows/Fonts')/filename,Path('C:/Windows/Fonts/msyh.ttc')] if p.exists()),None)
    if not font_path: raise ValueError('未找到可嵌入中文字体，请配置渲染组件')
    name='PaperChinese_'+hashlib.sha256(str(font_path).encode()).hexdigest()[:8]
    if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(font_path),subfontIndex=0))
    pdfmetrics.registerFontFamily(name,normal=name,bold=name,italic=name,boldItalic=name)
    body=ParagraphStyle('PaperBody',fontName=name,fontSize=float(fmt['font_size']),leading=float(fmt.get('line_spacing_points') or float(fmt['font_size'])*float(fmt['line_spacing'])),spaceAfter=5,firstLineIndent=float(fmt['font_size'])*float(fmt['first_line_chars']),wordWrap='CJK')
    heading=ParagraphStyle('PaperHeading',parent=body,fontSize=17,leading=27,spaceBefore=15,spaceAfter=14,firstLineIndent=0)
    title=ParagraphStyle('PaperTitle',parent=heading,fontSize=22,leading=36,alignment=1)
    caption=ParagraphStyle('Caption',parent=body,fontSize=10,alignment=1,firstLineIndent=0)
    table_style=ParagraphStyle('TableText',parent=body,fontSize=9,leading=14,firstLineIndent=0)
    story=[Spacer(1,35*mm),Paragraph(escape(project['title']),title),Spacer(1,15*mm),Paragraph('论文排版预览 · 独立 PDF 渲染',caption),Paragraph('本文档由本地 PDF 引擎排版，与 Word 的分页可能不同。请以学校指定软件验收为准。',caption),PageBreak()]
    toc=TableOfContents();toc.levelStyles=[ParagraphStyle('TOCLevel',parent=body,firstLineIndent=0,spaceAfter=6)]
    story.extend([Paragraph('目录',heading),toc,PageBreak()])
    def chapter_heading(text,level=0):
        paragraph=Paragraph(escape(text),heading);paragraph._paper_toc=(level,text);return paragraph
    def text(n):
        if n['type']=='text':
            value=escape(n.get('text',''))
            tags={'bold':'b','italic':'i','underline':'u','superscript':'super','subscript':'sub'}
            for mark in n.get('marks',[]):
                tag=tags.get(mark['type'])
                if tag:value='<'+tag+'>'+value+'</'+tag+'>'
            return value
        if n['type']=='citation':
            value='['+str(numbering.get(n.get('attrs',{}).get('citationId'),'?'))+']'
            return '<super>'+value+'</super>' if fmt.get('reference_superscript',True) else value
        if n['type']=='hardBreak':return '<br/>'
        if n['type']=='crossReference':
            target=resolve_reference(figure_rows,n.get('attrs',{}),active_sid)
            return escape(target['label'] if target else '【图表引用失效】')
        return ''.join(text(c) for c in n.get('content',[]))
    fig=0;table_count=0
    def block(n):
        nonlocal fig,table_count
        typ=n['type'];attrs=n.get('attrs',{})
        if typ in ('paragraph','heading','blockquote','listItem'):
            story.append(Paragraph(text(n) or ' ',heading if typ=='heading' else body))
        elif typ=='table':
            table_count+=1
            row=next((r for r in figure_rows if r['section_id']==active_sid and r['node_id']==attrs.get('nodeId')),None)
            story.append(Paragraph(escape((row['label'] if row else f'表 {table_count}')+' '+(attrs.get('caption') or '【表题待补】')),caption))
            source_rows=n.get('content',[]);columns=max((sum(max(1,int(c.get('attrs',{}).get('colspan',1))) for c in r.get('content',[])) for r in source_rows),default=1)
            values=[['']*columns for _ in source_rows];spans=[];occupied=set()
            for i,source_row in enumerate(source_rows):
                j=0
                for cell in source_row.get('content',[]):
                    while (i,j) in occupied:j+=1
                    a=cell.get('attrs',{});cs=max(1,int(a.get('colspan',1)));rs=max(1,int(a.get('rowspan',1)))
                    if j+cs>columns or i+rs>len(source_rows):raise ValueError('PDF表格合并范围无效')
                    values[i][j]=Paragraph(text(cell),table_style)
                    if cs>1 or rs>1:spans.append(('SPAN',(j,i),(j+cs-1,i+rs-1)))
                    occupied.update((r,c) for r in range(i,i+rs) for c in range(j,j+cs));j+=cs
            if values:
                width=(210-float(fmt['margin_left'])-float(fmt['margin_right']))*mm
                table=Table(values,colWidths=[width/columns]*columns,repeatRows=1,hAlign='LEFT')
                style=[('LINEABOVE',(0,0),(-1,0),1.5,colors.black),('LINEBELOW',(0,0),(-1,0),.75,colors.black),('LINEBELOW',(0,-1),(-1,-1),1.5,colors.black)] if attrs.get('tableStyle')=='three-line' else [('GRID',(0,0),(-1,-1),.5,colors.HexColor('#b5c5bc')),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf3ee'))]
                table.setStyle(TableStyle(style+spans+[('VALIGN',(0,0),(-1,-1),'TOP')]));story.extend([table,Spacer(1,8)])
                if attrs.get('note'):story.append(Paragraph(escape('注：'+attrs['note']),table_style))
        elif typ=='figure':
            a=artifacts.get(attrs.get('artifactId'))
            if a:
                row=next((r for r in figure_rows if r['section_id']==active_sid and r['node_id']==attrs.get('nodeId')),None)
                image=Image(str(safe_object(a['sha256'])));image._restrictSize(145*mm,180*mm);story.append(image);fig+=1;story.append(Paragraph(escape((row['label'] if row else f'图 {fig}')+' '+attrs.get('caption',a['name'])),caption))
        elif typ=='formula':story.append(Paragraph(escape(attrs.get('expression','')),caption))
        else:
            for c in n.get('content',[]):block(c)
    for x in snapshots:
        active_sid=x['section']['id']
        story.append(chapter_heading(x['section']['title']))
        for n in x['document'].get('content',[]):block(n)
    if refs:
        from .exports import bibliography_text
        story.append(chapter_heading('参考文献'))
        for rid,bib in refs.items():story.append(Paragraph(escape(f'[{numbering[rid]}] '+bibliography_text(db.loads(bib['metadata'],{}))),body))
    def page_number(canvas,doc):canvas.saveState();canvas.setFont(name,9);canvas.drawCentredString(105*mm,12*mm,str(doc.page));canvas.restoreState()
    class IndexedDocument(SimpleDocTemplate):
        def afterFlowable(self,flowable):
            item=getattr(flowable,'_paper_toc',None)
            if item:self.notify('TOCEntry',(item[0],item[1],self.page))
    doc=IndexedDocument(str(path),pagesize=(210*mm,297*mm),topMargin=float(fmt['margin_top'])*mm,bottomMargin=float(fmt['margin_bottom'])*mm,leftMargin=float(fmt['margin_left'])*mm,rightMargin=float(fmt['margin_right'])*mm)
    doc.multiBuild(story,onFirstPage=page_number,onLaterPages=page_number)
