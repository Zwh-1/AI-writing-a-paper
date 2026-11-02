"""Bounded navigation tools. These never create primary evidence from summaries."""
from . import knowledge_graph as graph, knowledge_wiki as wiki, knowledge_lineage as lineage

NAMES={'search_graph','read_graph_assertion','search_wiki','read_wiki_page','get_knowledge_lineage'}

def execute(pid,name,args):
    if name=='search_graph':
        result=graph.search(pid,args['query'],args.get('namespace','literature'),args.get('depth',1),30,20,True)
        return {'nodes':[{'id':n['id'],'name':n['name'],'kind':n['kind']} for n in result['nodes']],
                'claims':[{'claim_id':a['claim_version_id'],'preview':a['proposition']['statement'][:180],'source_count':len(a['sources']),'support_state':a['support_state']} for a in result['assertions']],
                'scope':'navigation_preview_not_evidence','next_action':'read_graph_assertion，然后按原文位置实际阅读'}
    if name=='read_graph_assertion':return graph.get_assertion(pid,args['claim_id'],True)
    if name=='search_wiki':
        result=wiki.search(pid,args['query'],args.get('namespace','literature'),min(8,max(1,args.get('limit',4))),True)
        return {'pages':[{'id':p['id'],'title':p['title'],'version_id':p['version']['id'],'blocks':len(p['blocks'])} for p in result['items']],'scope':'derived_page_catalogue_not_evidence'}
    if name=='read_wiki_page':
        page=wiki.get_page(pid,args['page_id'],remote=True);offset=args.get('offset',0);limit=args.get('limit',3)
        if not 0<=offset<=10000 or not 1<=limit<=6:lineage.fail('INVALID_TOOL_ARGUMENTS','Wiki分页范围无效',422)
        return {'page_id':page['id'],'title':page['title'],'version_id':page['version']['id'],'blocks':page['blocks'][offset:offset+limit],
                'total_blocks':len(page['blocks']),'next_offset':offset+len(page['blocks'][offset:offset+limit]),'scope':'derived_synthesis_requires_original_reading'}
    return lineage.trace(pid,args['kind'],args['identity'],True,80)
