import io,re,hashlib,zipfile,shutil,subprocess,sqlite3
from pathlib import Path
from docx import Document
from docx.shared import Pt,Mm,Inches
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from . import db,documents,reviews
from .config import DATA,DB_PATH
from .security import safe_object,require_project
from .docx_references import citation_link,bookmark_reference,paragraph_runs,navigation_report
DEFAULT_FORMAT={'font':'宋体','latin_font':'Times New Roman','font_size':12,'line_spacing':1.5,'line_spacing_points':None,'alignment':'justify','first_line_chars':2,'margin_top':25,'margin_bottom':25,'margin_left':30,'margin_right':25,'heading_font':'黑体','reference_superscript':True}
def format_config(pid):
    from .paper_format import resolve
    return resolve(pid)['config']
def bibliography_text(meta):
    if meta.get('citation_raw'):return re.sub(r'\s+',' ',str(meta['citation_raw'])).strip()
    authors=meta.get('authors','')
    if isinstance(authors,list): authors='，'.join(authors)
    return f"{authors or '【作者待补】'}. {meta.get('title') or '【题名待补】'}{(' ['+meta['type']+']') if meta.get('type') else ''}. {meta.get('journal') or meta.get('publisher') or ''}, {meta.get('year') or '【年份待补】'}{(', '+str(meta['volume'])) if meta.get('volume') else ''}{(':'+str(meta['pages'])) if meta.get('pages') else ''}.{(' DOI:'+meta['doi']) if meta.get('doi') else ''}"
def field(paragraph,code,result=None):
    begin=OxmlElement('w:fldChar');begin.set(qn('w:fldCharType'),'begin');paragraph.add_run()._r.append(begin)
    instruction=OxmlElement('w:instrText');instruction.set(qn('xml:space'),'preserve');instruction.text=code;paragraph.add_run()._r.append(instruction)
    sep=OxmlElement('w:fldChar');sep.set(qn('w:fldCharType'),'separate');paragraph.add_run()._r.append(sep)
    if result is not None:paragraph.add_run(str(result))
    end=OxmlElement('w:fldChar');end.set(qn('w:fldCharType'),'end');paragraph.add_run()._r.append(end)
def set_font(style,east,latin,size):
    style.font.name=latin;style.font.size=Pt(size);style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),east)
def renderer():
    candidates=[shutil.which('soffice'),r'C:\Program Files\LibreOffice\program\soffice.exe',r'C:\Program Files (x86)\LibreOffice\program\soffice.exe']
    found=next((str(p) for p in candidates if p and Path(p).is_file()),None)
    if found:return found
    from .office_renderer import capabilities
    return 'office-com' if capabilities()['registered_com'] and capabilities()['powershell'] else None
