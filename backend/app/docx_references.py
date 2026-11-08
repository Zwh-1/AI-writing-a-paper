"""Internal bibliography links with cached, readable citation text."""
import hashlib,re
from collections import Counter
from zipfile import ZipFile
from lxml import etree
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.text.run import Run

PREFIX='WBRef'


def reference_anchor(reference_id):
    # Word bookmark names must be short identifiers, not arbitrary database IDs.
    return PREFIX+hashlib.sha256(str(reference_id).encode()).hexdigest()[:24]


def paragraph_runs(paragraph):
    """Include runs nested in hyperlinks when applying typography."""
    return [Run(node,paragraph) for node in paragraph._p.iter(qn('w:r'))]


def citation_link(paragraph,reference_id,label,superscript=False):
    run=paragraph.add_run(label)
    run.font.superscript=superscript
    run.font.color.rgb=RGBColor(0,0,0)
    run.font.underline=False
    link=OxmlElement('w:hyperlink')
    link.set(qn('w:anchor'),reference_anchor(reference_id))
    link.set(qn('w:history'),'1')
    link.set(qn('w:tooltip'),'跳转到对应参考文献')
    paragraph._p.replace(run._r,link);link.append(run._r)
    return run


def bookmark_reference(paragraph,reference_id):
    document=paragraph._p.getroottree().getroot()
    existing=list(document.iter(qn('w:bookmarkStart')))
    name=reference_anchor(reference_id)
    if any(node.get(qn('w:name'))==name for node in existing):
        raise ValueError('参考文献书签重复')
    used={int(node.get(qn('w:id'))) for node in existing if str(node.get(qn('w:id'))).isdigit()}
    identity=0
    while identity in used:identity+=1
    start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(identity));start.set(qn('w:name'),name)
    paragraph._p.insert(1 if paragraph._p.pPr is not None else 0,start)
    end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),str(identity));paragraph._p.append(end)


def navigation_report(path,expected_citations=None,expected_references=None):
    with ZipFile(path) as archive:root=etree.fromstring(archive.read('word/document.xml'))
    starts=[node.get(qn('w:name')) for node in root.iter(qn('w:bookmarkStart'))]
    links=[node.get(qn('w:anchor')) for node in root.iter(qn('w:hyperlink'))
           if (node.get(qn('w:anchor')) or '').startswith(PREFIX)]
    element_links=len(links)
    # WPS/Word may materialize an anchor hyperlink as a HYPERLINK \l field
    # when saving updated fields. Recognize the actual cached field too.
    instructions=[];stack=[]
    for node in root.iter():
        if node.tag==qn('w:fldSimple'):instructions.append(node.get(qn('w:instr'),''))
        elif node.tag==qn('w:fldChar'):
            kind=node.get(qn('w:fldCharType'))
            if kind=='begin':stack.append({'code':[],'result':False})
            elif kind=='separate' and stack:stack[-1]['result']=True
            elif kind=='end' and stack:instructions.append(''.join(stack.pop()['code']))
        elif node.tag==qn('w:instrText') and stack and not stack[-1]['result']:
            stack[-1]['code'].append(node.text or '')
    for code in instructions:
        if not re.match(r'\s*HYPERLINK\b',code,re.I):continue
        match=re.search(r'\\l\s+"?([A-Za-z_][A-Za-z0-9_]*)',code)
        if match and match[1].startswith(PREFIX):links.append(match[1])
    duplicate=[name for name,count in Counter(starts).items() if count>1]
    dangling=sorted(set(links)-set(starts))
    if duplicate or dangling:raise ValueError('参考文献跳转存在重复或失效书签')
    references=sum(name.startswith(PREFIX) for name in starts)
    if expected_citations is not None and len(links)!=expected_citations:raise ValueError('导出后的正文引用链接数量与稿件不一致')
    if expected_references is not None and references!=expected_references:raise ValueError('导出后的参考文献书签数量与稿件不一致')
    return {'citation_links':len(links),'element_links':element_links,'field_links':len(links)-element_links,
            'bibliography_bookmarks':references,
            'dangling_targets':dangling,'duplicate_bookmarks':duplicate,
            'mechanism':'OOXML internal hyperlinks and bibliography bookmarks; visible citation text retained'}
