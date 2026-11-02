"""Word sections for explicit front-matter and body page-number requirements."""
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm,Pt
from .paper_tables import unindent


def new_numbered_section(doc):
    from docx.enum.section import WD_SECTION
    # Separate physical pagination from the numbering restart. Older Office
    # engines otherwise insert an unrequested recto blank page at each restart.
    doc.add_page_break()
    section=doc.add_section(WD_SECTION.CONTINUOUS)
    marker=doc.paragraphs[-1]
    marker.paragraph_format.space_before=Pt(0);marker.paragraph_format.space_after=Pt(0)
    marker.paragraph_format.line_spacing=Pt(1)
    return section


def numbering(section,fmt,start=1):
    section.footer.is_linked_to_previous=False
    kind=section._sectPr.find(qn('w:type'))
    if kind is None:kind=OxmlElement('w:type');section._sectPr.append(kind)
    kind.set(qn('w:val'),'continuous')
    element=section._sectPr.find(qn('w:pgNumType'))
    if element is None:element=OxmlElement('w:pgNumType');section._sectPr.append(element)
    element.set(qn('w:fmt'),fmt);element.set(qn('w:start'),str(start))


def body_headers(doc,section,policy,title,field,first_title):
    if not policy.get('chapter_headers'):return
    even=doc.settings.element.find(qn('w:evenAndOddHeaders'))
    if even is None:even=OxmlElement('w:evenAndOddHeaders');doc.settings.element.append(even)
    for header,chapter in ((section.header,False),(section.even_page_header,True)):
        header.is_linked_to_previous=False;paragraph=header.paragraphs[0]
        paragraph.clear();unindent(paragraph);paragraph.alignment=1;paragraph.paragraph_format.line_spacing=1
        paragraph.paragraph_format.space_before=Pt(0);paragraph.paragraph_format.space_after=Pt(0)
        if chapter:field(paragraph,' STYLEREF "Paper Chapter Heading" ',first_title)
        else:paragraph.add_run(title)
        for run in paragraph.runs:
            run.font.name='Times New Roman';run.font.size=Pt(policy.get('header_font_size',12))
            run._r.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),policy.get('header_font','宋体'))
        borders=OxmlElement('w:pBdr');bottom=OxmlElement('w:bottom')
        for key,value in {'val':'single','sz':'4','space':'1','color':'000000'}.items():bottom.set(qn('w:'+key),value)
        borders.append(bottom);paragraph._p.get_or_add_pPr().append(borders)
    if policy.get('header_distance'):section.header_distance=Mm(policy['header_distance'])
    if policy.get('footer_distance'):section.footer_distance=Mm(policy['footer_distance'])
