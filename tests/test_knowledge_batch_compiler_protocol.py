import json
from test_workbench import client
from app import knowledge_batch_compiler as batch,knowledge_model_bridge,db
from test_knowledge_batch_compiler import library


def test_invalid_json_protocol_can_be_repaired_without_new_source_request(client,monkeypatch):
    pid,original,task=library(client);calls=[]
    def call(task,operation,payload,refs):
        calls.append(operation)
        if operation=='knowledge_extract_batch':raise ValueError('unterminated string literal')
        return {'observations':[{'observation_id':o['id'],'claims':[],'no_claim_reason':'输出协议修复后仍无可采用论断'} for o in payload['observations']]},'repair-run'
    monkeypatch.setattr(knowledge_model_bridge,'json_call',call)
    result=batch.extract_batch(task,[batch.compiler.observe_unit(pid,db.one('select id from knowledge_units limit 1')['id'],remote=True)])
    assert calls==['knowledge_extract_batch','knowledge_extract_batch_protocol_repair']
    assert result[next(iter(result))]['no_claim_reason']
