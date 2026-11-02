import io,json,hashlib,zipfile
import pytest
from fastapi import HTTPException
from test_workbench import client,project,file
from test_graph_wiki import library,build,no_remote_models
from app import db,tasks,knowledge_graph as graph,knowledge_lineage as lineage,knowledge_wiki as wiki
from app import knowledge_decisions as decisions,knowledge_wiki_merge as merge,knowledge_maintenance as maintenance

def assertion(library):
    c,pid,source,parsed=library;build(pid);a=graph.search(pid,'单用户')['assertions'][0]
    oid=next(r['id'] for r in a['sources'] if r['type']=='observation');observation=lineage.owned(pid,'observation',oid)
    body={'observation_id':oid,'quote':observation['text'],'support':'supported','reviewer':'合成核对人','note':'合成原文已保留条件','compared_original':True}
    return pid,a,body

def test_human_support_wins_over_later_model(library):
    pid,a,body=assertion(library);cid=a['claim_version_id']
    human=graph.assess(pid,cid,body)
    graph.assess(pid,cid,{**body,'support':'contradicted'},'model')
    result=graph.get_assertion(pid,cid)
    assert result['support_state']=='supported' and result['human_reviewed']
    assert result['active_assessment_ids']==[human['id']]

def test_conflicting_humans_require_append_only_adjudication(library):
    pid,a,body=assertion(library);cid=a['claim_version_id'];first=graph.assess(pid,cid,body)
    second=graph.assess(pid,cid,{**body,'support':'partial','reviewer':'合成核对人二'})
    current=graph.get_assertion(pid,cid);assert current['requires_adjudication'] and current['support_state']=='not_checked'
    selected={'expected_assessment_fingerprint':current['assessment_fingerprint'],'assessment_ids':[first['id'],second['id']],
              'support':'partial','reviewer':'合成裁决人','note':'条件尚有缺口','compared_original':True}
    result=decisions.adjudicate(pid,cid,selected)
    assert graph.get_assertion(pid,cid)['decision']['id']==result['id']
    with pytest.raises(Exception):db.execute("UPDATE kg_support_decisions SET support='supported' WHERE id=?",(result['id'],))
    graph.assess(pid,cid,body)
    assert graph.get_assertion(pid,cid)['decision_outdated']

def wiki_fixture(library):
    c,pid,_,_=library;page=wiki.create_page(pid,{'namespace':'literature','slug':'merge-fixture','kind':'topic','title':'合成合并'})
    first=wiki.revise(pid,page['id'],{'base_version_id':None,'blocks':[{'key':'one','kind':'note','text':'第一段'},{'key':'two','kind':'note','text':'第二段'}]})
    wiki.publish(pid,page['id'],{'base_version_id':None,'version_id':first['version']['id']})
    second=wiki.revise(pid,page['id'],{'base_version_id':first['version']['id'],'blocks':[{'key':'one','kind':'note','text':'本地修改第一段'},{'key':'two','kind':'note','text':'第二段'}]})
    wiki.publish(pid,page['id'],{'base_version_id':first['version']['id'],'version_id':second['version']['id']})
    return pid,page,first,second

def test_three_way_merge_combines_disjoint_edits_and_preserves_drafts(library):
    pid,page,base,current=wiki_fixture(library)
    result=merge.submit(pid,page['id'],{'base_version_id':base['version']['id'],'blocks':[{'key':'one','kind':'note','text':'第一段'},{'key':'two','kind':'note','text':'候选修改第二段'}]})
    preview=merge.preview(pid,result['conflict_id']);assert preview['conflict_count']==0
    merged=merge.resolve(pid,result['conflict_id'],{'expected_fingerprint':preview['fingerprint'],'reviewer':'合成编辑','note':'保留不重叠修改','choices':{}})
    assert [b['text'] for b in merged['blocks']]==['本地修改第一段','候选修改第二段']
    assert wiki.get_page(pid,page['id'])['version']['id']==current['version']['id']
    assert not merged['published']

def test_overlapping_edits_require_choice_and_reject_changed_head(library):
    pid,page,base,current=wiki_fixture(library)
    result=merge.submit(pid,page['id'],{'base_version_id':base['version']['id'],'blocks':[{'key':'one','kind':'note','text':'另一份修改'},{'key':'two','kind':'note','text':'第二段'}]})
    p=merge.preview(pid,result['conflict_id']);assert p['conflict_count']==1
    with pytest.raises(HTTPException):merge.resolve(pid,p['id'],{'expected_fingerprint':p['fingerprint'],'reviewer':'合成','note':'未选择'})
    done=merge.resolve(pid,p['id'],{'expected_fingerprint':p['fingerprint'],'reviewer':'合成','note':'人工比较','choices':{'one':{'choice':'incoming'}}})
    assert done['blocks'][0]['text']=='另一份修改'