def export_project(pid,options,tid=None,prepared=None):
    from .export_service import prepare
    prepared=prepared or prepare(pid,options)
    from .export_checks import freeze,check_docx,markdown_document
    writing_report=prepared['writing_report']
    frozen=prepared['snapshot']
    figure_delivery_results=[]
    from .bibliography_format import render as render_reference
    bibliography_version=frozen['settings']['academic_standards:'+pid]['bibliography_version']
    format_bibliography=lambda meta:render_reference(meta,bibliography_version)['text']
    p=frozen['project'];fmt={**DEFAULT_FORMAT,**(frozen['settings'].get('format:'+pid) or {})};sections=frozen['sections'];valid_revisions={x['id'] for x in frozen['revisions'].values()};report_findings=[x for x in frozen['findings'] if not x['revision_id'] or x['revision_id'] in valid_revisions];findings={'findings':report_findings,'summary':{state:sum(x['result']==state for x in report_findings) for state in ('pass','fail','unknown')}}
    from .paper_format import resolve as resolve_format
    format_resolution=resolve_format(pid,package=frozen['package'],settings=frozen['settings'].get('format:'+pid) or {},project=p)
    fmt=format_resolution['config']
    if options.get('mode')=='strict' and format_resolution['conflicts']:raise ValueError('已发布排版规则存在冲突，请先处理后正式导出')
    section_formats={s['id']:resolve_format(pid,package=frozen['package'],settings=frozen['settings'].get('format:'+pid) or {},section=s,project=p)['config'] for s in sections}
    component_styles={name:dict(values) for name,values in format_resolution.get('components',{}).items()}
    for name,values in (fmt.get('component_styles') or {}).items():
        component_styles[name]={**component_styles.get(name,{}),**values}
    bibliography_hyphenation=component_styles.get('references_body',{}).get('auto_hyphenation') is True
    snapshots=[];refs={};artifacts=frozen['artifacts'];numbering={};md=['# '+p['title'],''];package=frozen['package']
    for s in sections:
        rev=frozen['revisions'].get(s['id'])
        if rev:
            doc=db.loads(rev['document'],{});snapshots.append({'section':s,'revision':rev,'document':doc})
            for n in documents.nodes(doc):
                if n['type']=='citation':
                    rid=n.get('attrs',{}).get('citationId')
                    if rid not in refs:
                        bib=dict(frozen['references'][rid]) if rid in frozen['references'] else None
                        if not bib: raise ValueError('导出存在悬空引用')
                        snapshot=next((c for c in frozen['citations'] if c['revision_id']==rev['id'] and c['node_id']==n.get('attrs',{}).get('nodeId')),None)
                        if snapshot and options.get('reference_metadata','current')=='adopted': bib['metadata']=snapshot['reference_snapshot']
                        refs[rid]=bib;numbering[rid]=len(numbering)+1
    from .figure_standards import DEFAULT as FIGURE_DEFAULT,inventory,resolve_reference,evaluate as evaluate_figures
    figure_policy={**FIGURE_DEFAULT,**(frozen['settings'].get('figure_policy:'+pid) or {})}
    figure_policy['_format_hash']=hashlib.sha256(db.dumps({'format':frozen['settings'].get('format:'+pid) or {},'template':frozen['settings'].get('template:'+pid)}).encode()).hexdigest()
    figure_rows=inventory(pid,figure_policy,snapshots,artifacts,section_plan=sections)
    figure_lookup={(r['section_id'],r['node_id']):r for r in figure_rows}
    figure_report=evaluate_figures(pid,snapshots,artifacts,figure_policy)
    if options.get('mode')=='strict' and any(x['result'] in ('fail','unknown') for x in figure_report['items']):
        raise ValueError('正式导出前需完成图文说明、配图政策、真实来源和交叉引用核对；请查看论文图表规范检查')
    export_id=db.uid();folder=DATA/'exports'/export_id;folder.mkdir(parents=True)
    if frozen.get('graph_wiki',{}).get('objects'):
        (folder/'图谱与Wiki知识使用追溯.json').write_text(db.dumps(frozen['graph_wiki']),encoding='utf-8')
    linked={row['unit_id'] for row in frozen.get('knowledge_links',[])}
    for revision in frozen['revisions'].values():
        for node in documents.nodes(db.loads(revision['document'],{})):linked.update(node.get('attrs',{}).get('knowledgeUnitIds',[]))
    if linked:
        (folder/'原文单元与引用核对记录.json').write_text(db.dumps({'units':{uid:frozen['knowledge_units'][uid] for uid in linked if uid in frozen['knowledge_units']},'citation_links':frozen['knowledge_links'],'claim_checks':[x for x in frozen['knowledge_claim_checks'] if x['unit_id'] in linked],'note':'原文、读取范围及人工记录的冻结快照，不自动证明论断支持性'}),encoding='utf-8')
    reading_ids={row['reading_id'] for row in frozen['citation_reading_links']}
    for revision in frozen['revisions'].values():
        for node in documents.nodes(db.loads(revision['document'],{})):reading_ids.update(node.get('attrs',{}).get('documentReadingIds',[]))
    if reading_ids:
        (folder/'文献阅读范围与用途记录.json').write_text(db.dumps({'readings':{identity:frozen['document_readings'][identity] for identity in reading_ids if identity in frozen['document_readings']},'citation_links':frozen['citation_reading_links'],'human_citation_reviews':frozen['reading_citation_reviews'],'material_usages':frozen['material_usages'],'material_profiles':frozen['material_profiles'],'note':'引用链接保存采用时的原文阅读快照及版本；当前阅读记录单列。阅读覆盖不等于论断支持，资料中的指令仅为原文内容。'}),encoding='utf-8')
    if writing_report:
        (folder/'句子—论断—证据映射.json').write_text(db.dumps(writing_report['sentence_mapping']),encoding='utf-8')
        (folder/'论证覆盖与未决项.json').write_text(db.dumps(writing_report),encoding='utf-8')
        state_row=db.one('SELECT * FROM writing_project_state WHERE project_id=?',(pid,))
        source_list=db.rows('SELECT id,kind,origin_type,origin_id,origin_version,locator_json,payload_hash,validity FROM evidence_items WHERE project_id=?',(pid,))
        (folder/'源码与资料版本清单.json').write_text(db.dumps({'selected_state':state_row,'evidence':source_list,'statement':'冻结基线，不代表活动工作目录同一瞬间的状态'}),encoding='utf-8')
    template=frozen['settings'].get('template:'+pid)
    preserve=bool(options.get('preserve_template'))
    if preserve and not template:raise ValueError('请先上传带正文占位符的学校DOCX模板')
    doc=Document(str(safe_object(template))) if template and not preserve and safe_object(template).exists() else Document()
    if template and not preserve:
        # Keep template styles, section setup and headers, remove example body text.
        body=doc.element.body
        for child in list(body):
            if child.tag != qn('w:sectPr'): body.remove(child)
    section=doc.sections[0];section.page_height=Mm(297);section.page_width=Mm(210)
    for side in ('top','bottom','left','right'): setattr(section,side+'_margin',Mm(float(fmt['margin_'+side])))
    normal=doc.styles['Normal'];set_font(normal,fmt['font'],fmt['latin_font'],float(fmt['font_size']));normal.paragraph_format.line_spacing=float(fmt['line_spacing']);normal.paragraph_format.space_after=Pt(0)
    if bibliography_hyphenation:
        # Enable Word's own language-aware line breaking only for bibliography
        # prose. Suppression on Normal keeps the opt-in from changing the body.
        for tag,value in (('autoHyphenation','true'),('hyphenationZone','0')):
            setting=doc.settings.element.find(qn('w:'+tag))
            if setting is None:setting=OxmlElement('w:'+tag);doc.settings.element.append(setting)
            setting.set(qn('w:val'),value)
        suppress=normal.element.get_or_add_pPr().find(qn('w:suppressAutoHyphens'))
        if suppress is None:suppress=OxmlElement('w:suppressAutoHyphens');normal.element.get_or_add_pPr().append(suppress)
        suppress.set(qn('w:val'),'1')
    if fmt.get('line_spacing_points'):normal.paragraph_format.line_spacing=Pt(fmt['line_spacing_points'])
    ind=normal.element.get_or_add_pPr().find(qn('w:ind'))
    if ind is None: ind=OxmlElement('w:ind');normal.element.get_or_add_pPr().append(ind)
    ind.set(qn('w:firstLineChars'),str(int(float(fmt['first_line_chars'])*100)))
    for level,size in ((1,16),(2,14),(3,12)):
        set_font(doc.styles[f'Heading {level}'],fmt['heading_font'],fmt['latin_font'],size)
        from docx.shared import RGBColor
        doc.styles[f'Heading {level}'].font.color.rgb=RGBColor(0,0,0)
    doc.styles['Title'].font.color.rgb=RGBColor(0,0,0)
    if not template:
        title_style=doc.styles['Title'];set_font(title_style,fmt['heading_font'],fmt['latin_font'],18)
        title_style.font.bold=True;title_style.font.underline=False
        title_style.paragraph_format.alignment=1;title_style.paragraph_format.first_line_indent=Pt(0)
        title_style.paragraph_format.space_before=Pt(30);title_style.paragraph_format.space_after=Pt(20)
        title_props=title_style.element.get_or_add_pPr()
        for old in list(title_props.findall(qn('w:pBdr'))):title_props.remove(old)
        title_ind=title_props.find(qn('w:ind'))
        if title_ind is None:title_ind=OxmlElement('w:ind');title_props.append(title_ind)
        title_ind.set(qn('w:firstLineChars'),'0')
    def apply_component(paragraph,component,style=False,runs=None):
        values=dict(component_styles.get(component,{}))
        if not values:return
        if component.startswith(('heading_','toc_')) or component.endswith('_heading'):values.setdefault('first_line_chars',0)
        paragraph_format=paragraph.paragraph_format
        if 'alignment' in values:paragraph_format.alignment={'left':0,'center':1,'right':2,'justify':3}[values['alignment']]
        if values.get('line_spacing_points'):paragraph_format.line_spacing=Pt(values['line_spacing_points'])
        if values.get('page_break_before'):paragraph_format.page_break_before=True
        size=values.get('font_size',fmt['font_size'])
        if 'left_indent_chars' in values:paragraph_format.left_indent=Pt(size*values['left_indent_chars'])
        if 'first_line_chars' in values:
            props=paragraph._element.get_or_add_pPr() if not style else paragraph.element.get_or_add_pPr()
            ind=props.find(qn('w:ind'))
            if ind is None:ind=OxmlElement('w:ind');props.append(ind)
            ind.set(qn('w:firstLineChars'),str(int(values['first_line_chars']*100)))
        if 'hanging_chars' in values:
            props=paragraph._element.get_or_add_pPr() if not style else paragraph.element.get_or_add_pPr()
            ind=props.find(qn('w:ind'))
            if ind is None:ind=OxmlElement('w:ind');props.append(ind)
            if values['hanging_chars']:
                for name in ('firstLine','firstLineChars'):ind.attrib.pop(qn('w:'+name),None)
                ind.set(qn('w:hangingChars'),str(round(values['hanging_chars']*100)))
                ind.set(qn('w:hanging'),str(round(size*values['hanging_chars']*20)))
            else:
                for name in ('hanging','hangingChars'):ind.attrib.pop(qn('w:'+name),None)
        line=values.get('line_spacing_points',size*float(fmt['line_spacing']))
        for key in ('space_before','space_after'):
            if key+'_lines' in values:setattr(paragraph_format,key,Pt(line*values[key+'_lines']))
        selected_runs=[] if style else paragraph_runs(paragraph) if runs is None else runs
        fonts=[paragraph.font] if style else [run.font for run in selected_runs]
        for font in fonts:
            if 'font_size' in values:font.size=Pt(values['font_size'])
            if 'bold' in values:font.bold=values['bold']
            if 'latin_font' in values:font.name=values['latin_font']
        if 'font' in values:
            elements=[paragraph.element] if style else [run._r for run in selected_runs]
            for element in elements:element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),values['font'])
    for level in (1,2,3):apply_component(doc.styles[f'Heading {level}'],'heading_'+str(level),style=True)
    for level in (1,2,3):
        # Word's TOC field binds the built-in internal style name "toc 1".
        # A separate custom "TOC 1" style does not govern generated rows.
        from .format_sections import canonical_toc_style
        toc_style=canonical_toc_style(doc,level)
        apply_component(toc_style,'toc_'+str(level),style=True)
    from .format_sections import toc_styles
    toc_styles(doc,210-float(fmt['margin_left'])-float(fmt['margin_right']),fmt,component_styles)
    apply_component(doc.styles['TOC Heading'],'toc_title',style=True)
    if not template:apply_component(doc.styles['Title'],'title',style=True)
    if not preserve:
        title_paragraph=doc.add_heading(p['title'],0)
        for old in list(title_paragraph._p.get_or_add_pPr().findall(qn('w:pBdr'))):title_paragraph._p.get_or_add_pPr().remove(old)
        brief=db.loads(p['brief'],{})
        for label,key in [('作者','author'),('学校','school'),('专业','major'),('指导教师','supervisor')]:
            if brief.get(key):apply_component(doc.add_paragraph(label+'：'+str(brief[key])),'cover_fields')
        cover_note=options.get('cover_note')
        if cover_note is not None and (not isinstance(cover_note,str) or len(cover_note)>200 or re.search(r'[\x00-\x1f\x7f]',cover_note)):raise ValueError('封面说明须为不超过200字的单行纯文本')
        doc.add_paragraph(cover_note if cover_note is not None else '稿件用途：'+options.get('mode','draft')+'；未决事项请查看随附报告。')
    # An explicit revision profile can refine published rules without rewriting
    # their historical snapshots or claiming a temporary choice is a school rule.
    document_policy={**component_styles.get('document',{}),**(fmt.get('document_policy') or {})}
    native_numbering=document_policy.get('heading_numbering')=='native_multilevel'
    heading_number_id=None
    if native_numbering:
        from .format_sections import native_heading_numbering
        heading_number_id=native_heading_numbering(doc)
    chapter_style='Heading 1'
    if document_policy.get('chapter_headers') and not preserve and not native_numbering:
        chapter_style='Paper Chapter Heading'
        if chapter_style not in doc.styles:
            from docx.enum.style import WD_STYLE_TYPE
            style=doc.styles.add_style(chapter_style,WD_STYLE_TYPE.PARAGRAPH);style.base_style=doc.styles['Heading 1']
            outline=OxmlElement('w:outlineLvl');outline.set(qn('w:val'),'0');style.element.get_or_add_pPr().append(outline)
    separate_frontmatter=not preserve and bool(document_policy.get('frontmatter_numbering'))
    if not preserve:
        if separate_frontmatter:
            from docx.enum.section import WD_SECTION
            from .format_sections import numbering as set_numbering,new_numbered_section
            front_section=new_numbered_section(doc)
            set_numbering(front_section,document_policy['frontmatter_numbering'])
        else:doc.add_page_break()
    def display_title(section):
        title=section['title']
        if native_numbering and section['kind'] not in ('abstract','abstract_en'):
            from .format_sections import heading_text
            return heading_text(title)
        if document_policy.get('heading_numbering')=='arabic_space' and section['kind'] not in ('abstract','abstract_en'):
            from .figure_standards import _chinese_number
            match=re.match(r'^\s*第([一二两三四五六七八九十零\d]+)章\s*[、.．]?\s*(.*)$',title)
            if match:return str(_chinese_number(match[1]))+' '+match[2]
        return title
    def directories():
        has_directory=False
        if options.get('include_toc',True):
            doc.add_paragraph('目录',style='TOC Heading')
            entries=[display_title(x['section'])+'　页码待更新' for x in snapshots if not(component_styles.get('toc',{}).get('exclude_abstract') and x['section']['kind'] in ('abstract','abstract_en'))]
            field(doc.add_paragraph(),' TOC \\o "1-3" \\h \\z \\u ','\n'.join(entries));has_directory=True
        if figure_policy['include_directory']:
            for node_kind,title_text,sequence_name in (('figure','插图目录','Figure'),('table','表格目录','Table')):
                entries=[r for r in figure_rows if r['kind']==node_kind]
                if not entries:continue
                if has_directory:doc.add_page_break()
                doc.add_paragraph(title_text,style='TOC Heading')
                field(doc.add_paragraph(),' TOC \\h \\z \\c "'+sequence_name+'" ','\n'.join(r['label']+' '+(r['caption'] or '【题名待补】')+'　页码待更新' for r in entries));has_directory=True
        return has_directory
    from .ooxml_preserve import author_year_labels,add_footnotes,omml
    citation_style=options.get("citation_style","numeric")
    if citation_style not in ("numeric","author_year"):raise ValueError("引用样式无效")
    year_labels=author_year_labels(refs);footnotes={}
    fig_count=0;table_count=0;active_sid=None;sequences=set();bookmark_count=500000000
    def figure_caption(node,default=''):
        nonlocal bookmark_count
        from .paper_tables import add_caption
        from .documents import normalize_workflow_terms
        attrs=node.get('attrs',{});row=figure_lookup.get((active_sid,attrs.get('nodeId')))
        if not row:return add_caption(doc,normalize_workflow_terms(attrs.get('caption') or default))
        paragraph=add_caption(doc,'');name='Figure' if node['type']=='figure' else 'Table'
        bookmark_count+=1;start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(bookmark_count));start.set(qn('w:name'),row['bookmark']);paragraph._p.append(start)
        prefix=('图' if node['type']=='figure' else '表')+(' ' if figure_policy['caption_space'] else '')
        if figure_policy['numbering']=='chapter' and row['chapter'] and not native_numbering:prefix+=str(row['chapter'])+'-'
        paragraph.add_run(prefix)
        if figure_policy['numbering']=='chapter' and row['chapter'] and native_numbering:
            field(paragraph,' STYLEREF 1 \\n ',row['chapter'])
            paragraph.add_run('-')
        key=(name,row['chapter'] if figure_policy['numbering']=='chapter' else None)
        reset=' \\r 1' if key not in sequences and figure_policy['numbering']=='chapter' else ''
        field(paragraph,' SEQ '+name+reset+' \\* ARABIC ',row['index']);sequences.add(key)
        end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),str(bookmark_count));paragraph._p.append(end)
        paragraph.add_run(' '+normalize_workflow_terms(attrs.get('caption') or default or '【题名待补】'))
        apply_component(paragraph,'caption')
        return paragraph
    def inline(par,n):
        typ=n.get('type');attrs=n.get('attrs',{})
        if typ=='text':
            from .documents import normalize_workflow_terms
            run=par.add_run(normalize_workflow_terms(n.get('text','')));marks={m['type'] for m in n.get('marks',[])};run.bold='bold' in marks;run.italic='italic' in marks;run.font.strike='strike' in marks
            run.underline='underline' in marks
            if 'superscript' in marks:run.font.superscript=True
            if 'subscript' in marks:run.font.subscript=True
            if 'code' in marks:run.font.name='Consolas'
        elif typ=='inlineFormula':
            from .math_office import convert
            par._p.append(omml(attrs.get('omml') or convert(attrs.get('latex') or attrs.get('expression',''))))
        elif typ=='citation':
            rid=attrs.get('citationId')
            label=('（'+year_labels[rid]+'）') if citation_style=='author_year' else ('['+str(numbering[rid])+']')
            citation_link(par,rid,label,citation_style=='numeric' and bool(fmt['reference_superscript']))
        elif typ=='footnote':
            identifier=100000+len(footnotes);footnotes[identifier]=attrs.get('noteText','');run=par.add_run();run.font.superscript=True;reference=OxmlElement('w:footnoteReference');reference.set(qn('w:id'),str(identifier));run._r.append(reference)
        elif typ=='hardBreak': par.add_run().add_break()
        elif typ=='crossReference':
            target=resolve_reference(figure_rows,attrs,active_sid)
            if target:field(par,' REF '+target['bookmark']+' \\h ',target['label'])
            else:par.add_run('【图表引用失效】')
        else:
            for ch in n.get('content',[]): inline(par,ch)
    def format_paragraph(paragraph,heading=False,cell=False):
        current=section_formats.get(active_sid,fmt)
        if not heading:paragraph.paragraph_format.line_spacing=Pt(current['line_spacing_points']) if current.get('line_spacing_points') else float(current['line_spacing'])
        if not heading and current.get('alignment'):paragraph.paragraph_format.alignment={'left':0,'center':1,'right':2,'justify':3}[current['alignment']]
        if not heading:
            ind=paragraph._p.get_or_add_pPr().find(qn('w:ind'))
            if ind is None:ind=OxmlElement('w:ind');paragraph._p.get_or_add_pPr().append(ind)
            ind.set(qn('w:firstLineChars'),str(0 if cell else int(float(current['first_line_chars'])*100)))
            for run in paragraph_runs(paragraph):
                run.font.size=Pt(float(current['font_size']))
                if run.font.name!='Consolas':run.font.name=current['latin_font']
                run._r.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),current['font'])
            section_kind=next((s['kind'] for s in sections if s['id']==active_sid),None)
            key='abstract_keywords' if section_kind=='abstract' else 'abstract_en_keywords' if section_kind=='abstract_en' else None
            if key and component_styles.get(key) and paragraph.runs:
                first=paragraph.runs[0];match=re.match(r'\s*(?:【?关键词】?|Keywords|Key words)[:：]?',first.text,re.I)
                if match:
                    if match.end()<len(first.text):
                        from copy import deepcopy
                        from docx.text.run import Run
                        remainder=Run(deepcopy(first._r),paragraph);remainder.text=first.text[match.end():];first._r.addnext(remainder._r)
                    first.text='【关键词】' if component_styles[key].get('label_brackets') else match[0]
                    apply_component(paragraph,key,runs=[first])
    def block(node,list_style=None,keep_next=False):
        nonlocal fig_count,table_count
        typ=node['type'];attrs=node.get('attrs',{})
        if typ in ('bulletList','orderedList'):
            children=node.get('content',[])
            for index,child in enumerate(children):block(child,'List Number' if typ=='orderedList' else 'List Bullet',index<len(children)-1)
        elif typ=='listItem':
            for child in node.get('content',[]):block(child,list_style,keep_next)
        elif typ=='blockquote':
            for child in node.get('content',[]):
                if child['type']=='paragraph':
                    para=doc.add_paragraph(style='Quote');inline(para,child);format_paragraph(para)
                else:block(child,list_style,keep_next)
        elif typ in ('paragraph','heading'):
            heading_level=min(max(int(attrs.get('level',2)),1),3)
            para=doc.add_paragraph(style=f"Heading {heading_level}" if typ=='heading' else list_style)
            if typ=='heading' and native_numbering:
                from .format_sections import heading_text,number_heading
                para.add_run(heading_text(documents.plain(node),heading_level))
                number_heading(para,heading_number_id,heading_level)
                apply_component(para,'heading_'+str(heading_level))
            else:inline(para,node)
            format_paragraph(para,typ=='heading')
            if list_style:para.paragraph_format.keep_with_next=keep_next
        elif typ=='table':
            from .paper_tables import style_three_line,add_caption,unindent
            table_count+=1
            figure_caption(node,'【表题待补】')
            rows=node.get('content',[]);cols=max((sum(max(1,int(c.get('attrs',{}).get('colspan',1))) for c in r.get('content',[])) for r in rows),default=1);table=doc.add_table(rows=len(rows),cols=cols);table.style='Table Grid'
            from docx.enum.table import WD_TABLE_ALIGNMENT,WD_CELL_VERTICAL_ALIGNMENT
            table.alignment=WD_TABLE_ALIGNMENT.CENTER;table.autofit=False
            width=Mm(210-float(fmt['margin_left'])-float(fmt['margin_right']))
            weights=attrs.get('columnWidths') or [1]*cols
            if len(weights)!=cols or any(not isinstance(x,(int,float)) or x<=0 for x in weights):raise ValueError('表格列宽比例无效')
            widths=[int(width*x/sum(weights)) for x in weights]
            for col,col_width in zip(table.columns,widths):col.width=col_width
            for table_row in table.rows:
                for cell,col_width in zip(table_row.cells,widths):cell.width=col_width
            occupied=set()
            for i,row in enumerate(rows):
                j=0
                for source_cell in row.get('content',[]):
                    while (i,j) in occupied:j+=1
                    cell_attrs=source_cell.get('attrs',{});cs=max(1,int(cell_attrs.get('colspan',1)));rs=max(1,int(cell_attrs.get('rowspan',1)))
                    if j+cs>cols or i+rs>len(rows):raise ValueError('表格合并范围超出实际行列')
                    target=table.cell(i,j)
                    if cs>1 or rs>1:target=target.merge(table.cell(i+rs-1,j+cs-1))
                    occupied.update((r,c) for r in range(i,i+rs) for c in range(j,j+cs))
                    target.text='';target.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    blocks=source_cell.get('content',[])
                    for k,part in enumerate(blocks):
                        paragraph=target.paragraphs[0] if k==0 else target.add_paragraph()
                        inline(paragraph,part);format_paragraph(paragraph,cell=True)
                        if source_cell['type']=='tableHeader':
                            for run in paragraph_runs(paragraph):run.bold=True
                    j+=cs
                if any(c['type']=='tableHeader' for c in row.get('content',[])):
                    header=OxmlElement('w:tblHeader');header.set(qn('w:val'),'true');table.rows[i]._tr.get_or_add_trPr().append(header)
            if attrs.get('tableStyle')=='three-line':style_three_line(table)
            for row_index,table_row in enumerate(table.rows):
                for table_cell in table_row.cells:
                    for paragraph in table_cell.paragraphs:
                        if attrs.get('fontSize'):
                            for run in paragraph_runs(paragraph):run.font.size=Pt(float(attrs['fontSize']))
                        if attrs.get('bodyAlignment') and row_index>0:paragraph.alignment={'left':0,'center':1,'right':2,'justify':3}[attrs['bodyAlignment']]
            if attrs.get('pageBreakRows'):
                from .table_continuation import split_word
                entry=figure_lookup.get((active_sid,attrs.get('nodeId')))
                table=split_word(doc,table,rows,attrs['pageBreakRows'],
                    ((entry['label']+' ') if entry else '')+(attrs.get('caption') or '【表题待补】'))
            if attrs.get('note'):
                from .paper_tables import keep_note_with_table
                keep_note_with_table(table)
                from .documents import normalize_workflow_terms
                note=doc.add_paragraph('注：'+normalize_workflow_terms(attrs['note']));unindent(note)
        elif typ=='figure':
            aid=attrs.get('artifactId');artifact=artifacts.get(attrs.get('assetVersionId') or aid)
            if not artifact or not artifact['sha256']: raise ValueError('图片资源缺失')
            try:
                from PIL import Image
                with Image.open(safe_object(artifact['sha256'])) as image:iw,ih=image.size
                metadata=db.loads(artifact.get('metadata'),{}) if isinstance(artifact.get('metadata'),str) else artifact.get('metadata',{}) or {}
                desired=metadata.get('delivery_spec',{}).get('width_mm',145)
                width=min(desired,210-float(fmt['margin_left'])-float(fmt['margin_right']),180*iw/ih)
                from .visual_delivery import quality as measure_figure
                figure_delivery_results.append({'artifact_id':artifact['id'],'section_id':active_sid,
                    **measure_figure({**metadata,'width':iw},width)})
                shape=doc.add_picture(str(safe_object(artifact['sha256'])),width=Mm(width))
                metadata=db.loads(artifact.get('metadata'),{}) if isinstance(artifact.get('metadata'),str) else artifact.get('metadata',{}) or {}
                svg_hash=attrs.get('svgSha256') or metadata.get('svg_sha256')
                if svg_hash:
                    from .docx_vector import attach_svg
                    attach_svg(doc,shape,safe_object(svg_hash).read_bytes())
                from .paper_tables import unindent
                picture=doc.paragraphs[-1];picture.alignment=1;unindent(picture);picture.paragraph_format.keep_with_next=True
                picture.paragraph_format.line_spacing=1
            except Exception as e: raise ValueError('图片格式不能导出，请使用PNG/JPEG') from e
            fig_count+=1
            from .paper_tables import add_caption
            caption=figure_caption(node,artifact['name']);caption.paragraph_format.keep_with_next=False
        elif typ=='formula':
            para=doc.add_paragraph()
            if attrs.get('omml'):para._p.append(omml(attrs['omml']))
            elif attrs.get('latex') or attrs.get('expression'):
                from .math_office import convert
                para._p.append(omml(convert(attrs.get('latex') or attrs.get('expression'))))
            else:
                math=OxmlElement('m:oMath');run=OxmlElement('m:r');t=OxmlElement('m:t');t.text=attrs.get('expression','');run.append(t);math.append(run);para._p.append(math)
        else:
            for ch in node.get('content',[]): block(ch)
    def render_section(item):
        nonlocal active_sid
        active_sid=item['section']['id']
        s=item['section'];parents={x['id']:x.get('parent_id') for x in sections};level=1;parent=parents.get(s['id']);seen=set()
        while parent and parent not in seen:seen.add(parent);level+=1;parent=parents.get(parent)
        heading_name='abstract_heading' if s['kind']=='abstract' else 'abstract_en_heading' if s['kind']=='abstract_en' else 'heading_'+str(min(level,3))
        if s['kind'] in ('abstract','abstract_en') and component_styles.get('toc',{}).get('exclude_abstract'):
            if 'Abstract Heading' not in doc.styles:
                from docx.enum.style import WD_STYLE_TYPE
                style=doc.styles.add_style('Abstract Heading',WD_STYLE_TYPE.PARAGRAPH);style.base_style=doc.styles['Heading 1'];style.paragraph_format.page_break_before=False
                outline=OxmlElement('w:outlineLvl');outline.set(qn('w:val'),'9');style.element.get_or_add_pPr().append(outline)
            heading=doc.add_paragraph(display_title(s),style='Abstract Heading')
        else:heading=doc.add_heading(display_title(s),min(level,3))
        if s['kind'] not in ('abstract','abstract_en') and level==1:heading.style=doc.styles[chapter_style]
        if native_numbering and s['kind'] not in ('abstract','abstract_en'):
            from .format_sections import number_heading
            number_heading(heading,heading_number_id,min(level,3))
        apply_component(heading,heading_name)
        if body_items and s['id']==body_items[0]['section']['id'] and (not preserve or separate_frontmatter or has_directory):heading.paragraph_format.page_break_before=False
        md.extend(['#'*min(level+1,6)+' '+display_title(s),'',markdown_document(item['document'],figure_rows,s['id']),''])
        for node in item['document'].get('content',[]): block(node)
    front=[item for item in snapshots if item['section']['kind'] in ('abstract','abstract_en')]
    body_items=[item for item in snapshots if item['section']['kind'] not in ('abstract','abstract_en')]
    for item in front:render_section(item)
    if front:doc.add_page_break()
    has_directory=directories()
    if separate_frontmatter:
        body_section=new_numbered_section(doc);set_numbering(body_section,document_policy.get('body_numbering','decimal'))
        from .format_sections import body_headers
        body_headers(doc,body_section,{**document_policy,'chapter_style':chapter_style},p['title'],field,display_title(body_items[0]['section']) if body_items else '')
    elif has_directory:doc.add_page_break()
    for item in body_items:render_section(item)
    bibliography_heading=doc.add_heading('参考文献',1);bibliography_heading.style=doc.styles[chapter_style];md.extend(['## 参考文献',''])
    apply_component(bibliography_heading,'reference_heading')
    if native_numbering:
        props=OxmlElement('w:numPr');disabled=OxmlElement('w:numId');disabled.set(qn('w:val'),'0');props.append(disabled);bibliography_heading._p.get_or_add_pPr().append(props)
    for rid,bib in sorted(refs.items(),key=lambda item:year_labels[item[0]]) if citation_style=='author_year' else refs.items():
        line=('' if citation_style=='author_year' else f'[{numbering[rid]}] ')+format_bibliography(db.loads(bib['metadata'],{}));paragraph=doc.add_paragraph(line)
        bookmark_reference(paragraph,rid)
        from .paper_tables import unindent
        unindent(paragraph);apply_component(paragraph,'references_body')
        # Bibliographic prose must wrap naturally; distributed justification of
        # a short final line creates the conspicuous gaps seen in older exports.
        reference_style=component_styles.get('references_body',{})
        if 'alignment' not in reference_style:paragraph.alignment=0
        if not any(key in reference_style for key in ('first_line_chars','hanging_chars')):
            paragraph.paragraph_format.left_indent=Pt(float(fmt['font_size'])*2)
            paragraph.paragraph_format.first_line_indent=Pt(-float(fmt['font_size'])*2)
        wrapping=OxmlElement('w:wordWrap');wrapping.set(qn('w:val'),'true');paragraph._p.get_or_add_pPr().append(wrapping)
        if bibliography_hyphenation:
            suppress=OxmlElement('w:suppressAutoHyphens');suppress.set(qn('w:val'),'0');paragraph._p.get_or_add_pPr().append(suppress)
        for run in paragraph.runs:
            props=run._r.get_or_add_rPr();fonts=props.get_or_add_rFonts();fonts.set(qn('w:eastAsia'),fmt['font']);fonts.set(qn('w:ascii'),fmt['latin_font']);fonts.set(qn('w:hAnsi'),fmt['latin_font'])
            spacing=props.find(qn('w:spacing'))
            if spacing is None:spacing=OxmlElement('w:spacing');props.append(spacing)
            spacing.set(qn('w:val'),'0')
            if bibliography_hyphenation:
                language=props.find(qn('w:lang'))
                if language is None:language=OxmlElement('w:lang');props.append(language)
                language.set(qn('w:val'),'en-US')
        md.append(line)
    for index,sec in enumerate(doc.sections):
        if separate_frontmatter and index==0:continue
        footers=[sec.footer]
        if document_policy.get('chapter_headers') and not preserve:
            sec.even_page_footer.is_linked_to_previous=False;footers.append(sec.even_page_footer)
        for item in footers:
            footer=item.paragraphs[0];footer.alignment=1
            from .paper_tables import unindent
            unindent(footer);footer.paragraph_format.line_spacing=1
            if not re.search(r'\bPAGE\b',' '.join(e.text or '' for e in item._element.iter(qn('w:instrText')))):field(footer,' PAGE ')
    update=OxmlElement('w:updateFields');update.set(qn('w:val'),'true');doc.settings.element.append(update)
    filename='论文.docx';doc.save(folder/filename);add_footnotes(folder/filename,footnotes)
    preservation=None
    if preserve:
        from .template_merge import graft
        output,preservation=graft(safe_object(template).read_bytes(),(folder/filename).read_bytes());(folder/filename).write_bytes(output)
    markdown='\n'.join(md)
    for rid,num in numbering.items(): markdown=markdown.replace('[cite:'+rid+']','（'+year_labels[rid]+'）' if citation_style=='author_year' else f'[{num}]')
    (folder/'论文.md').write_text(markdown,encoding='utf-8')
    report={'project_title':p['title'],'created_at':db.now(),'findings':findings,'visual_check':'not_run','template_preservation':preservation,'citation_style':citation_style,'footnotes':len(footnotes),'field_update':'请在目标Word软件更新目录、图表目录、交叉引用和页码','format_profile':fmt,'figure_checks':figure_report,'figure_numbering':figure_policy['numbering']}
    report['format_resolution']=format_resolution
    report['figure_delivery']=figure_delivery_results
    report['effective_component_styles']=component_styles;report['effective_document_policy']=document_policy
    pdf=False;tool=renderer()
    needs_field_update=has_directory or bool(figure_rows) or preserve or citation_style=='author_year' or footnotes or document_policy.get('frontmatter_numbering') or document_policy.get('chapter_headers') or any(n.get('attrs',{}).get('omml') for item in snapshots for n in documents.nodes(item['document']))
    if options.get('pdf') and needs_field_update and tool!='office-com':
        from .office_renderer import capabilities
        available=capabilities()
        if available.get('powershell') and {'Word.Application','KWPS.Application'}.intersection(available.get('registered_com',[])):
            tool='office-com'
    if options.get('pdf') and (preserve or citation_style=='author_year' or footnotes or document_policy.get('frontmatter_numbering') or document_policy.get('chapter_headers') or any(n.get('attrs',{}).get('omml') for item in snapshots for n in documents.nodes(item['document']))) and not tool:
        report['pdf_status']='requires_office_renderer_for_advanced_content'
    elif options.get('pdf') and tool:
        try:
            if tool=='office-com':
                from .office_renderer import render_path
                import tempfile,os
                with tempfile.TemporaryDirectory(prefix='export-fields-',dir=DATA/'tmp') as temporary:
                    updated=Path(temporary)/'updated.docx'
                    report['office_render']=render_path(folder/filename,folder/'论文.pdf','docx',updated_docx=updated)
                    os.replace(updated,folder/filename)
                report['field_update']='目录、图表目录、交叉引用与页码已在本次Office渲染中更新，并保存到导出的Word副本'
            else:
                r=subprocess.run([tool,'--headless','--convert-to','pdf','--outdir',str(folder),str(folder/filename)],capture_output=True,timeout=120)
            pdf=(folder/'论文.pdf').exists();report['pdf_status']='generated' if pdf else 'conversion_failed'
        except (ValueError,subprocess.SubprocessError) as error:
            pdf=False;report['pdf_status']='office_render_failed';report['pdf_error']=str(error)[:800]
    elif options.get('pdf'):
        try:
            from .pdf_render import render_pdf
            render_pdf(folder/'论文.pdf',p,snapshots,refs,numbering,fmt,artifacts,figure_rows,bibliography_formatter=format_bibliography)
            pdf=True;report['pdf_status']='generated_independent_layout';report['pdf_renderer']='ReportLab；PDF独立排版，不代表Word分页已验收'
        except Exception as exc: report['pdf_status']='renderer_unavailable';report['pdf_error']=str(exc)[:300]
    report['docx_property_checks']=check_docx(folder/filename,fmt,preservation.get('body_section_index') if preservation else None)
    from .academic_standards import check_docx as check_academic_docx
    report['academic_standard_checks']=check_academic_docx(folder/filename,frozen['settings']['academic_standards:'+pid])
    report['snapshot_consistency']='read_transaction'
    report['content_revision_ids']=[x['revision']['id'] for x in snapshots]
    expected_citations=sum(n['type']=='citation' for item in snapshots for n in documents.nodes(item['document']))
    report['reference_navigation']=navigation_report(folder/filename,expected_citations,len(refs))
    if options.get('include_submission_copy',True):
        from .export_submission import write_submission
        report['submission_copy']=write_submission(folder,p,snapshots,refs,numbering,year_labels,artifacts,figure_rows,fmt,citation_style,format_bibliography)
    (folder/'检查报告.json').write_text(db.dumps(report),encoding='utf-8');(folder/'检查报告.md').write_text('# 论文检查报告\n\n视觉检查：未人工验收。\n\n'+'\n'.join(f"- [{f['result']}] {f['message']}" for f in findings['findings']),encoding='utf-8')
    manifest={'project':p,'rule_package':package,'sections':[{'id':x['section']['id'],'revision_id':x['revision']['id']} for x in snapshots],'references':refs,'format':fmt,'template_sha256':template,'created_at':db.now(),'mode':options.get('mode','draft'),'visual_check':'not_run','pdf':pdf,'research_facts':frozen['facts'],'file_versions':frozen['file_versions'],'docx_property_checks':report['docx_property_checks'],'snapshot_at':frozen['created_at']}
    manifest['academic_standards']=frozen['settings']['academic_standards:'+pid]
    report['bibliography']=prepared['checks']['bibliography']
    (folder/'参考文献格式核对.json').write_text(db.dumps(report['bibliography']),encoding='utf-8')
    (folder/'版本清单.json').write_text(db.dumps(manifest),encoding='utf-8')
    evidence_mapping=[{'section_id':x['section']['id'],'revision_id':x['revision']['id'],'node_id':n.get('attrs',{}).get('nodeId'),'text':documents.plain(n),'source_ids':n.get('attrs',{}).get('sourceIds',[]),'fact_ids':n.get('attrs',{}).get('factIds',[]),'evidence_ids':n.get('attrs',{}).get('evidenceIds',[]),'knowledge_unit_ids':n.get('attrs',{}).get('knowledgeUnitIds',[]),'reading_ids':n.get('attrs',{}).get('documentReadingIds',[])} for x in snapshots for n in documents.nodes(x['document']) if any(n.get('attrs',{}).get(key) for key in ('sourceIds','factIds','evidenceIds','knowledgeUnitIds','documentReadingIds'))]
    (folder/'段落证据映射.json').write_text(db.dumps(evidence_mapping),encoding='utf-8')
    if frozen['citations']:
        (folder/'引用采用时与当前书目信息.json').write_text(db.dumps([{'reference_id':c['reference_id'],'revision_id':c['revision_id'],'node_id':c['node_id'],'adopted_metadata':db.loads(c['reference_snapshot'],{}),'current_metadata':db.loads(frozen['references'].get(c['reference_id'],{}).get('metadata'),{}),'support':c['support']} for c in frozen['citations']]),encoding='utf-8')
    if frozen.get('project_evidence'):
        from .writing_agent.common import unblob
        (folder/'项目代码与证据关联.json').write_text(db.dumps({identity:{**row,'payload':unblob(row['payload_hash'])} for identity,row in frozen['project_evidence'].items()}),encoding='utf-8')
    from PIL import Image
    for item in snapshots:
        for node in documents.nodes(item['document']):
            if node['type']=='figure':
                artifact=artifacts.get(node.get('attrs',{}).get('assetVersionId') or node.get('attrs',{}).get('artifactId'))
                if artifact and artifact['sha256']:
                    version_name='图-'+(node.get('attrs',{}).get('assetVersionId') or artifact['id'])+'.png'
                    with Image.open(safe_object(artifact['sha256'])) as image:image.convert('RGB').save(folder/version_name)
                    legacy_name=folder/('图-'+artifact['id']+'.png')
                    if not legacy_name.exists():legacy_name.write_bytes((folder/version_name).read_bytes())
    (folder/'研究过程与资产版本.json').write_text(db.dumps(frozen.get('research_trace',{})),encoding='utf-8')
    bundle=folder/'论文交付包.zip'
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
        for path in folder.iterdir():
            if path.is_file() and path!=bundle and not path.name.endswith('.pending.pdf'): z.write(path,path.name)
    db.execute('INSERT INTO exports VALUES(?,?,?,?,?,?)',(export_id,pid,db.dumps(manifest),filename,'ready',db.now()))
    return {'export_id':export_id,'files':[f.name for f in folder.iterdir() if f.is_file() and not f.name.endswith('.pending.pdf')],'pdf':pdf,'visual_check':'not_run','findings_summary':findings['summary']}

