from .. import db
from .common import owned,fail

@db.atomic
def create(pid,body):
    key=body.get('claim_key') or db.uid();proposition=body.get('proposition',{});kind=body.get('kind','implementation')
    if not isinstance(proposition,dict) or not isinstance(proposition.get('statement',''),str) or not proposition.get('statement','').strip():fail('INVALID_CLAIM','论断需要明确文字陈述')
    if len(db.dumps(proposition))>20000:fail('INVALID_CLAIM','论断过长')
    links=body.get('evidence',[])
    if not isinstance(links,list) or len(links)>30:fail('INVALID_CLAIM','论断依据无效')
    for link in links:
        owned('evidence',link['evidence_id'],pid)
        if link.get('relation','supports') not in ('supports','limits','contradicts'):fail('INVALID_RELATION','论断关系无效')
    version=db.one('SELECT COALESCE(MAX(revision),0)+1 n FROM claim_versions WHERE project_id=? AND claim_key=?',(pid,key))['n'];cid=db.uid()
    db.execute('INSERT INTO claim_versions VALUES(?,?,?,?,?,?,?,?)',(cid,pid,key,version,kind,db.dumps(proposition),'candidate',db.now()))
    for link in links:db.execute('INSERT INTO claim_evidence_links VALUES(?,?,?,?)',(cid,link['evidence_id'],link.get('relation','supports'),db.dumps(link.get('scope',{}))))
    return get(pid,cid)

def get(pid,cid):
    c=owned('claim',cid,pid)
    return {**c,'proposition':db.loads(c['proposition_json'],{}),'evidence':db.rows('SELECT * FROM claim_evidence_links WHERE claim_version_id=?',(cid,))}
