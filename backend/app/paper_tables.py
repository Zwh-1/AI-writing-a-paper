"""Native, editable Word three-line tables shared by standalone and thesis export."""
import io
from docx import Document
from docx.shared import Mm, Pt
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _border(parent, name, value='nil', size=0):
    element = OxmlElement('w:'+name)
    element.set(qn('w:val'), value)
    if size:
        element.set(qn('w:sz'), str(size))
        element.set(qn('w:color'), '000000')
    parent.append(element)


def unindent(paragraph):
    paragraph.paragraph_format.first_line_indent = Mm(0)
    ind = paragraph._p.get_or_add_pPr().find(qn('w:ind'))
    if ind is not None:
        ind.set(qn('w:firstLineChars'),'0')


def add_caption(doc, text):
    paragraph = doc.add_paragraph(text)
    paragraph.alignment = 1
    unindent(paragraph)
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_after = Pt(6)
    return paragraph


def style_three_line(table):
    table.style = 'Normal Table'
    table.alignment = 1
    props = table._tbl.tblPr
    for previous in list(props.findall(qn('w:tblBorders'))):
        props.remove(previous)
    borders = OxmlElement('w:tblBorders')
    for name in ('top','bottom','left','right','insideH','insideV'):
        _border(borders,name,'single' if name in ('top','bottom') else 'nil',12 if name in ('top','bottom') else 0)
    props.append(borders)
    for i, row in enumerate(table.rows):
        if i == 0:
            header = OxmlElement('w:tblHeader')
            row._tr.get_or_add_trPr().append(header)
        row_props=row._tr.get_or_add_trPr()
        for previous in list(row_props.findall(qn('w:cantSplit'))):row_props.remove(previous)
        estimated_lines=max((len(cell.text)/max(3,36/len(row.cells)) for cell in row.cells),default=0)
        if estimated_lines<30:
            keep = OxmlElement('w:cantSplit')
            row_props.append(keep)
        for cell in row.cells:
            cell.vertical_alignment = 1
            cp = cell._tc.get_or_add_tcPr()
            for old in list(cp.findall(qn('w:tcBorders'))):
                cp.remove(old)
            cb = OxmlElement('w:tcBorders')
            for name in ('top','bottom','left','right','insideH','insideV'):
                _border(cb,name,'single' if i == 0 and name == 'bottom' else 'nil',6 if i == 0 and name == 'bottom' else 0)
            if i == 0:
                cb.find(qn('w:top')).set(qn('w:val'),'single')
                cb.find(qn('w:top')).set(qn('w:sz'),'12')
            # Bottom table rule must win over an explicit nil on the last cells.
            if i == len(table.rows)-1:
                cb.find(qn('w:bottom')).set(qn('w:val'),'single')
                cb.find(qn('w:bottom')).set(qn('w:sz'),'12')
            cp.append(cb)
            shading = cp.find(qn('w:shd'))
            if shading is not None:
                cp.remove(shading)
            for paragraph in cell.paragraphs:
                paragraph.alignment = 1
                unindent(paragraph)
                paragraph.paragraph_format.space_before = Pt(4)
                paragraph.paragraph_format.space_after = Pt(4)
                paragraph.paragraph_format.line_spacing = 1.15
                for run in paragraph.runs:
                    if i==0:run.bold=True
                    elif run.bold is None:run.bold=False


def table_document(spec):
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.left_margin = section.right_margin = Mm(25)
    normal = doc.styles['Normal']
    normal.font.name, normal.font.size = 'Times New Roman', Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'宋体')
    add_caption(doc,spec['title'])
    table = doc.add_table(rows=len(spec['rows'])+1,cols=len(spec['headers']))
    for i, row in enumerate([spec['headers']]+spec['rows']):
        for j, value in enumerate(row):
            table.cell(i,j).text = str(value)
    style_three_line(table)
    if spec.get('note'):
        keep_note_with_table(table)
        doc.add_paragraph('注：'+spec['note'])
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def keep_note_with_table(table):
    import math
    if not table.rows:return
    columns=max(1,len(table.columns));per_line=max(3,36/columns)
    estimated=sum(max(1,max((math.ceil(len(cell.text)/per_line) for cell in row.cells),default=1))*14+8 for row in table.rows)
    rows=table.rows if estimated<400 else [table.rows[-1]]
    for row in rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:paragraph.paragraph_format.keep_with_next=True


def table_node(spec, artifact_id=None):
    from . import db
    attrs = {'nodeId':db.uid(),'tableStyle':'three-line','caption':spec['title'],'note':spec.get('note','')}
    if artifact_id:
        attrs['artifactId'] = artifact_id
    return {'type':'table','attrs':attrs,'content':[
        {'type':'tableRow','content':[
            {'type':'tableHeader' if i == 0 else 'tableCell','content':[
                {'type':'paragraph','content':[{'type':'text','text':str(value)}]} if str(value) else {'type':'paragraph'}
            ]} for value in row
        ]} for i,row in enumerate([spec['headers']]+spec['rows'])
    ]}