def test_maintenance_lists_all_stale_topics_not_just_six(library):
    pid,a,body=assertion(library);refs=[lineage.ref(pid,'assertion',a['claim_version_id'])]
    for index in range(9):
        page=wiki.create_page(pid,{'namespace':'literature','kind':'topic','title':'单用户'+str(index),'slug':'topic-'+str(index)})
        v=wiki.revise(pid,page['id'],{'base_version_id':None,'blocks':[{'key':'x','kind':'synthesis','text':'原文限定单用户','sources':refs}]},'model')
        wiki.publish(pid,page['id'],{'base_version_id':None,'version_id':v['version']['id']})
    graph.assess(pid,a['claim_version_id'],body)
    result=maintenance.prepare(pid,'literature',reports=False)
    assert len([r for r in result['items'] if r['kind']=='synthesize'])==9
    again=maintenance.prepare(pid,'literature',reports=False)
    assert len(again['items'])==len(result['items'])

def test_maintenance_preserves_unprocessed_items_when_budget_blocks(library,monkeypatch):
    from app.summary_billing import BudgetExceeded
    c,pid,_,_=library
    for i in range(8):maintenance.schedule(pid,'synthesize',str(i),{'title':str(i),'namespace':'literature'})
    monkeypatch.setattr('app.knowledge_compiler.synthesize',lambda *args:(_ for _ in ()).throw(BudgetExceeded('合成预算阻断')))
    result=maintenance.run(tasks.enqueue(pid,'knowledge_maintenance',{}),{'namespace':'literature','reports':False,'budget_limit':.01})
    assert result['counts']['budget_blocked']==1 and result['counts']['queued']==7

def test_argument_plan_retains_conditions_and_does_not_rank_incomparable_results(library):
    from app.knowledge_planning import plan
    pid,a,_=assertion(library);b={**a,'claim_version_id':'other','attribution':'另一篇','object':{'metric':'响应时间','value':1,'unit':'秒','comparator':'未提供'}}
    a={**a,'object':{'metric':'准确率','value':.9,'unit':'百分比','comparator':'基线'}}
    refs=[{'type':'assertion','id':x['claim_version_id'],'version':'synthetic'} for x in (a,b)]
    p=plan(pid,'比较方法',[a,b],[],refs)
    assert p['comparability_checks'][0]['status']=='not_directly_comparable'
    assert not p['comparability_checks'][0]['automatic_ranking_allowed']
    assert p['candidate_arguments'][0]['conditions']==a['conditions']

def test_auto_route_does_not_spend_without_explicit_model_flag(client):
    from app.knowledge_planning import route_plan
    pid=project(client);r=route_plan(pid,{'query':'探索新的研究路线','backend':'auto'})
    assert r['recommended_backend']=='drift' and r['selected_backend']=='native' and not r['paid']

def test_explicit_backend_unavailable_is_not_silent_native_success(client):
    from app.knowledge_planning import execute
    pid=project(client)
    with pytest.raises(HTTPException) as error:execute(tasks.enqueue(pid,'knowledge_query',{}),{'query':'综述','backend':'global'})
    assert error.value.detail['code']=='REQUESTED_BACKEND_UNAVAILABLE'

def test_evaluation_report_does_not_claim_95_without_gold_and_reviews(client):
    from app.knowledge_comparisons import report
    pid=project(client);tid=tasks.enqueue(pid,'knowledge_compare',{})['id'];cid=db.uid()
    variants={v:{'unit_ids':[],'evidence_ids':[],'recall':None,'mrr':None,'elapsed_seconds':1,'human_semantic_review':None} for v in ('A','B','C','D')}
    db.execute('INSERT INTO knowledge_comparisons VALUES(?,?,?,?,?,?,?)',(cid,pid,tid,'{}',db.dumps([{'case_id':'fixture','gold_state':'unlabelled','variants':variants}]),'awaiting_human_review',db.now()))
    r=report(pid,cid)
    assert not r['semantic_quality_verified'] and r['groups']['D']['support'] is None