def backup():
    bid=db.uid();tmp=DATA/'tmp'/f'{bid}.sqlite3'
    src=db.connect();dst=sqlite3.connect(tmp)
    try: src.backup(dst)
    finally: dst.close();src.close()
    # Backups retain frozen bytes but require explicitly rebinding local roots.
    portable=sqlite3.connect(tmp)
    try:
        if portable.execute("SELECT 1 FROM sqlite_master WHERE name='repository_bindings'").fetchone():
            portable.execute("UPDATE repository_bindings SET root_path_local='',egress_policy='local_only',scope_revision=scope_revision+1")
        portable.commit()
    finally:portable.close()
    entries={'workbench.sqlite3':tmp};entries.update({'objects/'+p.name:p for p in (DATA/'objects').iterdir() if p.is_file()})
    graph_snapshot=None
    graph_path=Path(db.DB_PATH).parent/'ai-graph-checkpoints.sqlite3'
    if graph_path.is_file():
        graph_snapshot=DATA/'tmp'/f'{bid}-graph.sqlite3'
        source=sqlite3.connect(graph_path.as_uri()+'?mode=ro',uri=True)
        target=sqlite3.connect(graph_snapshot)
        try:source.backup(target)
        finally:target.close();source.close()
        entries['ai-graph-checkpoints.sqlite3']=graph_snapshot
    resource_roots={}
    check=sqlite3.connect(tmp);check.row_factory=sqlite3.Row
    try:
        if check.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_artifacts'").fetchone():
            for artifact in check.execute("SELECT id,path,kind FROM knowledge_artifacts WHERE kind!='knowledge_bundle'"):
                original=Path(artifact['path']).resolve()
                if not original.exists() or not original.is_relative_to(DATA.resolve()):continue
                root='knowledge-resources/'+artifact['id'];resource_roots[artifact['id']]={'root':root,'directory':original.is_dir(),'old_path':artifact['path']}
                for resource in original.rglob('*') if original.is_dir() else [original]:
                    if resource.is_file() and not resource.is_symlink():entries[root+'/'+(resource.relative_to(original).as_posix() if original.is_dir() else resource.name)]=resource
    finally:check.close()
    manifest={'version':2,'created_at':db.now(),'files':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in entries.items()},'knowledge_resources':resource_roots,'credentials_included':False,
              'ai_graph_recovery':'business_receipts_authoritative_checkpoints_optional'}
    path=DATA/'backups'/f'{bid}.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,source in entries.items(): z.write(source,name)
        z.writestr('manifest.json',db.dumps(manifest))
    tmp.unlink(missing_ok=True)
    if graph_snapshot:graph_snapshot.unlink(missing_ok=True)
    db.execute('INSERT INTO backups VALUES(?,?,?,?)',(bid,path.name,db.dumps(manifest),db.now()))
    return {'id':bid,'filename':path.name,'credentials_included':False}
