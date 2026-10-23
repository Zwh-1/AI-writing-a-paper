import pytest
from test_workbench import client,project,file
from app import db,parsers

def sample(client,pid,text='参考文献不少于二十篇，外文文献不少于两篇。',role='research'):
    f=file(client,pid,text,role=role);return f,db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))

def run(client,pid,cases):return client.post('/api/v1/projects/'+pid+'/retrieval/evaluate',json={'cases':cases})

@pytest.mark.parametrize('placeholder',['人工确认的正确来源ID','实际来源ID','真实来源ID','来源ID'])
def test_retrieval_evaluation_rejects_placeholder_ids_with_actionable_message(client,placeholder):
    pid=project(client)
    response=run(client,pid,[{'query':'参考文献要求是多少？','expected_source_ids':[placeholder]}])
    assert response.status_code==422 and '第 1 个问题' in response.json()['detail'] and '示例来源 ID' in response.json()['detail']
    assert not db.rows('SELECT * FROM retrieval_evaluations')

@pytest.mark.parametrize('query',['待检验的问题','请输入要验证的真实问题'])
def test_retrieval_evaluation_rejects_example_question(client,query):
    pid=project(client);f,s=sample(client,pid)
    response=run(client,pid,[{'query':query,'expected_source_ids':[s['id']]}])
    assert response.status_code==422 and '示例问题' in response.json()['detail']

def test_retrieval_evaluation_validates_entire_batch_before_search(client,monkeypatch):
    pid=project(client);f,s=sample(client,pid);calls=[]
    monkeypatch.setattr(parsers,'search',lambda *args,**kwargs:calls.append(args) or [])
    response=run(client,pid,[{'query':'参考文献要求是多少？','expected_source_ids':[s['id']]},{'query':'另一项要求是什么？','expected_source_ids':['nonexistent']}])
    assert response.status_code==422 and '第 2 个问题' in response.json()['detail'] and '当前项目' in response.json()['detail']
    assert not calls and not db.rows('SELECT * FROM retrieval_evaluations')

def test_retrieval_evaluation_explains_cross_project_source(client):
    pid=project(client);other=project(client,'另一个项目');f,s=sample(client,other)
    response=run(client,pid,[{'query':'参考文献要求是多少？','expected_source_ids':[s['id']]}])
    assert response.status_code==422 and '当前项目中不存在' in response.json()['detail']

def test_retrieval_evaluation_rejects_disabled_material(client):
    pid=project(client);f,s=sample(client,pid);client.patch('/api/v1/files/'+f['id'],json={'active':False})
    response=run(client,pid,[{'query':'参考文献要求是多少？','expected_source_ids':[s['id']]}])
    assert response.status_code==422 and '已停用' in response.json()['detail']

@pytest.mark.parametrize('kind',['unknown',False,[],{}])
def test_retrieval_evaluation_rejects_invalid_scope(client,kind):
    pid=project(client);f,s=sample(client,pid)
    response=run(client,pid,[{'query':'参考文献要求是多少？','expected_source_ids':[s['id']],'kind':kind}])
    assert response.status_code==422 and '检索范围' in response.json()['detail']

def test_retrieval_evaluation_rejects_source_outside_selected_scope(client):
    pid=project(client);f,s=sample(client,pid,role='example')
    response=run(client,pid,[{'query':'参考文献要求是多少？','expected_source_ids':[s['id']],'kind':'research'}])
    assert response.status_code==422 and '不在所选检索范围' in response.json()['detail']

def test_retrieval_evaluation_normalizes_ids_and_does_not_mark_originals_reviewed(client,monkeypatch):
    pid=project(client);f,s=sample(client,pid);calls=[]
    def search(*args,**kwargs):calls.append((args,kwargs));return [{'id':s['id']}]
    monkeypatch.setattr(parsers,'search',search)
    response=run(client,pid,[{'query':' 参考文献要求是多少？ ','expected_source_ids':[s['id'],' '+s['id']+' '],'kind':'all'}])
    assert response.status_code==200,response.text
    result=response.json();assert result['recall_at_10']==1 and result['cases'][0]['expected_source_ids']==[s['id']]
    assert calls[0][0][1]=='参考文献要求是多少？' and calls[0][1]['limit']==10
    assert result['formal_human_acceptance'] is False and result['human_confirmation']=='not_recorded'
    assert db.one('SELECT review_state FROM pages WHERE file_id=?',(f['id'],))['review_state']=='unreviewed'
    assert db.one('SELECT verified FROM sources WHERE id=?',(s['id'],))['verified']==0
    assert not db.rows('SELECT * FROM generation_runs')
