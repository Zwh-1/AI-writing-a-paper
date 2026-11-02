"""Keep every original occurrence through nested duplicate merges and restores."""
from . import db


def flattened(pid,canonical_id,items=None,merges=None):
    if items is None:
        from .rule_workspace import entries
        items=entries(pid)
    if merges is None:
        from .rule_duplicates import history
        merges=history(pid)
    lookup={r['id']:r for r in items};edges={}
    # One currently rejected member can have only its latest active parent.
    for merge in sorted(merges,key=lambda m:m['id']):
        if merge['restored']:continue
        for row in merge.get('after',[]):
            current=lookup.get(row['id'])
            if current and current['state']=='rejected':
                edges[row['id']]={'parent':merge['canonical_id'],'merge_id':merge['id'],'review_id':merge.get('review_id'),'expected':row}
    children={}
    for member,edge in edges.items():children.setdefault(edge['parent'],[]).append((member,edge))
    result=[];seen={canonical_id}
    def walk(identity,path):
        for member,edge in sorted(children.get(identity,[]),key=lambda pair:pair[0]):
            if member in seen:continue
            seen.add(member);row=lookup[member];next_path=path+[member]
            changed=any(row.get(k)!=edge['expected'].get(k) for k in ('source_id','statement','scope','operations','checker','value','category','modality','decision_note'))
            result.append({k:row.get(k) for k in ('id','source_id','source_file','source_file_id','source_page_id','page_number','statement','state','modality')}
                |{'via_rule_ids':next_path,'merge_id':edge['merge_id'],'depth':len(next_path)-1,'origin_changed':changed,
                    'historical_review_id':edge['review_id'],'original_source_id':edge['expected'].get('source_id'),'original_statement':edge['expected'].get('statement')})
            walk(member,next_path)
    walk(canonical_id,[canonical_id])
    return result
