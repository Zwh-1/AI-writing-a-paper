import io,re,hashlib,zipfile,shutil,subprocess,sqlite3
from pathlib import Path
from docx import Document
from docx.shared import Pt,Mm,Inches
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from . import db,documents,reviews
from .config import DATA,DB_PATH
from .security import safe_object,require_project
DEFAULT_FORMAT={'font':'宋体','latin_font':'Times New Roman','font_size':12,'line_spacing':1.5,'first_line_chars':2,'margin_top':25,'margin_bottom':25,'margin_left':30,'margin_right':25,'heading_font':'黑体','reference_superscript':True}
def format_config(pid): return {**DEFAULT_FORMAT,**db.setting('format:'+pid,{})}
def bibliography_text(meta):
    if meta.get('citation_raw'):return str(meta['citation_raw']).strip()
    authors=meta.get('authors','')
    if isinstance(authors,list): authors='，'.join(authors)
    return f"{authors or '【作者待补】'}. {meta.get('title') or '【题名待补】'}{(' ['+meta['type']+']') if meta.get('type') else ''}. {meta.get('journal') or meta.get('publisher') or ''}, {meta.get('year') or '【年份待补】'}{(', '+str(meta['volume'])) if meta.get('volume') else ''}{(':'+str(meta['pages'])) if meta.get('pages') else ''}.{(' DOI:'+meta['doi']) if meta.get('doi') else ''}"
def field(paragraph,code):
    r=paragraph.add_run();begin=OxmlElement('w:fldChar');begin.set(qn('w:fldCharType'),'begin');instruction=OxmlElement('w:instrText');instruction.set(qn('xml:space'),'preserve');instruction.text=code;sep=OxmlElement('w:fldChar');sep.set(qn('w:fldCharType'),'separate');end=OxmlElement('w:fldChar');end.set(qn('w:fldCharType'),'end');r._r.extend([begin,instruction,sep,end])
def set_font(style,east,latin,size):
    style.font.name=latin;style.font.size=Pt(size);style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),east)
def renderer():
    candidates=[shutil.which('soffice'),r'C:\Program Files\LibreOffice\program\soffice.exe',r'C:\Program Files (x86)\LibreOffice\program\soffice.exe']
    found=next((str(p) for p in candidates if p and Path(p).is_file()),None)
    if found:return found
    from .office_renderer import capabilities
    return 'office-com' if capabilities()['registered_com'] and capabilities()['powershell'] else None
