"""Read-only audit of a new revision snapshot and its actual Word/PDF/ZIP files.

No database, model, adoption, Office automation, or rendering is used. A visual
receipt is accepted only when it binds the current revision and both files.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import runner  # noqa: E402,F401 - initialize the requested application runtime.
from lxml import etree as E  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
INTERNAL=re.compile(r'\[(?:cite|figref|source|evidence|knowledge_unit|document_reading):[^\]]*\]')
FIELD_ERROR=re.compile(r'Error!|Reference source not found|Bookmark not defined|错误[!！]|未定义书签|找不到引用源|页码待更新',re.I)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def compact(text):return re.sub(r'\s+','',str(text).replace('\u00ad',''))
def text(node):return ''.join(node.xpath('.//w:t/text()',namespaces=NS))
def nodes(value):
    yield value
    for child in value.get('content',[]):yield from nodes(child)
def parse(value):return json.loads(value) if isinstance(value,str) else value or {}
def title_core(value):
    value=re.sub(r'^第[一二两三四五六七八九十百零\d]+章\s*','',str(value).strip())
    return re.sub(r'^\d+(?:\.\d+){0,2}[\s、．]+','',value)


def snapshot_metrics(prepared,errors,expected_references=31):
    frozen=prepared.get('snapshot',prepared)
    sections=frozen.get('sections',[]);references=frozen.get('references',{})
    if len({s['id'] for s in sections})!=len(sections):errors.append('修订快照章节身份重复')
    chapter_sections=[s for s in sections if s.get('kind') not in ('abstract','abstract_en') and not s.get('parent_id')]
    if len(chapter_sections)!=7:errors.append('修订快照未包含完整七章')
    for kind in ('abstract','abstract_en'):
        if sum(s.get('kind')==kind for s in sections)!=1:errors.append('修订快照摘要数量不符：'+kind)
    if isinstance(references,list):references={r['id']:r for r in references}
    order=[];citations=[];heading_counts=Counter();long_prose=[];figures=tables=crossrefs=0
    for section in sections:
        revision=frozen.get('revisions',{}).get(section['id'])
        if not revision:
            errors.append('修订快照缺少章节正文：'+section.get('title',section['id']));continue
        document=parse(revision.get('document'))
        if section.get('kind') not in ('abstract','abstract_en'):
            level=1;parent=section.get('parent_id');seen=set();by_id={s['id']:s for s in sections}
            while parent in by_id and parent not in seen:seen.add(parent);level+=1;parent=by_id[parent].get('parent_id')
            heading_counts[min(level,3)]+=1
        for node in nodes(document):
            typ=node.get('type');attrs=node.get('attrs',{})
            if typ=='citation':
                rid=attrs.get('citationId');citations.append(rid)
                if rid not in references:errors.append('修订快照存在悬空引用：'+str(rid))
                elif rid not in order:order.append(rid)
            elif typ=='heading':heading_counts[min(max(int(attrs.get('level',2)),1),3)]+=1
            elif typ=='figure':figures+=1
            elif typ=='table':tables+=1
            elif typ=='crossReference':crossrefs+=1
            elif typ=='text' and len(compact(node.get('text','')))>=30:long_prose.append(node['text'])
    if len(references)!=expected_references:errors.append(f'书目数量为{len(references)}，目标{expected_references}')
    unused=sorted(set(references)-set(citations))
    if unused:errors.append('存在未在正文引用的书目：'+','.join(unused))
    identities=[]
    for rid,reference in references.items():
        if reference.get('id') not in (None,rid):errors.append('书目标识与映射键不同：'+rid)
        meta=parse(reference.get('metadata'))
        identity=compact(meta.get('doi') or '').lower() or compact(meta.get('title') or '').lower()
        if not identity:errors.append('书目缺少题名/DOI身份：'+rid)
        identities.append(identity)
    duplicates=[identity for identity,count in Counter(identities).items() if identity and count>1]
    if duplicates:errors.append('书目身份重复：'+','.join(duplicates))
    return frozen,{'sections':len(sections),'references':len(references),'citations':len(citations),'reference_order':order,
                   'heading_counts':dict(heading_counts),'figures':figures,'tables':tables,'cross_references':crossrefs,
                   'unused_references':unused,'duplicate_identities':duplicates,'long_prose':long_prose}


def inspect_docx(path,metrics,errors):
    with zipfile.ZipFile(path) as archive:
        body=E.fromstring(archive.read('word/document.xml'));styles=E.fromstring(archive.read('word/styles.xml'))
        namespaces={**NS};style_levels={}
        for style in styles.xpath('//w:style',namespaces=NS):
            name=style.find('w:name',NS)
            match=re.fullmatch(r'(?:heading\s*|标题\s*)([123])',name.get('{'+NS['w']+'}val',''),re.I) if name is not None else None
            if match:style_levels[style.get('{'+NS['w']+'}styleId')]=int(match[1])
        numbered=Counter();all_headings=Counter();super_numbers=[];ordinary_markers=[];bookmark_names=set()
        bibliography=False;entries=[]
        for paragraph in body.xpath('//w:p',namespaces=NS):
            value=text(paragraph);style=paragraph.find('w:pPr/w:pStyle',NS)
            level=style_levels.get(style.get('{'+NS['w']+'}val')) if style is not None else None
            if level:
                all_headings[level]+=1;num=paragraph.find('w:pPr/w:numPr/w:numId',NS)
                if num is not None and num.get('{'+NS['w']+'}val')!='0':
                    numbered[level]+=1
                    if title_core(value)!=value:errors.append('标题仍含手工章节编号：'+value)
            if value.strip()=='参考文献':bibliography=True;continue
            if bibliography and re.match(r'^\[\d+\]',value.strip()):entries.append(value.strip())
            for run in paragraph.xpath('.//w:r',namespaces=NS):
                marker=''.join(run.xpath('./w:t/text()',namespaces=NS))
                numbers=re.findall(r'\[(\d+)\]',marker)
                if not numbers:continue
                vertical=run.find('w:rPr/w:vertAlign',NS)
                if vertical is not None and vertical.get('{'+NS['w']+'}val')=='superscript':super_numbers.extend(map(int,numbers))
                elif not bibliography:ordinary_markers.extend(map(int,numbers))
        for start in body.xpath('//w:bookmarkStart',namespaces=NS):bookmark_names.add(start.get('{'+NS['w']+'}name'))
        instructions=' '.join(body.xpath('//w:instrText/text()',namespaces=NS))
        ref_targets=re.findall(r'\bREF\s+([A-Za-z_][A-Za-z0-9_]*)',instructions)
        dangling=sorted(set(ref_targets)-bookmark_names)
        if dangling:errors.append('DOCX交叉引用书签缺失：'+','.join(dangling))
        if not re.search(r'\bTOC\s+\\o\s+"1-3"',instructions):errors.append('DOCX缺少覆盖1—3级的自动目录域')
        if len(re.findall(r'\bSEQ\s+(?:Figure|Table)\b',instructions))<metrics['figures']+metrics['tables']:errors.append('DOCX自动图表题注域不足')
        if len(ref_targets)<metrics['cross_references']:errors.append('DOCX交叉引用域数量少于修订快照')
        for level,count in metrics['heading_counts'].items():
            if numbered[level]<count:errors.append(f'DOCX原生{level}级编号标题不足：{numbered[level]}/{count}')
        expected=Counter({metrics['reference_order'].index(rid)+1:count for rid,count in Counter(metrics.get('citation_ids',[])).items()})
        if expected and Counter(super_numbers)!=expected:errors.append('DOCX真正上标引用数量/编号与快照不符')
        if ordinary_markers:errors.append('DOCX正文出现非上标的数字引用标记')
        entry_numbers=[int(re.match(r'^\[(\d+)\]',entry)[1]) for entry in entries]
        if entry_numbers!=list(range(1,metrics['references']+1)):errors.append('DOCX文末书目编号不连续或数量不符')
        if set(super_numbers)!=set(range(1,metrics['references']+1)):errors.append('DOCX并非全部书目都有真实上标引用')
        if len(body.xpath('//w:tbl',namespaces=NS))!=metrics['tables']:errors.append('DOCX原生表格数量与快照不同')
        header_fields=[]
        for name in archive.namelist():
            if re.fullmatch(r'word/header\d+\.xml',name):
                header=E.fromstring(archive.read(name));codes=' '.join(header.xpath('//w:instrText/text()',namespaces=NS))
                if 'STYLEREF' in codes:
                    header_fields.append(codes)
                    if not re.search(r'STYLEREF\s+(?:1\b|"(?:Heading\s*1|标题\s*1)")',codes,re.I) or re.search(r'\\n\b',codes):errors.append('页眉未仅提取一级标题文字')
        if not header_fields:errors.append('DOCX缺少动态一级标题页眉域')
        alltext='\n'.join(body.xpath('//w:t/text()',namespaces=NS))
        if FIELD_ERROR.search(alltext):errors.append('DOCX含未更新目录/字段错误缓存')
        if INTERNAL.search(alltext):errors.append('DOCX含内部引用令牌')
        compact_docx=compact(alltext)
        missing=[s[:100] for s in metrics['long_prose'] if compact(s) not in compact_docx]
        if missing:errors.append('DOCX缺失修订快照正文片段')
        return {'paragraphs':len(body.xpath('//w:p',namespaces=NS)),'tables':metrics['tables'],'numbered_headings':dict(numbered),
                'all_headings':dict(all_headings),'native_superscript_markers':len(super_numbers),'superscript_counts':dict(Counter(super_numbers)),
                'bibliography_entries':entries,'ref_fields':len(ref_targets),'dangling_bookmarks':dangling,'header_fields':header_fields,
                'missing_prose':missing}


def logical_number(footer):
    value=compact(footer)
    if re.fullmatch(r'\d+',value):return 'decimal',int(value)
    if re.fullmatch(r'[IVXLCDM]+',value,re.I):
        numbers={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000};total=previous=0
        for char in reversed(value.upper()):
            current=numbers[char];total+=-current if current<previous else current;previous=max(previous,current)
        return 'roman',total
    return None,None


def inspect_pdf(path,frozen,metrics,errors,warnings):
    pdf=pdfium.PdfDocument(str(path));pages=[];fulltext=[];bodytexts=[];prose_regions=[];chapter=None;in_references=False
    chapters=[title_core(s['title']) for s in frozen['sections'] if s.get('kind') not in ('abstract','abstract_en') and not s.get('parent_id')]
    configured=frozen.get('settings',{}).get('format:'+frozen.get('project',{}).get('id',''),{}) or {}
    top_margin=float(configured.get('margin_top',25))*72/25.4
    bottom_margin=float(configured.get('margin_bottom',25))*72/25.4
    try:
        for index in range(len(pdf)):
            page=pdf[index];tp=page.get_textpage()
            try:
                width,height=page.get_size();value=tp.get_text_range()
                # Follow the actual body margins. A 65pt heuristic intersected
                # footer digits and header punctuation; PDFium then included
                # their entire content-stream range inside the body extraction.
                # A 75pt bottom crop also cut a genuine line-final underscore.
                header=tp.get_text_bounded(0,height-top_margin,width,height).strip()
                footer=tp.get_text_bounded(0,0,width,bottom_margin).strip()
                middle=tp.get_text_bounded(0,bottom_margin,width,height-top_margin)
            finally:tp.close();page.close()
            kind,number=logical_number(footer);fulltext.append(value);prose_regions.append(middle)
            if not compact(value):errors.append(f'PDF第{index+1}物理页没有可读文字')
            if FIELD_ERROR.search(value):errors.append(f'PDF第{index+1}物理页有字段错误/目录缓存')
            if INTERNAL.search(value):errors.append(f'PDF第{index+1}物理页有内部引用令牌')
            if '\ufffd' in value or '\x00' in value:warnings.append(f'PDF第{index+1}页文字提取含异常字符，需按图像核对')
            if kind=='decimal':
                for line in middle.splitlines():
                    stripped=compact(line)
                    for chapter_index,chapter_title in enumerate(chapters,1):
                        if stripped==str(chapter_index)+compact(chapter_title):chapter=chapter_title
                    if stripped=='参考文献':chapter='参考文献';in_references=True
                if number%2==1:
                    # 新规则：奇数页（单页）页眉=论文题目
                    expected_title=frozen['project']['title']
                    if compact(header)!=compact(expected_title):errors.append(f'PDF逻辑奇数页{number}页眉不等于论文题目：{header!r}，预期{expected_title!r}')
                else:
                    # 偶数页（双页）页眉=当前章标题
                    if chapter is None:errors.append(f'PDF逻辑偶数页{number}无法定位当前一级标题')
                    elif compact(header)!=compact(chapter):errors.append(f'PDF逻辑偶数页{number}页眉不等于章标题：{header!r}，预期{chapter!r}')
                if not in_references:bodytexts.append(middle)
                elif re.search(r'(?:^|\n)\s*参考文献\s*(?:\r?\n|$)',middle):bodytexts.append(re.split(r'(?:^|\n)\s*参考文献\s*(?:\r?\n|$)',middle,maxsplit=1)[0])
            pages.append({'physical_page':index+1,'logical_kind':kind,'logical_page':number,'header':header,'footer':footer,
                          'expected_chapter':chapter if kind=='decimal' else None,'width':width,'height':height,'characters':len(value)})
    finally:pdf.close()
    for kind in ('roman','decimal'):
        sequence=[p['logical_page'] for p in pages if p['logical_kind']==kind]
        if sequence and sequence!=list(range(1,len(sequence)+1)):errors.append('PDF'+kind+'页码不连续或未从1开始')
    no_footer=[p['physical_page'] for p in pages if p['logical_page'] is None]
    if any(p!=1 for p in no_footer):warnings.append('以下非封面页未自动识别逻辑页码：'+str(no_footer))
    body_cites=set(map(int,re.findall(r'\[\s*(\d+)\s*\]','\n'.join(bodytexts))))
    if body_cites!=set(range(1,metrics['references']+1)):errors.append('PDF正文可提取引用编号未完整覆盖书目')
    # Join body regions so a source paragraph spanning pages stays contiguous;
    # PAGE and STYLEREF stories must not be inserted into the paragraph text.
    compact_pdf=compact('\n'.join(prose_regions))
    missing=[s[:100] for s in metrics['long_prose'] if compact(s) not in compact_pdf]
    if missing:errors.append('PDF缺失修订快照正文片段')
    return {'pages':len(pages),'page_checks':pages,'body_citation_numbers':sorted(body_cites),'missing_prose':missing,'unidentified_page_numbers':no_footer}


def inspect_zip(directory,bundle,errors,excluded=()):
    if not bundle.is_file():errors.append('交付ZIP缺失');return {'status':'missing'}
    checked=[]
    with zipfile.ZipFile(bundle) as archive:
        names=[n for n in archive.namelist() if not n.endswith('/')]
        if len(names)!=len(set(names)):errors.append('ZIP成员名称重复')
        damaged=archive.testzip()
        if damaged:errors.append('ZIP成员损坏：'+damaged)
        for name in names:
            relative=PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts:errors.append('ZIP存在越界成员：'+name);continue
            local=directory.joinpath(*relative.parts)
            if not local.is_file():errors.append('ZIP成员缺少磁盘对应文件：'+name);continue
            if archive.read(name)!=local.read_bytes():errors.append('ZIP成员与磁盘最终版本不同：'+name)
            checked.append({'name':name,'sha256':sha(local),'bytes':local.stat().st_size})
        for name in ('论文.docx','论文.pdf'):
            if name not in names:errors.append('ZIP缺少最终文件：'+name)
        excluded={Path(p).resolve() for p in excluded if p}
        for local in directory.rglob('*'):
            if local.is_file() and local.resolve()!=bundle.resolve() and local.resolve() not in excluded:
                name=local.relative_to(directory).as_posix()
                if name not in names:errors.append('ZIP未包含交付目录文件：'+name)
    return {'files':len(checked),'damaged_member':damaged,'members':checked}


def audit(args):
    errors=[];warnings=[];directory=Path(args.delivery_dir).resolve();snapshot_path=Path(args.snapshot).resolve()
    prepared=json.loads(snapshot_path.read_text(encoding='utf-8-sig'))
    frozen,metrics=snapshot_metrics(prepared,errors,getattr(args,'expected_references',31))
    citation_ids=[]
    for section in frozen.get('sections',[]):
        revision=frozen.get('revisions',{}).get(section['id'])
        if revision:citation_ids.extend(n.get('attrs',{}).get('citationId') for n in nodes(parse(revision.get('document'))) if n.get('type')=='citation')
    metrics['citation_ids']=citation_ids
    paper_hash=prepared.get('revision',{}).get('paper_hash')
    if not paper_hash or not re.fullmatch(r'[a-fA-F0-9]{64}',paper_hash):errors.append('修订快照缺少有效的新paper_hash')
    from scripts.export_revised_candidate import manuscript_hash
    if paper_hash!=manuscript_hash(frozen):errors.append('修订paper_hash与有序章节/文档内容重算结果不同')
    hashes={'snapshot_sha256':sha(snapshot_path)};docx_metrics={};pdf_metrics={};zip_metrics={}
    for filename,key in [('论文.docx','docx_sha256'),('论文.pdf','pdf_sha256')]:
        path=directory/filename
        if not path.is_file():errors.append('最终交付文件缺失：'+filename);continue
        hashes[key]=sha(path)
        if filename.endswith('.docx'):docx_metrics=inspect_docx(path,metrics,errors)
        else:pdf_metrics=inspect_pdf(path,frozen,metrics,errors,warnings)
    zip_metrics=inspect_zip(directory,directory/'论文交付包.zip',errors,[getattr(args,'output',None)])
    structural=not errors;visual={'status':'pending','expected_pages':pdf_metrics.get('pages',0)}
    receipt_path=getattr(args,'visual_receipt',None)
    if receipt_path:
        receipt_path=Path(receipt_path).resolve();receipt=json.loads(receipt_path.read_text(encoding='utf-8-sig'))
        for key,expected in {**{k:v for k,v in hashes.items() if k!='snapshot_sha256'},'paper_hash':paper_hash}.items():
            if receipt.get(key)!=expected:errors.append('视觉回执未绑定最终版本：'+key)
        inspected=Counter(p.get('page') for p in receipt.get('pages',[]) if p.get('inspected') is True and p.get('clean') is True)
        if inspected!=Counter(range(1,pdf_metrics.get('pages',0)+1)):errors.append('视觉回执未逐页确认最终PDF全部页面')
        for page in receipt.get('pages',[]):
            image_path=page.get('image_path') or page.get('image') or page.get('path')
            image_hash=page.get('image_sha256') or page.get('sha256')
            if image_path and image_hash:
                image_path=Path(image_path)
                if not image_path.is_absolute():image_path=receipt_path.parent/image_path
                if not image_path.is_file() or sha(image_path)!=image_hash:errors.append('视觉回执图像缺失或哈希不同：'+str(page.get('page')))
        visual={'status':'passed' if not errors else 'failed','expected_pages':pdf_metrics.get('pages',0),'receipt_sha256':sha(receipt_path)}
    else:warnings.append('尚未提供绑定本轮最终文件哈希的逐页视觉回执')
    clean_metrics={k:v for k,v in metrics.items() if k not in ('citation_ids','long_prose')}
    return {'paper_hash':paper_hash,**hashes,'structural_passed':structural,
            'ready_for_review_delivery':not errors and visual['status']=='passed','errors':errors,'warnings':warnings,
            'snapshot':clean_metrics,'docx':docx_metrics,'pdf':pdf_metrics,'zip':zip_metrics,'visual':visual,
            'scope':'本轮修订快照与最终实物的结构、标记、字段、逻辑页和字节一致性核对；原文支持性、源码事实及学术结论另需阅读核验，不代表学校终审或作者采用。'}


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True);parser.add_argument('--delivery-dir',type=Path,required=True)
    parser.add_argument('--visual-receipt',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--expected-references',type=int,default=31)
    args=parser.parse_args();result=audit(args)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('paper_hash','structural_passed','ready_for_review_delivery','errors','warnings')},ensure_ascii=False))
    return 0 if result['ready_for_review_delivery'] else 2


if __name__=='__main__':raise SystemExit(main())
