"""Preserve complete DOCX packages; edit only explicitly selected text runs."""
import hashlib,io,re,zipfile,copy
from lxml import etree as E
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main';M='http://schemas.openxmlformats.org/officeDocument/2006/math';NS={'w':W,'m':M};XML='{http://www.w3.org/XML/1998/namespace}space'
def parse(data):return E.fromstring(data,E.XMLParser(resolve_entities=False,no_network=True))
def editable_parts(z):
    return [name for name in z.namelist() if name=='word/document.xml' or name in ('word/footnotes.xml','word/endnotes.xml') or re.fullmatch(r'word/(header|footer)\d+\.xml',name)]

def inspect_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if len(z.namelist())>20000 or sum(x.file_size for x in z.infolist())>600*1024*1024:raise ValueError('DOCX解包规模超出限制')
        runs=[];formulas=[];sections=0
        for part in editable_parts(z):
            root=parse(z.read(part));depth=0;index=0
            for element in root.iter():
                if element.tag=='{'+W+'}fldChar':
                    typ=element.get('{'+W+'}fldCharType');depth=max(0,depth+(1 if typ=='begin' else -1 if typ=='end' else 0))
                if element.tag=='{'+W+'}t':
                    runs.append({'id':index,'part':part,'text':element.text or '','editable':depth==0 and not bool(element.xpath('ancestor::m:oMath | ancestor::w:fldSimple | ancestor::w:del',namespaces=NS))});index+=1
            for i,f in enumerate(root.findall('.//m:oMath',NS)):formulas.append({'part':part,'id':i,'sha256':hashlib.sha256(E.tostring(f,method='c14n')).hexdigest(),'omml':E.tostring(f,encoding='unicode')})
            sections+=len(root.findall('.//w:sectPr',NS))
        return {'runs':runs,'formulas':formulas,'sections':sections,'footnotes':'word/footnotes.xml' in z.namelist(),'headers':[n for n in z.namelist() if re.fullmatch(r'word/(header|footer)\d+\.xml',n)],'package_sha256':hashlib.sha256(data).hexdigest(),'preservation':'保留全部OPC部件；编辑指定正文、脚注、页眉页脚文本；域结果受保护'}

def patch_docx(data,expected_sha,patches,formula_patches=None):
    if hashlib.sha256(data).hexdigest()!=expected_sha:raise ValueError('原件版本已变化，拒绝覆盖')
    if not isinstance(patches,list) or len(patches)>5000:raise ValueError('文本修改数量无效')
    formula_patches=formula_patches or [];description=inspect_docx(data);allowed={(x['part'],x['id']):x for x in description['runs']};changed={};seen=set()
    with zipfile.ZipFile(io.BytesIO(data)) as src:
        def tree(part):
            if part not in editable_parts(src):raise ValueError('该XML部件不可编辑')
            if part not in changed:changed[part]=parse(src.read(part))
            return changed[part]
        for patch in patches:
            part=patch.get('part','word/document.xml');index=patch.get('id');text=patch.get('text');key=(part,index)
            if not isinstance(index,int) or key in seen or key not in allowed or not isinstance(text,str) or len(text)>200000:raise ValueError('修改节点或文本无效')
            original=allowed[key]
            if not original['editable'] or original['text']!=patch.get('expected_text'):raise ValueError('节点不可编辑或基础文本已变化')
            node=tree(part).findall('.//w:t',NS)[index];node.text=text;node.set(XML,'preserve');seen.add(key)
        for patch in formula_patches:
            part=patch.get('part','word/document.xml');nodes=tree(part).findall('.//m:oMath',NS);index=patch.get('id')
            if not isinstance(index,int) or not 0<=index<len(nodes):raise ValueError('公式节点无效')
            original=nodes[index]
            if hashlib.sha256(E.tostring(original,method='c14n')).hexdigest()!=patch.get('expected_sha256'):raise ValueError('公式基础版本变化')
            replacement=omml(patch['omml']);original.getparent().replace(original,replacement)
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as dest:
            for info in src.infolist():dest.writestr(copy.copy(info),E.tostring(changed[info.filename],xml_declaration=True,encoding='UTF-8',standalone=True) if info.filename in changed else src.read(info.filename))
        result=stream.getvalue()
        with zipfile.ZipFile(io.BytesIO(result)) as check:
            preserved=[info.filename for info in src.infolist() if info.filename not in changed and src.read(info.filename)==check.read(info.filename)]
            if len(preserved)!=len(src.infolist())-len(changed):raise ValueError('未修改部件的保真校验失败')
        return result,{'parts_preserved':len(preserved),'changed_parts':list(changed),'edited_runs':len(seen),'edited_formulas':len(formula_patches),'unmodified_parts_byte_identical':True,'original_sha256':expected_sha,'output_sha256':hashlib.sha256(result).hexdigest(),'word_visual_acceptance':'pending'}

