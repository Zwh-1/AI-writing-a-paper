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
    # Word's primary header is the odd *logical page number* header.  A
    # STYLEREF without the \n switch returns the heading's text, excluding
    # the native list number.  Match only level-one headings, never Heading 2/3.
    chapter_style=policy.get('chapter_style','Heading 1')
    # Built-in style names are localized by the Office UI. The numeric
    # STYLEREF selector is language-independent; custom style names stay named.
    selector='1' if chapter_style in ('Heading 1','标题 1','标题1') else '"'+chapter_style+'"'
    # 单页（奇数页）显示论文题目；双页（偶数页）按课程原规则显示当前章标题。
    # 偶数页用 STYLEREF 域动态取当前一级标题，随所在章节变化。
    for header,even_page in ((section.header,False),(section.even_page_header,True)):
        header.is_linked_to_previous=False;paragraph=header.paragraphs[0]
        paragraph.clear();unindent(paragraph);paragraph.alignment=1;paragraph.paragraph_format.line_spacing=1
        paragraph.paragraph_format.space_before=Pt(0);paragraph.paragraph_format.space_after=Pt(0)
        if even_page:field(paragraph,' STYLEREF '+selector+' ',first_title)
        else:paragraph.add_run(title)
        for run in paragraph.runs:
            run.font.name='Times New Roman';run.font.size=Pt(policy.get('header_font_size',12))
            run._r.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),policy.get('header_font','宋体'))
        borders=OxmlElement('w:pBdr');bottom=OxmlElement('w:bottom')
        for key,value in {'val':'single','sz':'4','space':'1','color':'000000'}.items():bottom.set(qn('w:'+key),value)
        borders.append(bottom);paragraph._p.get_or_add_pPr().append(borders)
    if policy.get('header_distance'):section.header_distance=Mm(policy['header_distance'])
    if policy.get('footer_distance'):section.footer_distance=Mm(policy['footer_distance'])


def heading_text(title,level=1):
    """Separate historical typed numbering from a native numbered heading."""
    import re
    text=str(title).strip()
    if level==1:
        text=re.sub(r'^第[一二两三四五六七八九十百零\d]+章\s*[、.．]?\s*','',text)
    return re.sub(r'^\d+(?:\.\d+){0,2}(?:\s+|[、．])','',text).strip()


def native_heading_numbering(doc):
    """Create a three-level list tied to the three built-in heading styles."""
    root=doc.part.numbering_part.element
    abstract_ids=[int(x.get(qn('w:abstractNumId'))) for x in root.findall(qn('w:abstractNum'))]
    num_ids=[int(x.get(qn('w:numId'))) for x in root.findall(qn('w:num'))]
    abstract_id=max(abstract_ids,default=-1)+1
    num_id=max(num_ids,default=0)+1
    abstract=OxmlElement('w:abstractNum');abstract.set(qn('w:abstractNumId'),str(abstract_id))
    multi=OxmlElement('w:multiLevelType');multi.set(qn('w:val'),'multilevel');abstract.append(multi)
    for level in range(3):
        item=OxmlElement('w:lvl');item.set(qn('w:ilvl'),str(level))
        for tag,value in (('start','1'),('numFmt','decimal'),('pStyle','Heading'+str(level+1)),
                          ('lvlText','.'.join('%'+str(i+1) for i in range(level+1))),('lvlJc','left'),('suff','space')):
            child=OxmlElement('w:'+tag);child.set(qn('w:val'),value);item.append(child)
        props=OxmlElement('w:pPr');ind=OxmlElement('w:ind')
        ind.set(qn('w:left'),'0');ind.set(qn('w:hanging'),'0');props.append(ind);item.append(props)
        abstract.append(item)
    root.append(abstract)
    number=OxmlElement('w:num');number.set(qn('w:numId'),str(num_id))
    identity=OxmlElement('w:abstractNumId');identity.set(qn('w:val'),str(abstract_id));number.append(identity);root.append(number)
    return num_id


def number_heading(paragraph,num_id,level):
    props=paragraph._p.get_or_add_pPr()
    for old in list(props.findall(qn('w:numPr'))):props.remove(old)
    numbering=OxmlElement('w:numPr')
    for tag,value in (('ilvl',str(level-1)),('numId',str(num_id))):
        child=OxmlElement('w:'+tag);child.set(qn('w:val'),value);numbering.append(child)
    props.append(numbering);unindent(paragraph)
    paragraph.paragraph_format.keep_with_next=True


def toc_styles(doc,width_mm,fmt,components=None):
    """Give automatic directory rows deterministic indentation and tab leaders."""
    components=components or {}
    for level in (1,2,3):
        style=canonical_toc_style(doc,level)
        override=components.get('toc_'+str(level),{})
        font_size=float(override.get('font_size',fmt['font_size']))
        props=style.element.get_or_add_pPr()
        ind=props.find(qn('w:ind'))
        if ind is None:ind=OxmlElement('w:ind');props.append(ind)
        ind.set(qn('w:firstLineChars'),'0')
        if override.get('left_indent_chars') is not None:
            chars=float(override['left_indent_chars'])
            ind.set(qn('w:left'),str(round(chars*font_size*20)))
            ind.set(qn('w:leftChars'),str(round(chars*100)))
        elif ind.get(qn('w:left')) is None and ind.get(qn('w:leftChars')) is None:
            ind.set(qn('w:left'),str(round((level-1)*2*font_size*20)))
        ind.set(qn('w:right'),'0')
        for old in list(props.findall(qn('w:tabs'))):props.remove(old)
        tabs=OxmlElement('w:tabs');tab=OxmlElement('w:tab')
        for key,value in {'val':'right','leader':'dot','pos':str(round(width_mm*1440/25.4))}.items():tab.set(qn('w:'+key),value)
        tabs.append(tab);props.append(tabs)
        # Compiled TOC styles may deliberately use a larger bold first level;
        # add only missing font values and preserve those verified parameters.
        if style.font.name is None:style.font.name=fmt['latin_font']
        if style.font.size is None:style.font.size=Pt(fmt['font_size'])
        fonts=style.element.get_or_add_rPr().get_or_add_rFonts()
        if fonts.get(qn('w:eastAsia')) is None:fonts.set(qn('w:eastAsia'),fmt['font'])
        style.paragraph_format.space_before=Pt(0);style.paragraph_format.space_after=Pt(0)
        style.paragraph_format.keep_with_next=False
        if override.get('line_spacing_points'):
            style.paragraph_format.line_spacing=Pt(float(override['line_spacing_points']))


def canonical_toc_style(doc,level):
    """Resolve the style Word actually applies when regenerating a TOC."""
    from docx.enum.style import WD_STYLE_TYPE
    canonical='toc '+str(level)
    if canonical in doc.styles:return doc.styles[canonical]
    title='TOC '+str(level)
    if title in doc.styles:
        style=doc.styles[title]
        style.element.find(qn('w:name')).set(qn('w:val'),canonical)
        style.element.set(qn('w:styleId'),'TOC'+str(level))
        return doc.styles[canonical]
    style=doc.styles.add_style(canonical,WD_STYLE_TYPE.PARAGRAPH,builtin=True)
    style.element.set(qn('w:styleId'),'TOC'+str(level))
    return style
