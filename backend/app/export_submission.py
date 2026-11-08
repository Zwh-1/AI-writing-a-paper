"""Linear DOCX/TXT submission copies, retaining manuscript content and references."""
from docx import Document
from docx.shared import Mm,Pt,RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from . import db,documents,security
from .docx_references import citation_link,bookmark_reference,paragraph_runs
from .format_sections import heading_text
from .figure_standards import resolve_reference


def write_submission(folder,project,snapshots,refs,numbering,year_labels,artifacts,figure_rows,fmt,citation_style,bibliography_text):
    doc=Document();section=doc.sections[0]
    section.page_width=Mm(210);section.page_height=Mm(297)
    section.top_margin=section.bottom_margin=Mm(25)
    section.left_margin=Mm(30);section.right_margin=Mm(25)
    style=doc.styles['Normal'];style.font.name=fmt['latin_font'];style.font.size=Pt(fmt['font_size'])
    style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),fmt['font'])
    style.paragraph_format.line_spacing=1.5
    style.paragraph_format.space_after=Pt(0)
    style.paragraph_format.first_line_indent=Pt(fmt['font_size']*2)
    for name in ('Title','Heading 1','Heading 2','Heading 3'):
        doc.styles[name].font.color.rgb=RGBColor(0,0,0)
        doc.styles[name].font.name=fmt['latin_font']
        doc.styles[name].element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),fmt['font'])
    title_style=doc.styles['Title'];title_style.font.size=Pt(18)
    title_style.font.bold=True;title_style.font.underline=False
    # WPS can supply a blue built-in Title border when the source leaves it
    # unspecified. Explicit nil borders prevent that inherited decoration.
    title=doc.add_paragraph(project['title'],'Title');title.paragraph_format.first_line_indent=Pt(0)
    for props in (title_style.element.get_or_add_pPr(),title._p.get_or_add_pPr()):
        for old in list(props.findall(qn('w:pBdr'))):props.remove(old)
        borders=OxmlElement('w:pBdr')
        for edge in ('top','left','bottom','right','between','bar'):
            border=OxmlElement('w:'+edge);border.set(qn('w:val'),'nil');borders.append(border)
        props.append(borders)
    lines=[project['title']]
    counts={'paragraphs':0,'citations':0,'figures':0,'tables':0,'references':0}
    counters=[0,0,0];active_sid=None

    def label(node):
        rid=node.get('attrs',{}).get('citationId')
        if rid not in refs:raise ValueError('检测提交副本存在悬空引用')
        return '（'+year_labels[rid]+'）' if citation_style=='author_year' else '['+str(numbering[rid])+']'

    def inline(paragraph,node):
        typ=node.get('type');attrs=node.get('attrs',{})
        if typ=='text':
            value=documents.normalize_workflow_terms(node.get('text',''));run=paragraph.add_run(value)
            marks={m['type'] for m in node.get('marks',[])}
            run.bold='bold' in marks;run.italic='italic' in marks
            if 'superscript' in marks:run.font.superscript=True
            if 'subscript' in marks:run.font.subscript=True
            return value
        if typ=='citation':
            value=label(node);citation_link(paragraph,attrs['citationId'],value,citation_style=='numeric' and fmt.get('reference_superscript',True))
            counts['citations']+=1;return value
        if typ=='crossReference':
            target=resolve_reference(figure_rows,attrs,active_sid)
            if not target:raise ValueError('检测提交副本存在失效图表引用')
            paragraph.add_run(target['label']);return target['label']
        if typ=='hardBreak':paragraph.add_run().add_break();return '\n'
        if typ=='footnote':
            value='（注：'+attrs.get('noteText','')+'）';paragraph.add_run(value);return value
        if typ in ('inlineFormula','formula'):
            from .math_office import convert
            from .ooxml_preserve import omml
            value=attrs.get('latex') or attrs.get('expression','')
            paragraph._p.append(omml(attrs.get('omml') or convert(value)));return value
        return ''.join(inline(paragraph,child) for child in node.get('content',[]))

    def heading(value,level):
        level=min(max(level,1),3);counters[level-1]+=1
        for index in range(level,3):counters[index]=0
        value='.'.join(str(n) for n in counters[:level])+' '+heading_text(value,level)
        doc.add_paragraph(value,'Heading '+str(level));lines.append(value)

    def block(node,prefix=''):
        typ=node.get('type');attrs=node.get('attrs',{})
        if typ in ('bulletList','orderedList'):
            start=int(attrs.get('start',1))
            for index,child in enumerate(node.get('content',[])):
                block(child,(str(start+index)+'. ') if typ=='orderedList' else '• ')
            return
        if typ=='listItem':
            for index,child in enumerate(node.get('content',[])):block(child,prefix if index==0 else '')
            return
        if typ=='heading':heading(documents.plain(node),int(attrs.get('level',2)));return
        if typ=='paragraph':
            paragraph=doc.add_paragraph(prefix);value=prefix+inline(paragraph,node);lines.append(value);counts['paragraphs']+=1
            for run in paragraph_runs(paragraph):
                run.font.name=fmt['latin_font'];run.font.size=Pt(fmt['font_size'])
                run._r.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),fmt['font'])
            return
        if typ in ('figure','table'):
            row=next((r for r in figure_rows if r['section_id']==active_sid and r['node_id']==attrs.get('nodeId')),None)
            caption=((row['label']+' ') if row else '')+documents.normalize_workflow_terms(attrs.get('caption',''))
            if typ=='figure':
                artifact=artifacts.get(attrs.get('assetVersionId') or attrs.get('artifactId'))
                if not artifact or not artifact.get('sha256'):raise ValueError('检测提交副本缺少图片资源')
                doc.add_picture(str(security.safe_object(artifact['sha256'])),width=Mm(150))
                doc.paragraphs[-1].paragraph_format.keep_with_next=True
                doc.add_paragraph(caption);lines.append(caption);counts['figures']+=1;return
            caption_paragraph=doc.add_paragraph(caption);caption_paragraph.paragraph_format.keep_with_next=True
            lines.append(caption);rows=node.get('content',[])
            columns=max((sum(max(1,int(c.get('attrs',{}).get('colspan',1))) for c in r.get('content',[])) for r in rows),default=1)
            table=doc.add_table(rows=len(rows),cols=columns);table.style='Table Grid';table.autofit=False
            weights=attrs.get('columnWidths') or [1]*columns
            if len(weights)!=columns:raise ValueError('检测提交副本的表格列宽数量不一致')
            available=section.page_width-section.left_margin-section.right_margin
            widths=[int(available*weight/sum(weights)) for weight in weights]
            for column,width in zip(table.columns,widths):column.width=width
            table_borders=OxmlElement('w:tblBorders')
            for edge in ('top','left','bottom','right','insideH','insideV'):
                border=OxmlElement('w:'+edge)
                for key,value in {'val':'single','sz':'4','color':'D9D9D9'}.items():border.set(qn('w:'+key),value)
                table_borders.append(border)
            table._tbl.tblPr.append(table_borders)
            occupied=set()
            for i,row_node in enumerate(rows):
                row_properties=table.rows[i]._tr.get_or_add_trPr()
                row_properties.append(OxmlElement('w:cantSplit'))
                if row_node.get('content') and all(c.get('type')=='tableHeader' for c in row_node['content']):
                    row_properties.append(OxmlElement('w:tblHeader'))
                j=0;values=[]
                for cell_node in row_node.get('content',[]):
                    while (i,j) in occupied:j+=1
                    span=max(1,int(cell_node.get('attrs',{}).get('colspan',1)))
                    height=max(1,int(cell_node.get('attrs',{}).get('rowspan',1)))
                    if j+span>columns or i+height>len(rows):raise ValueError('检测提交副本的合并单元格超出表格范围')
                    cell=table.cell(i,j)
                    if span>1 or height>1:cell=cell.merge(table.cell(i+height-1,j+span-1))
                    cell.width=sum(widths[j:j+span])
                    for r in range(i,i+height):
                        for c in range(j,j+span):occupied.add((r,c))
                    content=cell_node.get('content',[]);parts=[]
                    for k,child in enumerate(content):
                        paragraph=cell.paragraphs[0] if k==0 else cell.add_paragraph()
                        parts.append(inline(paragraph,child))
                    values.append('\n'.join(parts))
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.first_line_indent=Pt(0)
                        paragraph.paragraph_format.line_spacing=1.2
                        for run in paragraph_runs(paragraph):
                            run.font.size=Pt(11)
                            if cell_node.get('type')=='tableHeader':run.bold=True
                    if cell_node.get('type')=='tableHeader':
                        shading=OxmlElement('w:shd');shading.set(qn('w:fill'),'F2F2F2');cell._tc.get_or_add_tcPr().append(shading)
                    j+=span
                lines.append('\t'.join(values))
            if attrs.get('pageBreakRows'):
                from .table_continuation import split_word
                table=split_word(doc,table,rows,attrs['pageBreakRows'],caption)
            if attrs.get('note'):
                note='注：'+documents.normalize_workflow_terms(attrs['note']);doc.add_paragraph(note);lines.append(note)
            counts['tables']+=1;return
        if typ=='formula':paragraph=doc.add_paragraph();lines.append(inline(paragraph,node));return
        for child in node.get('content',[]):block(child)

    sections={item['section']['id']:item['section'] for item in snapshots}
    for item in snapshots:
        s=item['section'];active_sid=s['id']
        if s['kind'] in ('abstract','abstract_en'):
            title='摘要' if s['kind']=='abstract' else 'Abstract'
            doc.add_paragraph(title,'Heading 1');lines.append(title)
        else:
            depth=1;parent=s.get('parent_id');seen=set()
            while parent in sections and parent not in seen:seen.add(parent);depth+=1;parent=sections[parent].get('parent_id')
            heading(s['title'],depth)
        block(item['document'])
    doc.add_paragraph('参考文献','Heading 1');lines.append('参考文献')
    reference_items=sorted(refs.items(),key=lambda row:year_labels[row[0]]) if citation_style=='author_year' else refs.items()
    for rid,bib in reference_items:
        value=('' if citation_style=='author_year' else '['+str(numbering[rid])+'] ')+bibliography_text(db.loads(bib['metadata'],{}))
        paragraph=doc.add_paragraph(value);paragraph.paragraph_format.first_line_indent=Pt(0)
        bookmark_reference(paragraph,rid);lines.append(value);counts['references']+=1
    doc.save(folder/'论文_检测提交版.docx')
    (folder/'论文_检测提交版.txt').write_text('\n\n'.join(lines)+'\n','utf-8')
    return {**counts,'layout':'linear flow with visible heading numbers and cached citation text',
            'omitted_layout_only':['automatic directory','repeated page headers','page-number footers'],
            'manuscript_sections_included':len(snapshots),'all_references_included':True,
            'platform_detection_result':'not_run','suggested_submission':'上传DOCX并查看平台解析内容；也可粘贴TXT全文。'}