def test_complete_bundle_roundtrip_and_fork_preserve_originals(library):
    from pathlib import Path
    from app import knowledge_transfer as transfer
    c,pid,f,parsed=library;build(pid)
    result=transfer.export(pid);raw=Path(result['path']).read_bytes();preview=transfer.preview(raw)
    assert preview['existing_project'] and preview['tables']['knowledge_units']
    original=db.rows('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    restored=transfer.restore(raw,{'mode':'isolated','expected_sha256':result['sha256']})
    assert not restored['automatically_applied'] and not restored['foreign_key_errors']
    fork=transfer.restore(raw,{'mode':'fork','expected_sha256':result['sha256'],'title':'导入合成项目'})
    assert fork['project_id']!=pid and not fork['foreign_key_errors']
    assert db.rows('SELECT * FROM sources WHERE file_id=?',(f['id'],))==original
    assert graph.search(fork['project_id'],'单用户')['assertions']
    assert wiki.search(fork['project_id'],'单用户')['items']

def test_bundle_hash_and_traversal_fail_before_import(library):
    from pathlib import Path
    from app import knowledge_transfer as transfer
    _,pid,_,_=library;result=transfer.export(pid);raw=Path(result['path']).read_bytes()
    source=zipfile.ZipFile(io.BytesIO(raw));out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name in source.namelist():z.writestr(name,source.read(name))
        z.writestr('../escape','invalid')
    with pytest.raises(ValueError):transfer.validate(out.getvalue())
    with pytest.raises(ValueError):transfer.restore(raw,{'mode':'fork','expected_sha256':'wrong'})


def test_encrypted_cloud_requires_etag_and_preserves_remote_conflicts(library,monkeypatch):
    import httpx
    from app import knowledge_transfer as transfer
    c,pid,_,_=library;db.set_setting('sharing:'+pid,True);state={};requests=[]
    def handle(request):
        requests.append(request)
        if request.method=='PUT':
            if state.get('data') and request.headers.get('if-match')!='"fixture-1"':return httpx.Response(412)
            state['data']=request.content;return httpx.Response(201,headers={'etag':'"fixture-1"'})
        return httpx.Response(200,headers={'etag':'"fixture-1"'},content=state['data'])
    real=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real(transport=httpx.MockTransport(handle)))
    body={'url':'https://cloud.example/knowledge.bin','username':'fixture','cloud_password':'fixture-cloud','sync_password':'fixture-long-local-password'}
    pushed=transfer.cloud(pid,body);assert pushed['etag']=='"fixture-1"'
    assert state['data'].startswith(b'PAPER-KNOWLEDGE-3\0') and b'knowledge_observations' not in state['data']
    pulled=transfer.cloud(pid,body,True);assert pulled['preview']['project_id']==pid and not pulled['automatically_applied']
    with pytest.raises(HTTPException) as error:transfer.cloud(pid,body)
    assert error.value.status_code==409
    assert requests[0].headers['if-none-match']=='*'
    transfer.cloud(pid,{**body,'etag':'"fixture-1"'})


def test_cloud_never_uploads_local_only_sources(library,monkeypatch):
    from app import knowledge_transfer as transfer
    c,pid,f,_=library;db.set_setting('sharing:'+pid,True);db.execute("UPDATE files SET egress='local_only' WHERE id=?",(f['id'],))
    monkeypatch.setattr(transfer,'export',lambda *args:pytest.fail('Blocked before export or network'))
    with pytest.raises(HTTPException):transfer.cloud(pid,{'url':'https://cloud.example/snapshot'})


def test_real_annotation_packet_keeps_human_fields_unset(library):
    from app.knowledge_comparisons import seed
    from app.knowledge_acceptance import annotation_packet
    _,pid,_,_=library;seed(pid);packet=annotation_packet(pid)
    assert len(packet['cases'])==80 and not packet['human_acceptance']
    assert all(r['state']=='unlabelled' and not r['human_passed'] and r['human_reviewer'] is None for r in packet['cases'])


