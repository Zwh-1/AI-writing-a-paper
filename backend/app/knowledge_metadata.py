"""Authorship/keywords are attributed to bibliography metadata, not paper facts."""
import re
from . import db,knowledge_graph as graph,knowledge_lineage as lineage
from . import research_records as store

def enrich(pid,rid):
    book=store.require_reference(pid,rid);metadata=book['metadata']
    if metadata.get('_studio_deleted'):return {'skipped':True}
    source=lineage.ref(pid,'reference',rid)
    if book['file_id']:lineage.add_dependencies(pid,'reference',rid,source['version'],[lineage.ref(pid,'file',book['file_id'])])
    key='file:'+book['file_id'] if book['file_id'] else 'bibliography:'+rid
    paper=db.one("SELECT id FROM kg_entities WHERE project_id=? AND namespace='literature' AND entity_key=? AND redirect_id IS NULL",(pid,key))
    if paper:
        paper=graph.get_entity(pid,paper['id'])
        if paper['version']['method']=='bibliography_metadata':paper=graph.entity(pid,{'namespace':'literature','kind':'document','key':key,'name':metadata.get('title') or '未命名文献','sources':[source],'method':'bibliography_metadata'})
    else:paper=graph.entity(pid,{'namespace':'literature','kind':'document','key':key,'name':metadata.get('title') or '未命名文献','sources':[source],'method':'bibliography_metadata'})
    authors=metadata.get('authors',[])
    if isinstance(authors,str):authors=[name.strip() for name in re.split('[;；，,]',authors) if name.strip()]
    targets=[]
    for index,author in enumerate(authors[:100]):
        name=author.get('name','') if isinstance(author,dict) else str(author);orcid=author.get('orcid') if isinstance(author,dict) else None
        if not name.strip():continue
        identity='author:orcid:'+orcid if orcid else 'author:bibliography:'+rid+':'+str(index)
        node=graph.entity(pid,{'namespace':'literature','kind':'author','key':identity,'name':name,'description':'书目作者；没有 ORCID 时按文献保留独立身份，姓名相同不自动视为同一人。','sources':[source],'method':'bibliography_metadata'})
        targets.append((node,'authored_by','书目记录将“'+name+'”列为该文献作者'))
    for keyword in list(dict.fromkeys(metadata.get('tags',[])))[:80]:
        if not isinstance(keyword,str) or not keyword.strip():continue
        node=graph.entity(pid,{'namespace':'literature','kind':'keyword','key':'keyword:'+rid+':'+graph.normalized(keyword),'name':keyword,'description':'书目分类标签，供主题导航。相同标签通过专题关系聚合，保留各文献来源。','sources':[source],'method':'bibliography_metadata'})
        targets.append((node,'has_keyword','书目记录为该文献设置关键词“'+keyword+'”'))
    claims=[]
    for target,predicate,statement in targets:
        item=graph.assertion(pid,{'namespace':'literature','subject_id':paper['id'],'object_id':target['id'],'predicate':predicate,'statement':statement,'sources':[source],
                                 'attribution':'书目元数据','method':'bibliography_metadata','modality':'reported','conditions':['这是书目信息与分类关系，不是经原文核验的研究论断。']})
        claims.append(item['claim_version_id'])
    related=[]
    tags={graph.normalized(tag) for tag in metadata.get('tags',[]) if isinstance(tag,str)}
    doi=str(metadata.get('doi','')).casefold()
    for row in db.rows("SELECT * FROM bibliography WHERE project_id=? AND id!=? AND COALESCE(json_extract(metadata,'$._studio_deleted'),0)=0",(pid,rid)):
        other=db.loads(row['metadata'],{});shared=tags&{graph.normalized(tag) for tag in other.get('tags',[]) if isinstance(tag,str)}
        if not shared:continue
        other_key='file:'+row['file_id'] if row['file_id'] else 'bibliography:'+row['id']
        other_paper=db.one("SELECT * FROM kg_entities WHERE project_id=? AND namespace='literature' AND entity_key=? AND redirect_id IS NULL",(pid,other_key))
        if not other_paper:continue
        try:
            graph.get_entity(pid,other_paper['id']);other_source=lineage.ref(pid,'reference',row['id'])
            relation=graph.assertion(pid,{'namespace':'literature','subject_id':paper['id'],'object_id':other_paper['id'],'predicate':'shares_keywords','statement':'书目分类具有共同关键词：'+', '.join(sorted(shared)),
                'sources':[source,other_source],'attribution':'书目元数据','method':'bibliography_metadata','conditions':['仅表示分类关联，不证明方法或结论相同。']})
            related.append(relation['claim_version_id'])
        except __import__('fastapi',fromlist=['HTTPException']).HTTPException:continue
    return {'reference_id':rid,'paper_id':paper['id'],'relations':claims,'related_papers':related,'metadata_only':True}

def enrich_project(pid):
    security=__import__('app.security',fromlist=['require_project']);security.require_project(pid)
    return {'items':[enrich(pid,row['id']) for row in db.rows("SELECT id FROM bibliography WHERE project_id=? AND COALESCE(json_extract(metadata,'$._studio_deleted'),0)=0",(pid,))]}
