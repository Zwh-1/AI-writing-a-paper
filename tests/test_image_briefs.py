import copy
import json
import pytest
from fastapi import HTTPException
from app import db,documents,image_generation,security
from app.figure_standards import evaluate
from app.paper_visuals import TEMPLATES
from test_workbench import client,project,section,save
from test_image_generation import configured,response_image


def context(client):
    pid=project(client,'共享打印设备管理系统');sid=section(client,pid)
    rev=save(client,sid,'账户模块负责注册与身份验证。\n设备申请先校验配额，不足时拒绝，充足时创建打印订单。\n管理员通过统计模块查看任务记录。').json()
    doc=db.loads(rev['document']);anchor=doc['content'][1]['attrs']['nodeId']
    body={'kind':'flowchart','title':'打印申请处理流程','section_id':sid,'anchor_node_id':anchor,
          'instruction':'只画配额校验和打印订单创建，不扩展账户功能。','explanation':'配额不足时拒绝申请，配额充足时创建订单。',
          'required_labels':['校验配额','创建打印订单'],'forbidden_content':'支付、区块链','layout':'top_down'}
    return pid,sid,rev,body


def candidate(client,pid,body):
    preview=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-brief',json=body)
    assert preview.status_code==200,preview.text
    task=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json={**body,'brief_hash':preview.json()['brief']['hash']})
    assert task.status_code==200,task.text
    return image_generation.generate(task.json())['asset'],preview.json()


def reviewed(client,pid,sid,asset,body):
    revision=security.require_section(sid)['current_revision_id']
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/alignment-review',json={
        'section_id':sid,'anchor_node_id':body['anchor_node_id'],'revision_id':revision,
        'confirmed':True,'note':'核对了配额判断的两条分支及创建订单的方向，和本段与图后说明一致。'})
    assert result.status_code==200,result.text
    return result.json()


def test_free_brief_uses_specific_paragraph_explanation_and_constraints_without_key(client,monkeypatch):
    monkeypatch.delenv('PAPER_IMAGE_API_KEY',raising=False);security.save_key('',image_generation.PROFILE)
    monkeypatch.setattr(image_generation,'request_image',lambda *a,**k:pytest.fail('free preview must not call a model'))
    pid,sid,rev,body=context(client)
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-brief',json=body)
    assert result.status_code==200,result.text
    result=result.json();brief=result['brief']
    assert brief['target']['text'].startswith('设备申请先校验配额')
    assert brief['target']['revision_id']==rev['id'] and brief['target']['node_id']==body['anchor_node_id']
    assert brief['explanation']==body['explanation'] and brief['blueprint'] is None
    assert '必须逐字保留的文字' in result['prompt'] and '支付、区块链' in result['prompt']
    assert '学生成绩' not in result['prompt'] and body['anchor_node_id'] not in result['prompt']
    assert not db.rows('SELECT * FROM tasks') and not db.rows('SELECT * FROM generation_runs')
    choices=client.get(f'/api/v1/projects/{pid}/paper-visuals/positions/{sid}').json()
    assert len(choices['blocks'])==3


def test_multi_paragraph_generation_requires_explicit_position(client,configured):
    pid,sid,rev,body=context(client);body.pop('anchor_node_id')
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json=body)
    assert result.status_code==422 and '具体段落' in result.text
    assert not db.rows('SELECT * FROM tasks')


def test_changed_brief_blocks_queue_before_budget_or_network(client,configured):
    calls,_=configured;pid,sid,rev,body=context(client)
    brief=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-brief',json=body).json()
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json={**body,'instruction':'另画支付流程','brief_hash':brief['brief']['hash']})
    assert response.status_code==409 and not calls
    assert not db.rows('SELECT * FROM tasks') and not db.rows('SELECT * FROM generation_runs')


def test_frozen_prompt_is_exact_and_retained_with_output(client,configured):
    calls,_=configured;pid,sid,rev,body=context(client)
    asset,preview=candidate(client,pid,body)
    assert json.loads(calls[0].content)['prompt']==preview['prompt']
    assert asset['metadata']['image_brief']==preview['brief']
    assert asset['metadata']['alignment_status']=='pending_human_review'
    assert security.require_section(sid)['current_revision_id']==rev['id']
    record=client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download?format=source').json()
    assert record['image_brief']==preview['brief'] and record['prompt']==preview['prompt']


def test_provider_rewritten_prompt_is_retained_in_the_downloadable_record(client,configured):
    _,answers=configured;answers[0]['data'][0]['revised_prompt']='模拟服务改写：应保留配额判断、拒绝分支和创建订单。'
    pid,sid,rev,body=context(client);asset,_=candidate(client,pid,body)
    record=client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download?format=source').json()
    assert record['revised_prompt']==answers[0]['data'][0]['revised_prompt']


def test_source_change_while_queued_prevents_paid_request(client,configured):
    calls,_=configured;pid,sid,rev,body=context(client)
    task=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json=body).json()
    doc=db.loads(rev['document']);doc['content'][1]['content'][0]['text']='配额校验改为由管理员审批。'
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':rev['id']}).status_code==200
    with pytest.raises(HTTPException,match='正文已变化'):image_generation.generate(task)
    assert not calls and not db.rows('SELECT * FROM generation_runs')


