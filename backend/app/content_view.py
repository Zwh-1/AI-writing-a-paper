"""Current document content view; originals and historical identities are never overwritten."""
from . import db
from .source_access import require_file

def file_units(pid,fid,remote=False):
    original=require_file(pid,fid,remote)
    from . import knowledge_units
    head=db.one('SELECT p.* FROM knowledge_parses p JOIN knowledge_heads h ON h.parse_id=p.id WHERE h.file_id=?',(fid,))
    if not head or not knowledge_units.parse_current(head,original):
        knowledge_units.build(pid,fid,allow_material=True)
        head=db.one('SELECT p.* FROM knowledge_parses p JOIN knowledge_heads h ON h.parse_id=p.id WHERE h.file_id=?',(fid,))
    rows=db.rows('SELECT * FROM knowledge_units WHERE parse_id=? ORDER BY ordinal',(head['id'],))
    excluded={row['page_number'] for row in db.rows("SELECT page_number FROM pages WHERE file_id=? AND review_state='not_applicable'",(fid,))}
    units=[]
    for row in rows:
        locator=db.loads(row['locator'],{});pages={locator.get('page',1)}|{item.get('locator',{}).get('page') for item in locator.get('segments',[])}
        if row['review_state']!='excluded' and not pages&excluded:units.append({**row,'locator':locator,'heading_path':db.loads(row['heading_path'],[])})
    return original,head['id'],units


def summary_pages(original, original_pages):
    """Use adopted layout text while preserving physical page identities for previews."""
    head=db.one('SELECT p.* FROM knowledge_parses p JOIN knowledge_heads h ON h.parse_id=p.id WHERE p.file_id=?',(original['id'],))
    if not head or head['strategy']!='layout':return original_pages,None
    from .knowledge_units import parse_current
    if not parse_current(head,original):return original_pages,None
    _,parse_id,units=file_units(original['project_id'],original['id'])
    by_page={}
    for unit in units:by_page.setdefault(unit['locator'].get('page',1),[]).append(unit['text'])
    metadata={row['page_number']:row for row in original_pages}
    return [{**metadata.get(number,{'id':None,'page_number':number,'title':'','notes':''}),
        'text':'\n\n'.join(texts),'content_parse_id':parse_id} for number,texts in sorted(by_page.items())],parse_id