def omml(expression):
    root=parse(expression.encode())
    if root.tag not in ('{'+M+'}oMath','{'+M+'}oMathPara'):raise ValueError('需要OMML公式根节点')
    word={'rPr','rFonts','b','i','bCs','iCs','caps','smallCaps','strike','dstrike','outline','shadow','emboss','imprint','noProof','snapToGrid','vanish','webHidden','color','spacing','w','kern','position','sz','szCs','highlight','u','effect','bdr','shd','fitText','vertAlign','rtl','cs','em','lang','eastAsianLayout','specVanish'}
    if len(expression)>100000:raise ValueError('公式XML过长')
    for n in root.iter():
        if not isinstance(n.tag,str) or not (n.tag.startswith('{'+M+'}') or n.tag.startswith('{'+W+'}') and E.QName(n).localname in word):raise ValueError('公式包含未允许的XML元素')
        if any(k.startswith('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}') for k in n.attrib):raise ValueError('公式不能包含外部资源关系')
    if any('}id' in attr or 'href' in attr.lower() for node in root.iter() for attr in node.attrib):raise ValueError('公式不能包含外部关系')
    return root

def author_year_labels(refs):
    groups={};labels={}
    for rid,bib in refs.items():
        from . import db
        meta=db.loads(bib['metadata'],{});authors=meta.get('authors') or '佚名';authors='、'.join(str(x) for x in authors) if isinstance(authors,list) else str(authors);first=re.split(r'[、;,；]',authors)[0].strip();base=(first,str(meta.get('year') or '无日期'));groups.setdefault(base,[]).append((str(meta.get('title','')),rid))
    for (author,year),items in groups.items():
        for index,(_,rid) in enumerate(sorted(items)):labels[rid]=author+'，'+year+(chr(97+index) if len(items)>1 else '')
    return labels

def add_footnotes(path,notes):
    if not notes:return
    with zipfile.ZipFile(path) as src:
        parts={info.filename:(info,src.read(info.filename)) for info in src.infolist()}
    CT='http://schemas.openxmlformats.org/package/2006/content-types';REL='http://schemas.openxmlformats.org/package/2006/relationships'
    existing=parse(parts['word/footnotes.xml'][1]) if 'word/footnotes.xml' in parts else E.Element('{'+W+'}footnotes',nsmap={'w':W});used={int(n.get('{'+W+'}id')) for n in existing};mapping={}
    if not list(existing):
        for identifier,kind in [(-1,'separator'),(0,'continuationSeparator')]:
            foot=E.SubElement(existing,'{'+W+'}footnote',{'{'+W+'}id':str(identifier),'{'+W+'}type':kind});p=E.SubElement(foot,'{'+W+'}p');r=E.SubElement(p,'{'+W+'}r');E.SubElement(r,'{'+W+'}'+kind)
    for requested,text in notes.items():
        if requested in used:raise ValueError('脚注ID与模板冲突，请使用保真编辑路径或移除冲突')
        foot=E.SubElement(existing,'{'+W+'}footnote',{'{'+W+'}id':str(requested)});p=E.SubElement(foot,'{'+W+'}p');pp=E.SubElement(p,'{'+W+'}pPr');E.SubElement(pp,'{'+W+'}pStyle',{'{'+W+'}val':'FootnoteText'});marker=E.SubElement(p,'{'+W+'}r');rp=E.SubElement(marker,'{'+W+'}rPr');E.SubElement(rp,'{'+W+'}rStyle',{'{'+W+'}val':'FootnoteReference'});E.SubElement(marker,'{'+W+'}footnoteRef');run=E.SubElement(p,'{'+W+'}r');t=E.SubElement(run,'{'+W+'}t');t.set(XML,'preserve');t.text=text
    styles=parse(parts['word/styles.xml'][1]);style_ids={n.get('{'+W+'}styleId') for n in styles}
    for identifier,kind,name in [('FootnoteText','paragraph','footnote text'),('FootnoteReference','character','footnote reference')]:
        if identifier in style_ids:continue
        style=E.SubElement(styles,'{'+W+'}style',{'{'+W+'}type':kind,'{'+W+'}styleId':identifier});E.SubElement(style,'{'+W+'}name',{'{'+W+'}val':name});E.SubElement(style,'{'+W+'}basedOn',{'{'+W+'}val':'Normal' if kind=='paragraph' else 'DefaultParagraphFont'});rp=E.SubElement(style,'{'+W+'}rPr')
        if kind=='paragraph':E.SubElement(rp,'{'+W+'}sz',{'{'+W+'}val':'20'})
        else:E.SubElement(rp,'{'+W+'}vertAlign',{'{'+W+'}val':'superscript'})
    parts['word/styles.xml']=(parts['word/styles.xml'][0],E.tostring(styles,xml_declaration=True,encoding='UTF-8'))
    parts['word/footnotes.xml']=(None,E.tostring(existing,xml_declaration=True,encoding='UTF-8',standalone=True));ct=parse(parts['[Content_Types].xml'][1])
    if not any(n.get('PartName')=='/word/footnotes.xml' for n in ct):E.SubElement(ct,'{'+CT+'}Override',PartName='/word/footnotes.xml',ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml')
    rels=parse(parts['word/_rels/document.xml.rels'][1])
    if not any(n.get('Type','').endswith('/footnotes') for n in rels):
        ids={n.get('Id') for n in rels};rid='rIdPaperFootnotes'
        while rid in ids:rid+='X'
        E.SubElement(rels,'{'+REL+'}Relationship',Id=rid,Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes',Target='footnotes.xml')
    for name,root in [('[Content_Types].xml',ct),('word/_rels/document.xml.rels',rels)]:parts[name]=(parts[name][0],E.tostring(root,xml_declaration=True,encoding='UTF-8'))
    temporary=path.with_suffix('.footnotes.tmp')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as out:
        for name,(info,data) in parts.items():out.writestr(copy.copy(info) if info else name,data)
    temporary.replace(path)
