from html import escape
from pathlib import Path
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Image,Table,TableStyle
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from . import documents,db
from .security import safe_object

def render_pdf(path,project,snapshots,refs,numbering,fmt,artifacts):
    font_path=next((p for p in [Path('C:/Windows/Fonts/simsun.ttc'),Path('C:/Windows/Fonts/msyh.ttc')] if p.exists()),None)
    if not font_path: raise ValueError('未找到可嵌入中文字体，请配置渲染组件')
    name='PaperChinese'
    if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(font_path),subfontIndex=0))
    body=ParagraphStyle('PaperBody',fontName=name,fontSize=float(fmt['font_size']),leading=float(fmt['font_size'])*float(fmt['line_spacing']),spaceAfter=5,firstLineIndent=float(fmt['font_size'])*float(fmt['first_line_chars']),wordWrap='CJK')
    heading=ParagraphStyle('PaperHeading',parent=body,fontSize=17,leading=27,spaceBefore=15,spaceAfter=14,firstLineIndent=0)
    title=ParagraphStyle('PaperTitle',parent=heading,fontSize=22,leading=36,alignment=1)
    caption=ParagraphStyle('Caption',parent=body,fontSize=10,alignment=1,firstLineIndent=0)
    table_style=ParagraphStyle('TableText',parent=body,fontSize=9,leading=14,firstLineIndent=0)
    story=[Spacer(1,35*mm),Paragraph(escape(project['title']),title),Spacer(1,15*mm),Paragraph('论文排版预览 · 独立 PDF 渲染',caption),Paragraph('本文档由本地 PDF 引擎排版，与 Word 的分页可能不同。请以学校指定软件验收为准。',caption),PageBreak()]
    def text(n):
        if n['type']=='text':return escape(n.get('text',''))
        if n['type']=='citation':return '<super>['+str(numbering.get(n.get('attrs',{}).get('citationId'),'?'))+']</super>'
        if n['type']=='hardBreak':return '<br/>'
        return ''.join(text(c) for c in n.get('content',[]))
    fig=0
    def block(n):
        nonlocal fig
        typ=n['type'];attrs=n.get('attrs',{})
        if typ in ('paragraph','heading','blockquote','listItem'):
            story.append(Paragraph(text(n) or ' ',heading if typ=='heading' else body))
        elif typ=='table':
            values=[[Paragraph(escape(documents.plain(c)),table_style) for c in row.get('content',[])] for row in n.get('content',[])]
            if values:
                table=Table(values,repeatRows=1,hAlign='LEFT');table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.HexColor('#b5c5bc')),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf3ee')),('VALIGN',(0,0),(-1,-1),'TOP')]));story.extend([table,Spacer(1,8)])
        elif typ=='figure':
            a=artifacts.get(attrs.get('artifactId'))
            if a:
                image=Image(str(safe_object(a['sha256'])));image._restrictSize(145*mm,180*mm);story.append(image);fig+=1;story.append(Paragraph(escape(f'图 {fig} '+attrs.get('caption',a['name'])),caption))
        elif typ=='formula':story.append(Paragraph(escape(attrs.get('expression','')),caption))
        else:
            for c in n.get('content',[]):block(c)
    for x in snapshots:
        story.append(Paragraph(escape(x['section']['title']),heading))
        for n in x['document'].get('content',[]):block(n)
    if refs:
        from .exports import bibliography_text
        story.append(Paragraph('参考文献',heading))
        for rid,bib in refs.items():story.append(Paragraph(escape(f'[{numbering[rid]}] '+bibliography_text(db.loads(bib['metadata'],{}))),body))
    def page_number(canvas,doc):canvas.saveState();canvas.setFont(name,9);canvas.drawCentredString(105*mm,12*mm,str(doc.page));canvas.restoreState()
    doc=SimpleDocTemplate(str(path),pagesize=(210*mm,297*mm),topMargin=float(fmt['margin_top'])*mm,bottomMargin=float(fmt['margin_bottom'])*mm,leftMargin=float(fmt['margin_left'])*mm,rightMargin=float(fmt['margin_right'])*mm)
    doc.build(story,onFirstPage=page_number,onLaterPages=page_number)