def test_local_backup_retains_knowledge_resource_files(library):
    from pathlib import Path
    from app import config,exports
    _,pid,_,_=library;folder=config.DATA/'knowledge'/'graphrag'/'fixture-index';folder.mkdir(parents=True)
    resource=folder/'community_reports.parquet';resource.write_bytes(b'fixture original community bytes')
    aid=db.uid();db.execute('INSERT INTO knowledge_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(aid,pid,None,'graphrag_index','fixture-hash',str(folder),db.dumps({'packet':{'folder':str(folder)}}),'ready',db.now()))
    saved=exports.backup();raw=(config.DATA/'backups'/saved['filename']).read_bytes();restored=exports.restore_backup(raw)
    location=Path(restored['restored_data_directory']);resource_root=location/'knowledge-resources'/aid
    assert (resource_root/'community_reports.parquet').read_bytes()==resource.read_bytes()
    import sqlite3
    with sqlite3.connect(location/'workbench.sqlite3') as c:
        path=c.execute('SELECT path FROM knowledge_artifacts WHERE id=?',(aid,)).fetchone()[0]
        assert Path(path)==resource_root


def test_fork_keeps_json_formatted_original_bytes_exact(client):
    from pathlib import Path
    from app import parsers,knowledge_units,knowledge_transfer as transfer,security
    pid=project(client);raw=b'{ "message" : "original bytes must not be canonicalized", "n": 1 }'
    original,_=parsers.register_file(pid,'raw-json-text.txt',raw,'research');parsers.parse_file(original['id']);knowledge_units.build(pid,original['id'])
    result=transfer.export(pid);fork=transfer.restore(Path(result['path']).read_bytes(),{'mode':'fork','expected_sha256':result['sha256']})
    imported=db.one('SELECT * FROM files WHERE project_id=?',(fork['project_id'],))
    assert imported['sha256']==original['sha256']
    assert security.safe_object(imported['sha256']).read_bytes()==raw


def test_assessment_updates_refresh_document_page_without_new_model_call(library,monkeypatch):
    pid,a,body=assertion(library);graph.assess(pid,a['claim_version_id'],{**body,'support':'partial'})
    page=db.one("SELECT p.* FROM wiki_pages p JOIN wiki_versions v ON v.id=p.current_version_id WHERE p.project_id=? AND p.kind='document' AND v.state='stale'",(pid,))
    assert page
    monkeypatch.setattr(tasks,'model_call',lambda *args,**kwargs:pytest.fail('Updating displayed support needs no new model request'))
    result=maintenance.run(tasks.enqueue(pid,'knowledge_maintenance',{}),{'namespace':'literature','reports':False})
    current=wiki.get_page(pid,page['id'])
    assert current['current'] and any('partial' in b['text'] for b in current['blocks'])
    assert result['counts']['completed']>=1


def test_auto_official_route_requires_matching_ready_index_and_exposes_failure(library,monkeypatch):
    from app import knowledge_planning as planning,graphrag_adapter as adapter
    c,pid,_,_=library;monkeypatch.setattr(adapter,'capabilities',lambda:{'installed':True})
    a_id=db.uid();db.execute('INSERT INTO knowledge_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(a_id,pid,None,'graphrag_index','fixture-index-version','unused',db.dumps({'namespace':'literature','packet':{'reports':True,'embeddings':False},'mapping':{}}),'ready',db.now()))
    request={'query':'全库研究综述','backend':'auto','namespace':'literature','allow_model':True,'budget_limit':.1,'include_originals':False}
    decision=planning.route_plan(pid,request)
    assert decision['selected_backend']=='global' and decision['artifact_id']==a_id
    monkeypatch.setattr(adapter,'query',lambda *args:(_ for _ in ()).throw(__import__('app.provider',fromlist=['ProviderError']).ProviderError('fixture 524',unknown=True)))
    with pytest.raises(__import__('app.provider',fromlist=['ProviderError']).ProviderError):planning.execute(tasks.enqueue(pid,'knowledge_query',request),request)


def test_human_review_metrics_require_real_counts_and_keep_history(client):
    from app import knowledge_comparisons as comparisons
    pid=project(client);cid=db.uid();case=db.uid();task=tasks.enqueue(pid,'knowledge_compare',{})
    db.execute('INSERT INTO knowledge_evaluation_cases VALUES(?,?,?,?,?,?,?)',(case,pid,'locating','合成问题','{}','labelled',db.now()))
    result=[{'case_id':case,'gold_state':'labelled','variants':{'D':{'unit_ids':[],'recall':1.,'mrr':1.,'elapsed_seconds':1.,'human_semantic_review':None}}}]
    db.execute('INSERT INTO knowledge_comparisons VALUES(?,?,?,?,?,?,?)',(cid,pid,task['id'],'{}',db.dumps(result),'awaiting_human_review',db.now()))
    body={'case_id':case,'variant':'D','reviewer':'合成评审','note':'合成统计验证','supported_claims':19,'checkable_claims':20,'qualifiers_preserved':4,'qualifiers_required':4,'wrong_citations':0,'unsupported_expansions':0,'comparisons_valid':2,'comparisons_required':2,'contradictions_identified':1,'contradictions_required':1,'user_revision_count':3,'completion_seconds':12.5}
    comparisons.review(pid,cid,body);report=comparisons.report(pid,cid)
    assert report['groups']['D']['support']['rate']==.95
    assert report['groups']['D']['support']['lower']<.95
    assert report['groups']['D']['totals']['user_revision_count']==3
    assert len(report['review_history'])==1
    comparisons.review(pid,cid,{**body,'supported_claims':18})
    assert len(comparisons.report(pid,cid)['review_history'])==2