def test_changed_source_during_remote_generation_preserves_candidate_but_blocks_insertion(client,configured,monkeypatch):
    pid,sid,rev,body=context(client)
    def respond(*args,**kwargs):
        doc=db.loads(rev['document']);doc['content'][1]['content'][0]['text']='新增人工审核后才创建订单。'
        changed=client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':rev['id']})
        assert changed.status_code==200
        return response_image()
    monkeypatch.setattr(image_generation,'request_image',respond)
    asset,_=candidate(client,pid,body)
    assert asset['metadata']['alignment_status']=='source_changed'
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/alignment-review',json={
        'section_id':sid,'anchor_node_id':body['anchor_node_id'],'revision_id':security.require_section(sid)['current_revision_id'],
        'confirmed':True,'note':'不能用确认按钮覆盖已改变的设计。'})
    assert result.status_code==409
    assert db.one('SELECT * FROM generation_runs')['status']=='completed'


def test_human_review_and_anchor_control_all_insertion_routes_and_retry(client,configured):
    pid,sid,rev,body=context(client);asset,_=candidate(client,pid,body)
    endpoint=f'/api/v1/sections/{sid}/paper-visuals/{asset["id"]}/insert'
    assert client.post(endpoint,json={'base_revision_id':rev['id']}).status_code==422
    doc=db.loads(rev['document']);doc['content']+=asset['insert_nodes']
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':rev['id']}).status_code==422
    review=reviewed(client,pid,sid,asset,body)
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':rev['id']}).status_code==422
    inserted=client.post(endpoint,json={'base_revision_id':rev['id'],'review_id':review['review_id']})
    assert inserted.status_code==200,inserted.text
    new=inserted.json()['revision'];nodes=db.loads(new['document'])['content']
    assert nodes[1]['attrs']['nodeId']==body['anchor_node_id'] and nodes[2]['type']=='figure' and nodes[3]['attrs']['describesFigureId']==nodes[2]['attrs']['nodeId']
    assert client.post(endpoint,json={'base_revision_id':new['id'],'review_id':review['review_id']}).json()['already_inserted']
    assert client.post(endpoint,json={'base_revision_id':rev['id'],'review_id':review['review_id']}).json()['already_inserted']
    assert len([n for n in nodes if n['type']=='figure'])==1
    finding=next(r for r in evaluate(pid)['items'] if r['code']=='IMAGE_PLACEMENT_ALIGNMENT')
    assert finding['result']=='pass'


def test_repositioning_an_inserted_image_is_reported_as_a_real_gap(client,configured):
    pid,sid,rev,body=context(client);asset,_=candidate(client,pid,body);review=reviewed(client,pid,sid,asset,body)
    inserted=client.post(f'/api/v1/sections/{sid}/paper-visuals/{asset["id"]}/insert',json={'base_revision_id':rev['id'],'review_id':review['review_id']}).json()['revision']
    doc=db.loads(inserted['document']);doc['content'].append(doc['content'].pop(2))
    result=client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':inserted['id']})
    assert result.status_code==200,result.text
    assert next(r for r in evaluate(pid)['items'] if r['code']=='IMAGE_PLACEMENT_ALIGNMENT')['result']=='fail'


def test_review_of_one_position_cannot_authorize_another_or_changed_explanation(client,configured):
    pid,sid,rev,body=context(client);asset,_=candidate(client,pid,body)
    other=section(client,pid);save(client,other,'这是另一个章节。')
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/alignment-review',json={
        'section_id':other,'revision_id':security.require_section(other)['current_revision_id'],'confirmed':True,'note':'此图不应插入另一个章节。'})
    assert response.status_code==409
    reviewed(client,pid,sid,asset,body)
    annotated=client.post(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/annotate',json={'explanation':'图后说明已改为需要重新核对的另一版本。'}).json()
    response=client.post(f'/api/v1/sections/{sid}/paper-visuals/{annotated["id"]}/insert',json={'base_revision_id':rev['id']})
    assert response.status_code==422


def test_edit_uses_current_target_and_excludes_historical_text(client,configured):
    pid,sid,rev,body=context(client);asset,_=candidate(client,pid,body)
    doc=db.loads(rev['document']);old=doc['content'][1]['content'][0]['text'];doc['content'][1]['content'][0]['text']='配额不足则返回修正，充足时登记新的任务队列。'
    assert client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':rev['id']}).status_code==200
    revised={**body,'source_artifact_id':asset['id'],'instruction':'仅把创建订单调整为登记任务队列。','required_labels':['校验配额','登记任务队列']}
    preview=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-brief',json=revised)
    assert preview.status_code==200,preview.text
    brief=preview.json()['brief']
    assert brief['excluded_sources'][0]['reason']=='historical_revision'
    assert old not in preview.json()['prompt']
    assert brief['preserve']['required_labels']==body['required_labels']
    assert brief['required_labels']==revised['required_labels']


def test_blueprint_is_only_sent_when_explicitly_provided(client,configured):
    pid,sid,rev,body=context(client)
    blueprint=copy.deepcopy(TEMPLATES['flowchart']);blueprint['nodes'][1]['label']='提交打印申请'
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-brief',json={**body,'current_spec':blueprint})
    assert result.status_code==200,result.text
    assert result.json()['brief']['blueprint']['nodes'][1]['label']=='提交打印申请'
    assert '提交打印申请' in result.json()['brief']['required_labels']


def test_standalone_candidate_can_be_explicitly_bound_during_human_review(client,configured):
    pid,sid,rev,body=context(client)
    standalone={k:v for k,v in body.items() if k not in ('section_id','anchor_node_id')}
    asset,_=candidate(client,pid,standalone);review=reviewed(client,pid,sid,asset,body)
    response=client.post(f'/api/v1/sections/{sid}/paper-visuals/{asset["id"]}/insert',json={'base_revision_id':rev['id'],'review_id':review['review_id']})
    assert response.status_code==200,response.text
    assert next(r for r in evaluate(pid)['items'] if r['code']=='IMAGE_PLACEMENT_ALIGNMENT')['result']=='pass'