def restore_backup(data):
    rid=db.uid();target=DATA/'restored'/rid
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist())>2_000_000_000: raise ValueError('备份解压体积超过上限')
        manifest=db.loads(z.read('manifest.json'),{})
        if not manifest.get('files') or 'workbench.sqlite3' not in manifest['files']: raise ValueError('备份缺少清单或数据库')
        checked=[]
        for name,expected in manifest['files'].items():
            dest=(target/name).resolve()
            if not dest.is_relative_to(target.resolve()) or name not in z.namelist(): raise ValueError('备份路径无效')
            raw=z.read(name)
            if hashlib.sha256(raw).hexdigest()!=expected: raise ValueError('备份哈希校验失败')
            checked.append((dest,raw))
        for dest,raw in checked: dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
    if manifest.get('knowledge_resources'):
        restored=sqlite3.connect(target/'workbench.sqlite3')
        try:
            for identity,entry in manifest['knowledge_resources'].items():
                resource=target/entry['root'];new=resource if entry['directory'] else next(resource.iterdir())
                row=restored.execute('SELECT manifest_json FROM knowledge_artifacts WHERE id=?',(identity,)).fetchone()
                if row:
                    meta=db.loads(row[0],{});meta.get('packet',{}).update(folder=str(new))
                    restored.execute('UPDATE knowledge_artifacts SET path=?,manifest_json=? WHERE id=?',(str(new),db.dumps(meta),identity))
            restored.commit()
        finally:restored.close()
    # Preserve database layout expected by the launcher.
    return {'restored_data_directory':str(target),'files_verified':len(checked),'message':'已恢复隔离副本。使用启动脚本 -DataDirectory 指向此目录，不覆盖当前项目。'}
