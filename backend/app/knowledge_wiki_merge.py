"""Three-way Wiki block merge, preserving both submitted edits and histories."""
from . import db,knowledge_wiki as wiki,knowledge_lineage as lineage
from fastapi import HTTPException

def block(value):
    return {'key':str(value.get('key',value.get('block_key'))),'kind':value.get('kind','note'),'text':value.get('text'),
            'sources':value.get('sources',[]),'metadata':value.get('metadata',{})}

def version_blocks(pid,page,vid):
    return [block(b) for b in wiki.get_page(pid,page,vid)['blocks']] if vid else []

def save_conflict(pid,page,body,author):
    current=lineage.owned(pid,'wiki_page',page);base=body.get('base_version_id')
    if base and lineage.owned(pid,'wiki_version',base)['page_id']!=page:lineage.fail('INVALID_WIKI_VERSION','基础版本不属于页面',422)
    if not isinstance(body.get('blocks'),list) or not 1<=len(body['blocks'])<=250:lineage.fail('INVALID_WIKI_BLOCKS','候选内容块无效',422)
    identity=db.uid();db.execute('INSERT INTO wiki_edit_conflicts(id,project_id,page_id,base_version_id,current_version_id,proposed_json,author,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (identity,pid,page,base,current['current_version_id'],db.dumps(body['blocks']),author,db.now()))
    db.audit(pid,'wiki_edit_conflict_preserved',{'conflict_id':identity,'page_id':page,'base_version_id':base,'current_version_id':current['current_version_id']})
    return identity

def submit(pid,page,body,author='human'):
    try:return wiki.revise(pid,page,body,author)
    except HTTPException as error:
        if not isinstance(error.detail,dict) or error.detail.get('code')!='WIKI_EDIT_CONFLICT':raise
        identity=save_conflict(pid,page,body,author)
        return {'conflict_id':identity,'state':'conflict','message':'两份编辑均已保留，请逐块合并','page_id':page}

def preview(pid,identity):
    conflict=db.one('SELECT * FROM wiki_edit_conflicts WHERE id=? AND project_id=?',(identity,pid))
    if not conflict:lineage.fail('WIKI_CONFLICT_NOT_FOUND','页面冲突不存在',404)
    page=lineage.owned(pid,'wiki_page',conflict['page_id']);head=page['current_version_id']
    base=version_blocks(pid,page['id'],conflict['base_version_id']);local=version_blocks(pid,page['id'],head)
    incoming=[block(b) for b in db.loads(conflict['proposed_json'],[])]
    maps=[{b['key']:b for b in values} for values in (base,local,incoming)];b,l,r=maps
    keys=list(dict.fromkeys([x['key'] for x in base+local+incoming]));items=[]
    for key in keys:
        before,left,right=b.get(key),l.get(key),r.get(key)
        if left==right:value,conflicted=left,False
        elif before==left:value,conflicted=right,False
        elif before==right:value,conflicted=left,False
        else:value,conflicted=None,True
        # A model revision cannot silently delete or replace human edits.
        if conflict['author']!='human':
            human=next((x for x in wiki.get_page(pid,page['id'])['blocks'] if x['block_key']==key and x['author']=='human'),None)
            if human and right!=left:value,conflicted=left,False
        items.append({'key':key,'base':before,'local':left,'incoming':right,'automatic':value,'conflict':conflicted})
    def order(values,common):return [x['key'] for x in values if x['key'] in common]
    common=set(b)&set(l)&set(r)
    order_conflict=order(base,common)!=order(local,common) and order(base,common)!=order(incoming,common) and order(local,common)!=order(incoming,common)
    fingerprint=lineage.digest([identity,head,base,local,incoming])
    return {'id':identity,'page_id':page['id'],'state':conflict['state'],'current_version_id':head,'fingerprint':fingerprint,'blocks':items,
            'order_conflict':order_conflict,'local_order':[x['key'] for x in local],'incoming_order':[x['key'] for x in incoming],
            'conflict_count':sum(x['conflict'] for x in items)+int(order_conflict)}

@db.atomic
def resolve(pid,identity,body):
    proposal=preview(pid,identity)
    if proposal['state']!='open':lineage.fail('WIKI_CONFLICT_RESOLVED','该冲突已处理')
    if body.get('expected_fingerprint')!=proposal['fingerprint']:lineage.fail('WIKI_EDIT_CONFLICT','页面已再次变化，请重新比较')
    if not str(body.get('reviewer','')).strip() or not str(body.get('note','')).strip():lineage.fail('MERGE_REVIEW_REQUIRED','合并需要核对人和说明',422)
    choices=body.get('choices',{});merged=[]
    for item in proposal['blocks']:
        value=item['automatic']
        if item['conflict']:
            choice=choices.get(item['key'],{});mode=choice.get('choice') if isinstance(choice,dict) else None
            if mode in ('local','incoming'):value=item[mode]
            elif mode=='manual' and isinstance(choice.get('block'),dict):
                value=block({**choice['block'],'key':item['key']})
            else:lineage.fail('WIKI_BLOCK_CONFLICT','请逐块选择保留本地、采用候选或手工合并',422)
        if value:merged.append(value)
    ordering=body.get('order')
    if proposal['order_conflict'] and not isinstance(ordering,list):lineage.fail('WIKI_ORDER_CONFLICT','双方调整了顺序，请明确合并后的块顺序',422)
    if ordering is not None:
        if not isinstance(ordering,list) or len(ordering)!=len(merged) or set(ordering)!={b['key'] for b in merged}:lineage.fail('INVALID_WIKI_ORDER','合并顺序必须恰好包含每个保留块一次',422)
        mapping={b['key']:b for b in merged};merged=[mapping[k] for k in ordering]
    result=wiki.revise(pid,proposal['page_id'],{'base_version_id':proposal['current_version_id'],'blocks':merged},'human')
    vid=result['version']['id'];db.execute("UPDATE wiki_edit_conflicts SET state='resolved',resolution_version_id=?,resolved_at=? WHERE id=?",(vid,db.now(),identity))
    db.audit(pid,'wiki_conflict_resolved',{'conflict_id':identity,'version_id':vid,'reviewer':body['reviewer'],'note':body['note']})
    return {**result,'conflict_resolved':True,'published':False}
