"""Page-level local PPT rendering and source-preserving table continuations."""
import base64,html,io,json,posixpath,re,zipfile,subprocess
from pathlib import Path
from xml.etree import ElementTree as E
from . import db,security
P='http://schemas.openxmlformats.org/presentationml/2006/main';A='http://schemas.openxmlformats.org/drawingml/2006/main';R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS={'p':P,'a':A,'r':R}
def safe_color(value,default='#222222'):return '#'+value if value and re.fullmatch('[0-9a-fA-F]{6}',value) else default
def tag(ns,name):return '{'+ns+'}'+name

def slide_package(file,page):
    z=zipfile.ZipFile(security.safe_object(file['sha256']));root=E.fromstring(z.read('ppt/presentation.xml'));rels=E.fromstring(z.read('ppt/_rels/presentation.xml.rels'));targets={r.get('Id'):posixpath.normpath('ppt/'+r.get('Target')) for r in rels if r.get('TargetMode')!='External'};slides=root.findall('p:sldIdLst/p:sldId',NS)
    if not 1<=page<=len(slides):z.close();raise ValueError('幻灯片不存在')
    part=targets[slides[page-1].get(tag(R,'id'))];size=root.find('p:sldSz',NS);return z,part,float(size.get('cx',9144000)),float(size.get('cy',6858000))
def slide_tables(file,page):
    z,part,_,_=slide_package(file,page)
    try:
        root=E.fromstring(z.read(part));tables=[]
        for number,table in enumerate(root.findall('.//a:tbl',NS),1):
            rows=[]
            for row in table.findall('a:tr',NS):
                rows.append([{'text':'\n'.join(''.join(p.itertext()) for p in cell.findall('a:txBody/a:p',NS)), 'colspan':int(cell.get('gridSpan',1)),'rowspan':int(cell.get('rowSpan',1)),'merged':cell.get('hMerge')=='1' or cell.get('vMerge')=='1'} for cell in row.findall('a:tc',NS)])
            tables.append({'table':number,'page':page,'rows':rows,'source':part})
        return tables
    finally:z.close()

