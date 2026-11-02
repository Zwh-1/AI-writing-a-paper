from test_workbench import client
from test_knowledge_batch_compiler import library
from app import db,knowledge_compiler,knowledge_wiki_checks,knowledge_model_bridge


def test_verbatim_excerpt_support_check_sends_full_original_once(client,monkeypatch):
    pid,original,task=library(client)
    compiled=knowledge_compiler.compile_file(task,original['id'],'literal',namespace='literature')
    seen=[]
    def call(task,operation,payload,refs):
        seen.append(payload)
        assert payload['claim']['statement_reference']['field']=='original.text'
        assert 'statement' not in payload['claim']
        return {'support':'not_checked','quote':'','note':'原文摘录对应关系不等于研究语义判断','conditions':[]},'synthetic-support-payload'
    monkeypatch.setattr(knowledge_model_bridge,'json_call',call)
    result=knowledge_wiki_checks.assess_with_model(task,{'claim_ids':[compiled['assertion_ids'][0]]})
    observation=db.one('SELECT text FROM knowledge_observations WHERE id=?',(compiled['observation_ids'][0],))
    assert seen[0]['original']['text']==observation['text']
    assert result['assessments'][0]['support']=='not_checked' and not result['human_approval_added']
    assert not db.rows('SELECT * FROM knowledge_review_events')
