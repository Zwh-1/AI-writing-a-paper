"""Local frozen knowledge export, including raw lineage and editorial versions."""
import zipfile
from pathlib import Path
from . import db, security, knowledge_wiki as wiki, knowledge_lineage as lineage

def export(pid,task_id=None):
    from .config import DATA
    security.require_project(pid);identity=db.uid();folder=DATA/'exports'/'knowledge';folder.mkdir(parents=True,exist_ok=True);path=folder/(identity+'.zip')
    pages=[]
    # Freeze database reads so concurrent edits cannot mix version heads.
    with db.transaction():
        for row in db.rows('SELECT id FROM wiki_pages WHERE project_id=? ORDER BY namespace,slug',(pid,)):
            pages.append(wiki.get_page(pid,row['id']))
        observations=db.rows('SELECT * FROM knowledge_observations WHERE project_id=?',(pid,))
        manifest={'format':'graph-wiki-export.v1','project_id':pid,'created_at':db.now(),'release_id':db.setting('knowledge_release:'+pid),'pages':pages,
                  'observations':observations,'dependencies':db.rows('SELECT * FROM knowledge_dependencies WHERE project_id=?',(pid,)),
                  'entities':db.rows('SELECT * FROM kg_entities WHERE project_id=?',(pid,)),
                  'entity_versions':db.rows('SELECT * FROM kg_entity_versions WHERE project_id=?',(pid,)),
                  'assertions':db.rows('SELECT a.*,c.proposition_json FROM kg_assertions a JOIN claim_versions c ON c.id=a.claim_version_id WHERE a.project_id=?',(pid,)),
                  'assessments':db.rows('SELECT * FROM kg_support_assessments WHERE project_id=?',(pid,)),
                  'source_role':'原文和派生综合分别保存；导出不自动证明语义支持性'}
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',db.dumps(manifest));z.writestr('index.md','# 知识库导出\n\n'+'\n'.join('- '+p['title']+'：'+p['namespace']+'/'+p['id']+'.md' for p in pages))
        z.writestr('log.md','# 变更记录\n\n'+'\n'.join(e['created_at']+' '+e['note'] for e in db.rows('SELECT * FROM knowledge_events WHERE project_id=? ORDER BY created_at',(pid,))))
        for p in pages:z.writestr(p['namespace']+'/'+p['id']+'.md',wiki.markdown(p))
    content=__import__('hashlib').sha256(path.read_bytes()).hexdigest();refs=[lineage.ref(pid,'wiki_version',p['version']['id']) for p in pages if p.get('current')]
    db.execute('INSERT INTO knowledge_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(identity,pid,task_id,'knowledge_export',content,str(path),db.dumps({'page_versions':{p['id']:p['current_version_id'] for p in pages},'refs':refs}),'ready',db.now()))
    lineage.add_dependencies(pid,'artifact',identity,content,refs)
    return {'id':identity,'pages':len(pages),'download':f'/api/v1/projects/{pid}/knowledge/exports/{identity}','contains_original_text':True}

def writing_trace(pid):
    security.require_project(pid)
    return {'format':'knowledge-writing-trace.v1','release_id':db.setting('knowledge_release:'+pid),
            'runs':[{'id':r['id'],'manifest':db.loads(r['manifest'],{})} for r in db.rows("SELECT id,manifest FROM generation_runs WHERE project_id=? AND json_type(manifest,'$.knowledge_refs')='array'",(pid,))],
            'note':'记录生成时使用的派生知识及来源；支持性与当前有效性须分别核对'}