def native_svg(file,page):
    from .parsers import relationships
    z,part,width,height=slide_package(file,page);warnings=[];content=[]
    try:
        rels=relationships(z,part);root=E.fromstring(z.read(part));background=root.find('.//p:bg//a:srgbClr',NS);bg=safe_color(background.get('val'),'#ffffff') if background is not None else '#ffffff'
        content.append(f'<rect width="{width}" height="{height}" fill="{bg}"/>')
        def draw(node):
            kind=node.tag.rsplit('}',1)[-1];xfrm=node.find('.//a:xfrm',NS)
            if kind=='grpSp':
                if xfrm is None:
                    for child in node:draw(child)
                    return
                off=xfrm.find('a:off',NS);ext=xfrm.find('a:ext',NS);co=xfrm.find('a:chOff',NS);ce=xfrm.find('a:chExt',NS)
                if all(v is not None for v in (off,ext,co,ce)):
                    sx=float(ext.get('cx'))/max(1,float(ce.get('cx')));sy=float(ext.get('cy'))/max(1,float(ce.get('cy')));content.append(f'<g transform="translate({float(off.get("x"))} {float(off.get("y"))}) scale({sx} {sy}) translate({-float(co.get("x"))} {-float(co.get("y"))})">')
                    for child in node:
                        if child.tag not in (tag(P,'grpSpPr'),tag(P,'nvGrpSpPr')):draw(child)
                    content.append('</g>')
                return
            if kind not in ('sp','pic','graphicFrame','cxnSp'):return
            if xfrm is None:xfrm=node.find('p:xfrm',NS)
            off=xfrm.find('a:off',NS) if xfrm is not None else None;ext=xfrm.find('a:ext',NS) if xfrm is not None else None
            x=float(off.get('x',0)) if off is not None else 0;y=float(off.get('y',0)) if off is not None else 0;w=float(ext.get('cx',0)) if ext is not None else width/2;h=float(ext.get('cy',0)) if ext is not None else height/10
            angle=float(xfrm.get('rot',0))/60000 if xfrm is not None else 0;content.append(f'<g transform="translate({x} {y}) rotate({angle} {w/2} {h/2})">')
            if kind=='pic':
                blip=node.find('.//a:blip',NS);rid=blip.get(tag(R,'embed')) if blip is not None else None;target=rels.get(rid,(None,None))[0]
                if target and target in z.namelist():
                    extension=Path(target).suffix.lower();mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif'}.get(extension)
                    if mime:content.append(f'<image width="{w}" height="{h}" preserveAspectRatio="none" href="data:{mime};base64,{base64.b64encode(z.read(target)).decode()}"/>')
                    else:warnings.append({'object':'image','part':target,'reason':'图片格式需要Office渲染器'})
            elif node.find('.//a:tbl',NS) is not None:
                table=node.find('.//a:tbl',NS);rows=table.findall('a:tr',NS);cy=0
                for row in rows:
                    cells=row.findall('a:tc',NS);rh=float(row.get('h',h/max(1,len(rows))));cw=w/max(1,len(cells))
                    for i,cell in enumerate(cells):
                        text=' '.join(t.text or '' for t in cell.findall('.//a:t',NS));content.append(f'<rect x="{i*cw}" y="{cy}" width="{cw}" height="{rh}" fill="white" stroke="#808080" stroke-width="10000"/><text x="{i*cw+30000}" y="{cy+rh*.65}" font-size="120000">{html.escape(text)}</text>')
                    cy+=rh
            elif kind=='graphicFrame':warnings.append({'object':'graphicFrame','part':part,'reason':'图表、SmartArt或OLE保留原件，当前SVG无法忠实渲染'});content.append(f'<rect width="{w}" height="{h}" fill="#fff3d6" stroke="#996600" stroke-width="10000"/><text y="150000" font-size="120000">复杂对象 请查看原件或Office预览</text>')
            else:
                fill=node.find('p:spPr/a:solidFill/a:srgbClr',NS);color=safe_color(fill.get('val')) if fill is not None else 'none';content.append(f'<rect width="{w}" height="{h}" fill="{color}"/>');ty=100000
                for paragraph in node.findall('p:txBody/a:p',NS):
                    runs=paragraph.findall('a:r',NS);ppr=paragraph.find('a:pPr',NS);alignment=ppr.get('algn','l') if ppr is not None else 'l';anchor='middle' if alignment=='ctr' else 'end' if alignment=='r' else 'start';tx=w/2 if anchor=='middle' else w if anchor=='end' else 50000
                    size=max([int(r.find('a:rPr',NS).get('sz','1800')) if r.find('a:rPr',NS) is not None else 1800 for r in runs] or [1800])*127;ty+=size
                    spans=[]
                    for run in runs:
                        rp=run.find('a:rPr',NS);text=''.join(t.text or '' for t in run.findall('a:t',NS));bold=rp is not None and rp.get('b')=='1';clr=rp.find('a:solidFill/a:srgbClr',NS) if rp is not None else None;fc=safe_color(clr.get('val')) if clr is not None else '#222222';spans.append(f'<tspan fill="{fc}" font-weight="{700 if bold else 400}">{html.escape(text)}</tspan>')
                    content.append(f'<text x="{tx}" y="{ty}" text-anchor="{anchor}" font-size="{size}">'+''.join(spans)+'</text>');ty+=size*.15
            content.append('</g>')
        tree=root.find('p:cSld/p:spTree',NS)
        if tree is not None:
            for node in tree:draw(node)
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1280" height="{int(1280*height/width)}" viewBox="0 0 {width} {height}" font-family="Microsoft YaHei,Arial,sans-serif">'+''.join(content)+'</svg>'
        return {'svg':svg,'mode':'approximate_local_layout','warnings':warnings,'needs_office_visual_acceptance':True,'source_part':part}
    finally:z.close()

def continuation_candidates(file):
    pages=db.rows('SELECT * FROM pages WHERE file_id=? ORDER BY page_number',(file['id'],));previous=[];candidates=[]
    for page in pages:
        current=[]
        if file['extension']=='.pptx':
            current=slide_tables(file,page['page_number'])
            if not current and ('表' in page['title'] or '测试用例' in page['text'] or '续表' in page['text']):
                from .ppt_objects import ocr_tables
                if not db.one("SELECT id FROM sources WHERE page_id=? AND method='windows_ocr'",(page['id'],)):
                    from .local_ocr import page_ocr
                    page_ocr(page['id'])
                current=[{**t,'page':page['page_number'],'table':i} for i,t in enumerate(ocr_tables(page['id']),1)]
        elif file['extension']=='.pdf':
            import pdfplumber
            with pdfplumber.open(security.safe_object(file['sha256'])) as pdf:
                current=[{'page':page['page_number'],'table':i,'rows':[[{'text':str(c or '')} for c in row] for row in table]} for i,table in enumerate(pdf.pages[page['page_number']-1].extract_tables(),1)]
        for a in previous:
            for b in current:
                if not a['rows'] or not b['rows']:continue
                header=lambda t:[re.sub(r'\s+','',c['text']) for c in t['rows'][0]]
                if header(a)==header(b) and any(header(a)):
                    candidates.append({'from_page':a['page'],'to_page':b['page'],'tables':[a,b],'merged_rows':a['rows']+b['rows'][1:],'reason':'连续页表头一致，仅为续表候选','review_state':'pending'})
        previous=current
    return candidates
