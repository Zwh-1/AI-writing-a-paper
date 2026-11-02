import pytest
from fastapi import HTTPException
from test_workbench import client,project,file,manual_package
from test_graph_wiki import no_remote_models,build
from app import db,knowledge_units as units,knowledge_router as router,knowledge_graph as graph,knowledge_wiki as wiki
from app.source_workspace import overview

def test_unified_source_map_preserves_one_identity_with_multiple_uses(client):
    pid=project(client);f=file(client,pid,'原件支持研究用途，也保留写作示例。',role='example')
    db.execute('INSERT INTO file_source_profiles VALUES(?,?,?,?)',(f['id'],'research_publication','合成核对',db.now()))
    db.execute('INSERT INTO file_usage_additions VALUES(?,?,?,?)',(f['id'],'research','合成追加用途',db.now()))
    ref=client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'合成书目'},'file_id':f['id']}).json()
    units.build(pid,f['id'],allow_material=True)
    result=client.get(f'/api/v1/projects/{pid}/source-workspace')
    assert result.status_code==200,result.text
    data=result.json();assert len(data['files'])==1
    source=data['files'][0];assert source['id']==f['id'] and source['project_id']==pid and source['sha256']==f['sha256'] and source['usages']==['example','research']
    assert source['research_eligible'] and source['guidance_eligible']
    assert source['references'][0]['id']==ref['id'] and source['parse_id']
    assert source['knowledge_units']>0 and data['counts']['research_files']==1

def test_source_counts_exclude_retired_rules_and_do_not_label_reference_as_published(client):
    pid=project(client);f=file(client,pid,'摘要应说明方法，示例用于理解。',role='rules');source=db.one('SELECT id FROM sources WHERE file_id=?',(f['id'],))
    ids=[]
    for state,modality in [('machine_verified','recommended'),('reference_only','example'),('candidate','pending'),('confirmed','mandatory')]:
        rid=db.uid();ids.append(rid)
        db.execute('INSERT INTO rules(id,project_id,source_id,state,modality,statement,created_at) VALUES(?,?,?,?,?,?,?)',(rid,pid,source['id'],state,modality,'合成规则',db.now()))
    db.set_setting('retired_rule_ids:'+pid,[ids[-1]])
    data=overview(pid);assert data['counts']['current_rules']==3 and data['counts']['eligible_rules']==1
    assert data['files'][0]['rules']=={'total':3,'eligible':1,'pending':1}
    assert data['counts']['rule_sources']==1

def test_source_map_is_project_scoped_and_read_only(client):
    a=project(client);b=project(client,'另一项目');file(client,a,'原件甲');file(client,b,'原件乙')
    tables=['files','pages','sources','bibliography','rules','wiki_pages'];before={t:db.rows('SELECT * FROM '+t) for t in tables}
    data=overview(a)
    assert len(data['files'])==1 and data['files'][0]['name']=='研究.txt'
    assert {t:db.rows('SELECT * FROM '+t) for t in tables}==before
    assert client.get('/api/v1/projects/no-such-project/source-workspace').status_code==404

def test_file_context_filters_graph_wiki_and_reading_plan(client):
    pid=project(client);a=file(client,pid,'单用户条件下方法甲可用。');b=file(client,pid,'另一种单用户方法只用于离线场景。')
    units.build(pid,a['id']);units.build(pid,b['id']);build(pid)
    filtered=router.query(pid,{'query':'单用户','namespace':'literature','include_originals':False,'file_ids':[a['id']]})
    assert filtered['reading_plan'] and {p['file_id'] for p in filtered['reading_plan']}=={a['id']}
    assert filtered['graph']['assertions']
    pages=client.get(f'/api/v1/projects/{pid}/knowledge/wiki',params={'file_id':a['id'],'namespace':'literature'}).json()['items']
    assert pages and all(p['slug']!='source-'+b['id'] for p in pages)
    wrong=client.post(f'/api/v1/projects/{pid}/knowledge/graph/search',json={'query':'单用户','file_ids':['external-id']})
    assert wrong.status_code==404
    other=project(client,'其他资料');foreign=file(client,other)
    with pytest.raises(HTTPException):router.query(pid,{'query':'单用户','file_ids':[foreign['id']],'include_originals':False})

def test_guidance_context_keeps_course_and_research_separate(client):
    pid=project(client);r=file(client,pid,'论文摘要应概括方法。',role='rules');f=file(client,pid,'方法用于单用户场景。')
    units.build(pid,r['id'],allow_material=True);units.build(pid,f['id']);build(pid,'all')
    result=graph.search(pid,'',ns='guidance',file_ids=[r['id']])
    assert result['assertions'] and all(a['namespace']=='guidance' for a in result['assertions'])
    assert not graph.search(pid,'',ns='literature',file_ids=[r['id']])['assertions']


def test_raw_search_context_filters_before_top_k(client):
    pid=project(client);a=file(client,pid,'credential identity method applies to single-user use.');b=file(client,pid,'credential identity method repeated credential identity credential identity')
    result=client.post(f'/api/v1/projects/{pid}/knowledge-base/search',json={'query':'credential identity','limit':1,'file_ids':[a['id']]})
    assert result.status_code==200,result.text
    assert result.json()['items'] and all(r['file_id']==a['id'] for r in result.json()['items'])


def test_maintenance_scope_does_not_execute_other_namespace_items(client,monkeypatch):
    from app import knowledge_maintenance as maintenance,tasks
    pid=project(client)
    one=maintenance.schedule(pid,'synthesize','guide',{'namespace':'guidance','title':'合成指导'})
    two=maintenance.schedule(pid,'synthesize','research',{'namespace':'literature','title':'合成研究'})
    seen=[]
    monkeypatch.setattr('app.knowledge_compiler.synthesize',lambda task,payload:seen.append(payload['namespace']) or {'page_id':'synthetic'})
    status=maintenance.run(tasks.enqueue(pid,'knowledge_maintenance',{}),{'namespace':'guidance','reports':False})
    assert seen==['guidance'] and status['counts']['completed']==1
    assert db.one('SELECT status FROM knowledge_maintenance_items WHERE id=?',(two['id'],))['status']=='queued'
    assert len(maintenance.status(pid,'guidance')['items'])==1