def export_project(pid,options,tid=None):
    reviews.run(pid)
    from .export_checks import freeze,check_docx,markdown_document
    writing_report=None
    with db.transaction():
        frozen=freeze(pid)
        has_writing=db.one('SELECT id FROM thesis_core_versions WHERE project_id=? LIMIT 1',(pid,))
        if has_writing or options.get('mode')=='strict' or options.get('writing_policy')=='strict':
            from .writing_agent.paper_review import export_report
            writing_report=export_report(pid,options.get('mode')=='strict' or options.get('writing_policy')=='strict')
    p=frozen['project'];fmt={**DEFAULT_FORMAT,**(frozen['settings'].get('format:'+pid) or {})};sections=frozen['sections'];valid_revisions={x['id'] for x in frozen['revisions'].values()};report_findings=[x for x in frozen['findings'] if not x['revision_id'] or x['revision_id'] in valid_revisions];findings={'findings':report_findings,'summary':{state:sum(x['result']==state for x in report_findings) for state in ('pass','fail','unknown')}}
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
                        if snapshot: bib['metadata']=snapshot['reference_snapshot']
                        refs[rid]=bib;numbering[rid]=len(numbering)+1
    export_id=db.uid();folder=DATA/'exports'/export_id;folder.mkdir(parents=True)
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
    ind=normal.element.get_or_add_pPr().find(qn('w:ind'))
    if ind is None: ind=OxmlElement('w:ind');normal.element.get_or_add_pPr().append(ind)
    ind.set(qn('w:firstLineChars'),str(int(float(fmt['first_line_chars'])*100)))
    for level,size in ((1,16),(2,14),(3,12)):
        set_font(doc.styles[f'Heading {level}'],fmt['heading_font'],fmt['latin_font'],size)
    if not preserve:
        doc.add_heading(p['title'],0);brief=db.loads(p['brief'],{})
        for label,key in [('作者','author'),('学校','school'),('专业','major'),('指导教师','supervisor')]:
            if brief.get(key): doc.add_paragraph(label+'：'+str(brief[key]))
        doc.add_paragraph('稿件用途：'+options.get('mode','draft')+'；未决事项请查看随附报告。');doc.add_page_break();doc.add_paragraph('目录',style='TOC Heading');field(doc.add_paragraph(),' TOC \\o "1-3" \\h \\z \\u ');doc.add_page_break()
    elif options.get('include_toc',True):
        doc.add_paragraph('目录',style='TOC Heading');field(doc.add_paragraph(),' TOC \\o "1-3" \\h \\z \\u ');doc.add_page_break()
    from .ooxml_preserve import author_year_labels,add_footnotes,omml
    citation_style=options.get("citation_style","numeric")
    if citation_style not in ("numeric","author_year"):raise ValueError("引用样式无效")
    year_labels=author_year_labels(refs);footnotes={}
    fig_count=0
    def inline(par,n):
        typ=n.get('type');attrs=n.get('attrs',{})
        if typ=='text':
            run=par.add_run(n.get('text',''));marks={m['type'] for m in n.get('marks',[])};run.bold='bold' in marks;run.italic='italic' in marks;run.font.strike='strike' in marks
        elif typ=='citation':
            run=par.add_run(('（'+year_labels.get(attrs.get('citationId'),'?')+'）') if citation_style=='author_year' else ('['+str(numbering.get(attrs.get('citationId'),'?'))+']'));run.font.superscript=citation_style=='numeric' and bool(fmt['reference_superscript'])
        elif typ=='footnote':
            identifier=100000+len(footnotes);footnotes[identifier]=attrs.get('noteText','');run=par.add_run();run.font.superscript=True;reference=OxmlElement('w:footnoteReference');reference.set(qn('w:id'),str(identifier));run._r.append(reference)
        elif typ=='hardBreak': par.add_run().add_break()
        else:
            for ch in n.get('content',[]): inline(par,ch)
    def block(node):
        nonlocal fig_count
        typ=node['type'];attrs=node.get('attrs',{})
        if typ in ('paragraph','heading','blockquote','listItem'):
            para=doc.add_paragraph(style=f"Heading {min(max(int(attrs.get('level',2)),1),3)}" if typ=='heading' else None)
            inline(para,node)
        elif typ=='table':
            rows=node.get('content',[]);cols=max((len(r.get('content',[])) for r in rows),default=1);table=doc.add_table(rows=len(rows),cols=cols);table.style='Table Grid'
            for i,row in enumerate(rows):
                for j,cell in enumerate(row.get('content',[])):
                    table.cell(i,j).text=documents.plain(cell)
        elif typ=='figure':
            aid=attrs.get('artifactId');artifact=artifacts.get(aid)
            if not artifact or not artifact['sha256']: raise ValueError('图片资源缺失')
            try: doc.add_picture(str(safe_object(artifact['sha256'])),width=Mm(min(145,210-float(fmt['margin_left'])-float(fmt['margin_right']))))
            except Exception as e: raise ValueError('图片格式不能导出，请使用PNG/JPEG') from e
            fig_count+=1;doc.add_paragraph(f'图 {fig_count} '+attrs.get('caption',artifact['name']))
        elif typ=='formula':
            para=doc.add_paragraph()
            if attrs.get('omml'):para._p.append(omml(attrs['omml']))
            else:
                math=OxmlElement('m:oMath');run=OxmlElement('m:r');t=OxmlElement('m:t');t.text=attrs.get('expression','');run.append(t);math.append(run);para._p.append(math)
        else:
            for ch in node.get('content',[]): block(ch)
    for item in snapshots:
        s=item['section'];parents={x['id']:x.get('parent_id') for x in sections};level=1;parent=parents.get(s['id']);seen=set()
        while parent and parent not in seen:seen.add(parent);level+=1;parent=parents.get(parent)
        doc.add_heading(s['title'],min(level,3));md.extend(['#'*min(level+1,6)+' '+s['title'],'',markdown_document(item['document']),''])
        for node in item['document'].get('content',[]): block(node)
    doc.add_heading('参考文献',1);md.extend(['## 参考文献',''])
    for rid,bib in sorted(refs.items(),key=lambda item:year_labels[item[0]]) if citation_style=='author_year' else refs.items():
        line=('' if citation_style=='author_year' else f'[{numbering[rid]}] ')+bibliography_text(db.loads(bib['metadata'],{}));doc.add_paragraph(line);md.append(line)
    for sec in doc.sections:
        footer=sec.footer.paragraphs[0];footer.alignment=1;field(footer,' PAGE ')
    update=OxmlElement('w:updateFields');update.set(qn('w:val'),'true');doc.settings.element.append(update)
    filename='论文.docx';doc.save(folder/filename);add_footnotes(folder/filename,footnotes)
    preservation=None
    if preserve:
        from .template_merge import graft
        output,preservation=graft(safe_object(template).read_bytes(),(folder/filename).read_bytes());(folder/filename).write_bytes(output)
    markdown='\n'.join(md)
    for rid,num in numbering.items(): markdown=markdown.replace('[cite:'+rid+']','（'+year_labels[rid]+'）' if citation_style=='author_year' else f'[{num}]')
    (folder/'论文.md').write_text(markdown,encoding='utf-8')
    report={'project_title':p['title'],'created_at':db.now(),'findings':findings,'visual_check':'not_run','template_preservation':preservation,'citation_style':citation_style,'footnotes':len(footnotes),'field_update':'请在目标Word软件更新目录和页码','format_profile':fmt}
    pdf=False;tool=renderer()
    if options.get('pdf') and (preserve or citation_style=='author_year' or footnotes or any(n.get('attrs',{}).get('omml') for item in snapshots for n in documents.nodes(item['document']))) and not tool:
        report['pdf_status']='requires_office_renderer_for_advanced_content'
    elif options.get('pdf') and tool:
        try:
            if tool=='office-com':
                from .office_renderer import render_path
                report['office_render']=render_path(folder/filename,folder/'论文.pdf','docx')
            else:
                r=subprocess.run([tool,'--headless','--convert-to','pdf','--outdir',str(folder),str(folder/filename)],capture_output=True,timeout=120)
            pdf=(folder/'论文.pdf').exists();report['pdf_status']='generated' if pdf else 'conversion_failed'
        except (ValueError,subprocess.SubprocessError) as error:
            pdf=False;report['pdf_status']='office_render_failed';report['pdf_error']=str(error)[:800]
    elif options.get('pdf'):
        try:
            from .pdf_render import render_pdf
            render_pdf(folder/'论文.pdf',p,snapshots,refs,numbering,fmt,artifacts)
            pdf=True;report['pdf_status']='generated_independent_layout';report['pdf_renderer']='ReportLab；PDF独立排版，不代表Word分页已验收'
        except Exception as exc: report['pdf_status']='renderer_unavailable';report['pdf_error']=str(exc)[:300]
    report['docx_property_checks']=check_docx(folder/filename,fmt,preservation.get('body_section_index') if preservation else None)
    report['snapshot_consistency']='read_transaction'
    report['content_revision_ids']=[x['revision']['id'] for x in snapshots]
    (folder/'检查报告.json').write_text(db.dumps(report),encoding='utf-8');(folder/'检查报告.md').write_text('# 论文检查报告\n\n视觉检查：未人工验收。\n\n'+'\n'.join(f"- [{f['result']}] {f['message']}" for f in findings['findings']),encoding='utf-8')
    manifest={'project':p,'rule_package':package,'sections':[{'id':x['section']['id'],'revision_id':x['revision']['id']} for x in snapshots],'references':refs,'format':fmt,'template_sha256':template,'created_at':db.now(),'mode':options.get('mode','draft'),'visual_check':'not_run','pdf':pdf,'research_facts':frozen['facts'],'file_versions':frozen['file_versions'],'docx_property_checks':report['docx_property_checks'],'snapshot_at':frozen['created_at']}
    (folder/'版本清单.json').write_text(db.dumps(manifest),encoding='utf-8')
    evidence_mapping=[{'section_id':x['section']['id'],'revision_id':x['revision']['id'],'node_id':n.get('attrs',{}).get('nodeId'),'text':documents.plain(n),'source_ids':n.get('attrs',{}).get('sourceIds',[]),'fact_ids':n.get('attrs',{}).get('factIds',[])} for x in snapshots for n in x['document'].get('content',[]) if n.get('attrs',{}).get('sourceIds') or n.get('attrs',{}).get('factIds')]
    (folder/'段落证据映射.json').write_text(db.dumps(evidence_mapping),encoding='utf-8')
    from PIL import Image
    for item in snapshots:
        for node in documents.nodes(item['document']):
            if node['type']=='figure':
                artifact=artifacts.get(node.get('attrs',{}).get('artifactId'))
                if artifact and artifact['sha256']:
                    with Image.open(safe_object(artifact['sha256'])) as image:image.convert('RGB').save(folder/('图-'+artifact['id']+'.png'))
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
    manifest={'version':1,'created_at':db.now(),'files':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in entries.items()},'credentials_included':False}
    path=DATA/'backups'/f'{bid}.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,source in entries.items(): z.write(source,name)
        z.writestr('manifest.json',db.dumps(manifest))
    tmp.unlink(missing_ok=True);db.execute('INSERT INTO backups VALUES(?,?,?,?)',(bid,path.name,db.dumps(manifest),db.now()))
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
    # Preserve database layout expected by the launcher.
    return {'restored_data_directory':str(target),'files_verified':len(checked),'message':'已恢复隔离副本。使用启动脚本 -DataDirectory 指向此目录，不覆盖当前项目。'}
