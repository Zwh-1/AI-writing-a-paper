"""Immutable sentence analyses. Offsets are Unicode code points, never UTF-16."""
import re
from .. import db,documents
from .common import blob,digest,owned,candidate,unblob,fail
VERSION='sentences.cp.v1'

def paragraph_text(node):
    if node.get('type')=='text':return node.get('text','')
    if node.get('type')=='hardBreak':return '\n'
    if node.get('type') in ('citation','formula','crossReference','footnote'):return '\ufffc'
    return ''.join(paragraph_text(x) for x in node.get('content',[]))

def paragraphs(doc):return [n for n in documents.nodes(doc) if n.get('type') in ('paragraph','heading')]

def paragraph_for_context(node,ordinal):
    result={'node_id':node['attrs']['nodeId'],'ordinal':ordinal,'text':paragraph_text(node)}
    references=[];positions=[];offset=0
    for child in node.get('content',[]):
        if child['type']=='citation':references.append(child['attrs']['citationId']);positions.append(offset)
        offset+=len(paragraph_text(child))
    if references:result.update(reference_ids=references,citation_positions_cp=positions)
    for source,target in (('sourceIds','source_ids'),('factIds','fact_ids')):
        if node['attrs'].get(source):result[target]=node['attrs'][source]
    return result

def ranges(text):
    start=0;i=0
    while i<len(text):
        c=text[i];terminal=c in '。！？!?；;\n'
        if c=='.' and (i+1==len(text) or text[i+1].isspace()):
            token=re.search(r'[A-Za-z.]+$',text[:i+1]);terminal=not(token and token.group().lower() in ('e.g.','i.e.','dr.','mr.','etc.'))
        if terminal:
            end=i+1
            while end<len(text) and text[end] in '”’\"\'」』':end+=1
            a=start;b=end
            while a<b and text[a].isspace():a+=1
            while b>a and text[b-1].isspace():b-=1
            if b>a:yield a,b
            start=end;i=end
        else:i+=1
    a=start;b=len(text)
    while a<b and text[a].isspace():a+=1
    while b>a and text[b-1].isspace():b-=1
    if b>a:yield a,b

def utf16_offset(text,codepoint):
    if not 0<=codepoint<=len(text):raise ValueError('码点位置越界')
    return len(text[:codepoint].encode('utf-16-le'))//2

@db.atomic
def analyze(pid,document,candidate_id=None,revision_id=None):
    if bool(candidate_id)==bool(revision_id):fail('INVALID_ANALYSIS','分析必须对应唯一候选或正文版本')
    if candidate_id:candidate(candidate_id,pid)
    if revision_id:
        if not db.one('SELECT r.id FROM revisions r JOIN sections s ON s.id=r.section_id WHERE r.id=? AND s.project_id=?',(revision_id,pid)):fail('NOT_FOUND','正文版本不存在',404)
    identity=db.uid();db.execute('INSERT INTO document_analyses VALUES(?,?,?,?,?,?,?)',(identity,pid,candidate_id,revision_id,digest(document),VERSION,'unverified'))
    for node in paragraphs(document):
        text=paragraph_text(node);nid=node.get('attrs',{}).get('nodeId')
        if not nid:fail('INVALID_NODE','正文节点缺少稳定标识')
        for ordinal,(a,b) in enumerate(ranges(text)):
            db.execute('INSERT INTO sentence_spans VALUES(?,?,?,?,?,?,?,?)',(db.uid(),identity,nid,ordinal,a,b,digest(text[a:b]),'unknown'))
    return get(pid,identity,document)

def analysis_document(pid,analysis):
    if analysis['candidate_id']:return unblob(candidate(analysis['candidate_id'],pid)['document_blob'])
    r=db.one('SELECT r.document FROM revisions r JOIN sections s ON s.id=r.section_id WHERE r.id=? AND s.project_id=?',(analysis['revision_id'],pid))
    if not r:fail('NOT_FOUND','分析原文不存在',404)
    return db.loads(r['document'],{})

def get(pid,identity,document=None):
    a=owned('analysis',identity,pid);doc=document or analysis_document(pid,a);texts={n['attrs']['nodeId']:paragraph_text(n) for n in paragraphs(doc)}
    if digest(doc)!=a['text_hash']:fail('ANALYSIS_TEXT_CHANGED','分析与正文版本不匹配',409)
    rows=db.rows('SELECT * FROM sentence_spans WHERE analysis_id=?',(identity,));node_order={n['attrs']['nodeId']:i for i,n in enumerate(paragraphs(doc))};rows.sort(key=lambda r:(node_order.get(r['node_id'],len(node_order)),r['ordinal']))
    for r in rows:
        text=texts.get(r['node_id'],'');r['text']=text[r['start_cp']:r['end_cp']]
        if digest(r['text'])!=r['text_hash']:fail('ANALYSIS_TEXT_CHANGED','句子映射已失效',409)
        r['start_utf16']=utf16_offset(text,r['start_cp']);r['end_utf16']=utf16_offset(text,r['end_cp']);r['claims']=db.rows('SELECT c.id,c.kind,c.proposition_json,l.role FROM sentence_claim_links l JOIN claim_versions c ON c.id=l.claim_version_id WHERE l.sentence_id=?',(r['id'],))
    return {**a,'sentences':rows,'coverage':{'total':len(rows),'classified':sum(r['role']!='unknown' for r in rows),'with_claims':sum(bool(r['claims']) for r in rows),'unclassified':sum(r['role']=='unknown' for r in rows)}}
