"""Insert generated body at an explicit template anchor while retaining original stories/sections."""
import copy,hashlib,io,posixpath,zipfile
from lxml import etree as E
from .ooxml_preserve import W,NS,parse
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships';REL='http://schemas.openxmlformats.org/package/2006/relationships';CT='http://schemas.openxmlformats.org/package/2006/content-types'

def graft(template,generated,marker='{{PAPER_BODY}}'):
    with zipfile.ZipFile(io.BytesIO(template)) as original,zipfile.ZipFile(io.BytesIO(generated)) as incoming:
        parts={i.filename:(i,original.read(i.filename)) for i in original.infolist()};source={n:incoming.read(n) for n in incoming.namelist()};root=parse(parts['word/document.xml'][1]);body=root.find('w:body',NS);newroot=parse(source['word/document.xml']);newbody=newroot.find('w:body',NS)
        anchors=[p for p in body.findall('w:p',NS) if ''.join(p.itertext()).strip()==marker]
        if len(anchors)!=1:raise ValueError('复杂模板须在正文位置提供唯一的{{PAPER_BODY}}占位段落；不会删除模板中的其他内容')
        blocks=[copy.deepcopy(n) for n in newbody if n.tag!='{'+W+'}sectPr'];namespace='paper-import/'+hashlib.sha256(generated).hexdigest()[:12]+'/'
        styles=parse(parts['word/styles.xml'][1]);srcstyles=parse(source['word/styles.xml']);existing={n.get('{'+W+'}styleId'):n for n in styles.findall('w:style',NS)};byname={n.find('w:name',NS).get('{'+W+'}val','').lower():n.get('{'+W+'}styleId') for n in styles if n.find('w:name',NS) is not None};stylemap={};created=[]
        for s in srcstyles.findall('w:style',NS):
            sid=s.get('{'+W+'}styleId');name=s.find('w:name',NS);label=name.get('{'+W+'}val','').lower() if name is not None else ''
            target=sid if sid in existing else byname.get(label)
            if not target:target='pwb_'+sid;created.append(sid)
            stylemap[sid]=target
        num_map={};abstract_map={};appended_numbers=[]
        if 'word/numbering.xml' in source:
            numbers=parse(parts['word/numbering.xml'][1]) if 'word/numbering.xml' in parts else E.Element('{'+W+'}numbering',nsmap={'w':W});srcnum=parse(source['word/numbering.xml']);next_num=max([int(n.get('{'+W+'}numId')) for n in numbers.findall('w:num',NS)]+[0])+1;next_abs=max([int(n.get('{'+W+'}abstractNumId')) for n in numbers.findall('w:abstractNum',NS)]+[-1])+1
            for item in srcnum.findall('w:abstractNum',NS):abstract_map[item.get('{'+W+'}abstractNumId')]=str(next_abs);next_abs+=1
            for item in srcnum.findall('w:num',NS):num_map[item.get('{'+W+'}numId')]=str(next_num);next_num+=1
            for item in srcnum:
                c=copy.deepcopy(item)
                if c.tag=='{'+W+'}abstractNum':c.set('{'+W+'}abstractNumId',abstract_map[c.get('{'+W+'}abstractNumId')])
                if c.tag=='{'+W+'}num':c.set('{'+W+'}numId',num_map[c.get('{'+W+'}numId')])
                for n in c.findall('.//w:abstractNumId',NS):n.set('{'+W+'}val',abstract_map[n.get('{'+W+'}val')])
                numbers.append(c);appended_numbers.append(c)
        def remap(element):
            for n in element.iter():
                if n.tag in ('{'+W+'}pStyle','{'+W+'}rStyle','{'+W+'}tblStyle','{'+W+'}basedOn','{'+W+'}next','{'+W+'}link','{'+W+'}styleLink','{'+W+'}numStyleLink'):
                    value=n.get('{'+W+'}val');n.set('{'+W+'}val',stylemap.get(value,value))
                if n.tag=='{'+W+'}numId' and n.get('{'+W+'}val') in num_map:n.set('{'+W+'}val',num_map[n.get('{'+W+'}val')])
        for s in srcstyles.findall('w:style',NS):
            sid=s.get('{'+W+'}styleId')
            if sid in created:
                c=copy.deepcopy(s);c.set('{'+W+'}styleId',stylemap[sid]);remap(c);styles.append(c)
        rels=parse(parts['word/_rels/document.xml.rels'][1]);srcrels=parse(source['word/_rels/document.xml.rels']);ids={n.get('Id') for n in rels};ridmap={};copied=[];skipped={'styles','numbering','settings','webSettings','fontTable','theme','footnotes','endnotes'}
        for rel in srcrels:
            kind=rel.get('Type','').rsplit('/',1)[-1]
            if kind in skipped:continue
            rid='rIdPwb'+str(len(ids)+1)
            while rid in ids:rid+='x'
            ids.add(rid);ridmap[rel.get('Id')]=rid;c=copy.deepcopy(rel);c.set('Id',rid)
            if rel.get('TargetMode')!='External':
                target=posixpath.normpath('word/'+rel.get('Target',''));c.set('Target','/'+namespace+target)
            rels.append(c)
        skip_parts={'word/document.xml','word/_rels/document.xml.rels','word/styles.xml','word/numbering.xml','word/settings.xml','word/webSettings.xml','word/fontTable.xml','word/footnotes.xml','word/endnotes.xml','[Content_Types].xml','_rels/.rels'}
        for name,data in source.items():
            if name in skip_parts or name.startswith('docProps/'):continue
            if name.endswith('.rels'):
                rr=parse(data)
                for relationship in rr:
                    if relationship.get('TargetMode')!='External':
                        base=posixpath.dirname(posixpath.dirname(name));target=relationship.get('Target','');resolved=target.lstrip('/') if target.startswith('/') else posixpath.normpath(base+'/'+target);relationship.set('Target','/'+namespace+resolved)
                data=E.tostring(rr,xml_declaration=True,encoding='UTF-8')
            parts[namespace+name]=(None,data);copied.append(name)
        footmap={}
        if 'word/footnotes.xml' in source:
            notes=parse(parts['word/footnotes.xml'][1]) if 'word/footnotes.xml' in parts else E.Element('{'+W+'}footnotes',nsmap={'w':W});used={int(n.get('{'+W+'}id')) for n in notes};next_id=max(used|{0})+1
            for item in parse(source['word/footnotes.xml']):
                identifier=int(item.get('{'+W+'}id'));c=copy.deepcopy(item)
                if identifier<=0:
                    if identifier not in used:notes.append(c);used.add(identifier)
                    continue
                footmap[str(identifier)]=str(next_id);c.set('{'+W+'}id',str(next_id));next_id+=1;remap(c);notes.append(c)
            parts['word/footnotes.xml']=(parts.get('word/footnotes.xml',(None,None))[0],E.tostring(notes,xml_declaration=True,encoding='UTF-8'))
            if not any(n.get('Type','').endswith('/footnotes') for n in rels):E.SubElement(rels,'{'+REL+'}Relationship',Id='rIdPwbNotes',Type=R+'/footnotes',Target='footnotes.xml')
        for block in blocks:
            remap(block)
            for n in block.iter():
                for key,value in list(n.attrib.items()):
                    if key.startswith('{'+R+'}') and value in ridmap:n.set(key,ridmap[value])
                if n.tag=='{'+W+'}footnoteReference':n.set('{'+W+'}id',footmap[n.get('{'+W+'}id')])
        anchor=anchors[0];index=list(body).index(anchor);body_section=sum(len(n.findall('.//w:sectPr',NS)) for n in list(body)[:index]);boundary=anchor.find('w:pPr/w:sectPr',NS)
        if boundary is not None:
            retained=E.Element('{'+W+'}p');properties=E.SubElement(retained,'{'+W+'}pPr');properties.append(copy.deepcopy(boundary));blocks.append(retained)
        before_sections=[E.tostring(n,method='c14n') for n in root.findall('.//w:sectPr',NS)];body.remove(anchor)
        for offset,block in enumerate(blocks):body.insert(index+offset,block)
        changed={'word/document.xml':root,'word/_rels/document.xml.rels':rels}
        if created:changed['word/styles.xml']=styles
        if 'word/numbering.xml' in source:
            for n in appended_numbers:remap(n)
            changed['word/numbering.xml']=numbers
            if not any(n.get('Type','').endswith('/numbering') for n in rels):E.SubElement(rels,'{'+REL+'}Relationship',Id='rIdPwbNumbering',Type=R+'/numbering',Target='numbering.xml')
        ct=parse(parts['[Content_Types].xml'][1]);srcct=parse(source['[Content_Types].xml']);overrides={n.get('PartName') for n in ct};defaults={n.get('Extension') for n in ct if n.tag=='{'+CT+'}Default'}
        for item in srcct:
            c=copy.deepcopy(item)
            if item.tag=='{'+CT+'}Override':
                part=item.get('PartName').lstrip('/')
                target='/'+namespace+part if part in copied else '/'+part if part in ('word/footnotes.xml','word/numbering.xml') and part in parts or part in changed else None
                if target and target not in overrides:c.set('PartName',target);ct.append(c);overrides.add(target)
            elif item.get('Extension') not in defaults:ct.append(c);defaults.add(item.get('Extension'))
        changed['[Content_Types].xml']=ct
        for name,element in changed.items():parts[name]=(parts.get(name,(None,None))[0],E.tostring(element,xml_declaration=True,encoding='UTF-8',standalone=True))
        if before_sections!=[E.tostring(n,method='c14n') for n in root.findall('.//w:sectPr',NS)]:raise ValueError('模板分节保留校验失败，未输出可能损坏的文档')
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as dest:
            for name,(info,data) in parts.items():dest.writestr(copy.copy(info) if info else name,data)
        output=stream.getvalue();modified=[name for name in original.namelist() if original.read(name)!=parts[name][1]];untouched=len(original.namelist())-len(modified)
        return output,{'mode':'preserve_template_at_anchor','marker':marker,'original_parts_retained':len(original.namelist()),'original_parts_byte_identical':untouched,'modified_original_parts':modified,'cover_sections_retained':True,'body_section_index':body_section,'template_styles_preferred':True,'visual_acceptance':'pending'}
