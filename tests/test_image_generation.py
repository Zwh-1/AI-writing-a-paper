import base64
import io
import json
import httpx
import pytest
from PIL import Image
from app import db,security,provider,tasks,image_generation
from app.paper_visuals import TEMPLATES
from test_workbench import client,project,section,save,file

IMAGE_KEY='fixture-independent-image-secret'
PRIMARY_KEY='fixture-primary-text-secret'
BACKUP_KEY='fixture-backup-text-secret'


def response_image(model='gpt-image-2'):
    out=io.BytesIO();Image.new('RGB',(256,256),'white').save(out,format='PNG')
    return {'model':model,'data':[{'b64_json':base64.b64encode(out.getvalue()).decode()}],
            'usage':{'input_tokens':11,'output_tokens':22,'input_tokens_details':{'text_tokens':11,'image_tokens':0}}}


@pytest.fixture
def configured(client,monkeypatch):
    for key in ('PAPER_API_KEY','PAPER_GPT_API_KEY','PAPER_IMAGE_API_KEY','PAPER_SUMMARY_API_KEY'):monkeypatch.delenv(key,raising=False)
    security.save_key(PRIMARY_KEY);security.save_key(BACKUP_KEY,'gpt-fallback')
    result=client.put('/api/v1/image-generation/settings',json={'provider':{'base_url':'https://api.hanhegufei.online/v1'},'api_key':IMAGE_KEY})
    assert result.status_code==200,result.text
    calls=[];answers=[response_image()]
    def handler(request):
        calls.append(request)
        answer=answers[-1]
        if isinstance(answer,Exception):raise answer
        if isinstance(answer,httpx.Response):return answer
        return httpx.Response(200,json=answer)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handler),timeout=cfg.get('timeout_seconds',600)))
    return calls,answers


def enqueue(client,pid,**extra):
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json={'kind':'flowchart','title':'真实设计流程图','instruction':'根据提供设计作图',**extra})
    assert response.status_code==200,response.text
    return response.json()


def test_image_settings_key_is_isolated_masked_and_blank_preserves(client,configured):
    original=provider.config()
    settings=client.get('/api/v1/image-generation/settings').json()
    assert settings['provider']['model']=='gpt-image-2' and settings['ready']
    assert IMAGE_KEY not in json.dumps(settings) and settings['credential']['masked'].endswith(IMAGE_KEY[-4:])
    assert security.get_key()==PRIMARY_KEY and security.get_key('gpt-fallback')==BACKUP_KEY
    assert security.get_key('image-generation')==IMAGE_KEY
    assert provider.config()==original
    assert client.put('/api/v1/image-generation/settings',json={'provider':{'quality':'medium'}}).status_code==200
    assert security.get_key('image-generation')==IMAGE_KEY
    assert IMAGE_KEY not in security.redact('failure '+IMAGE_KEY)
    bad=client.put('/api/v1/image-generation/settings',json={'provider':{'model':'gpt-image-1.5'},'api_key':'not-saved'})
    assert bad.status_code==422 and security.get_key('image-generation')==IMAGE_KEY
    assert client.put('/api/v1/image-generation/settings',json={'clear_key':True}).status_code==200
    assert security.get_key('image-generation')=='' and security.get_key()==PRIMARY_KEY


def test_primary_and_backup_keys_are_never_image_fallback(client,monkeypatch):
    monkeypatch.delenv('PAPER_IMAGE_API_KEY',raising=False)
    security.save_key(PRIMARY_KEY);security.save_key(BACKUP_KEY,'gpt-fallback')
    pid=project(client)
    result=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json={'kind':'er','instruction':'按真实数据设计绘制'})
    assert result.status_code==422 and '独立' in result.text
    assert not db.rows('SELECT * FROM tasks')


def test_image_env_key_does_not_inherit_summary_or_primary(client,monkeypatch):
    monkeypatch.setenv('PAPER_API_KEY',PRIMARY_KEY);monkeypatch.setenv('PAPER_SUMMARY_API_KEY','summary-only')
    monkeypatch.delenv('PAPER_IMAGE_API_KEY',raising=False)
    assert security.get_key('image-generation')==''
    monkeypatch.setenv('PAPER_IMAGE_API_KEY',IMAGE_KEY)
    assert security.get_key('image-generation')==IMAGE_KEY


def test_generations_api_uses_exact_model_address_and_only_image_key(client,configured):
    calls,_=configured;pid=project(client);sid=section(client,pid);revision=save(client,sid,'管理员录入信息，通过验证后保存。').json()
    task=enqueue(client,pid,section_id=sid)
    result=image_generation.generate(task)
    assert len(calls)==1
    request=calls[0]
    assert str(request.url)=='https://api.hanhegufei.online/v1/images/generations'
    assert request.headers['authorization']=='Bearer '+IMAGE_KEY
    body=json.loads(request.content)
    assert body['model']=='gpt-image-2' and body['n']==1 and body['size']=='2048x1152'
    assert body['quality']=='high' and body['output_format']=='png'
    assert not {'response_format','input_fidelity','max_output_tokens','messages','style'} & body.keys()
    assert '管理员录入信息' in body['prompt'] and PRIMARY_KEY not in body['prompt']
    assert result['automatic_acceptance'] is False and result['render_engine']=='gpt-image-2'
    asset=result['asset'];metadata=asset['metadata']
    assert metadata['source_revision_ids']==[revision['id']] and metadata['render_engine']=='gpt-image-2'
    assert security.require_section(sid)['current_revision_id']==revision['id']
    image=client.get(f'/api/v1/images/{asset["sha256"]}')
    assert image.status_code==200 and image.headers['content-type']=='image/png'
    assert client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}').json()['node']['type']=='figure'
    downloaded=client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download?format=source').json()
    assert downloaded['render_engine']=='gpt-image-2'
    run=db.one('SELECT * FROM generation_runs WHERE id=?',(result['run_id'],))
    assert run['model']=='gpt-image-2' and run['protocol']=='images' and run['cost'] is None and run['reserved']==2
    assert IMAGE_KEY not in run['manifest'] and PRIMARY_KEY not in run['manifest']
    assert db.loads(run['manifest'])['provider_profile']=='image-generation'


def test_edit_api_multipart_uses_generated_reference_and_independent_key(client,configured):
    calls,_=configured;pid=project(client)
    first=image_generation.generate(enqueue(client,pid))['asset']
    task=enqueue(client,pid,source_artifact_id=first['id'],instruction='仅修改节点标签')
    second=image_generation.generate(task)['asset']
    assert len(calls)==2
    request=calls[-1]
    assert request.url.path=='/v1/images/edits' and request.headers['authorization']=='Bearer '+IMAGE_KEY
    assert request.headers['content-type'].startswith('multipart/form-data; boundary=')
    content=request.content
    assert b'name="image[]"' in content and b'filename="reference.png"' in content
    assert b'gpt-image-2' in content and b'response_format' not in content and b'input_fidelity' not in content
    assert second['id']!=first['id'] and second['metadata']['parent_artifact_id']==first['id']


def test_edit_manual_structure_uses_local_render_as_image_reference(client,configured):
    calls,_=configured;pid=project(client)
    image_generation.generate(enqueue(client,pid,current_spec=TEMPLATES['flowchart']))
    assert calls[0].url.path=='/v1/images/edits' and b'filename="reference.png"' in calls[0].content


@pytest.mark.parametrize('status,expected',[(401,'failed'),(403,'failed'),(429,'failed'),(500,'outcome_unknown'),(503,'outcome_unknown')])
def test_no_implicit_image_retry_or_text_fallback(client,configured,status,expected):
    calls,answers=configured;answers.append(httpx.Response(status,json={'error':'fixture rejection'}))
    pid=project(client);task=enqueue(client,pid)
    assert tasks.run_one()
    assert len(calls)==1 and db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']==expected
    assert all(r['model']=='gpt-image-2' for r in db.rows('SELECT model FROM generation_runs'))


def test_unknown_read_timeout_keeps_reservation_and_requires_retry_ack(client,configured):
    calls,answers=configured;answers.append(httpx.ReadTimeout('fixture response timeout'))
    pid=project(client);task=enqueue(client,pid);tasks.run_one()
    assert len(calls)==1
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='outcome_unknown'
    assert db.one('SELECT budget_charge FROM generation_budget_usage')['budget_charge']==2
    assert client.post(f'/api/v1/tasks/{task["id"]}/retry',json={}).status_code==422


@pytest.mark.parametrize('response',[{'data':[{'b64_json':'not-base64'}]}, {'data':[{'url':'https://example.invalid/image.png'}]}, {'model':'gpt-image-1.5','data':[{'b64_json':'bad'}]}])
def test_invalid_received_image_is_retained_as_potentially_charged(client,configured,response):
    calls,answers=configured;answers.append(response)
    pid=project(client);task=enqueue(client,pid);tasks.run_one()
    assert len(calls)==1 and not db.rows('SELECT * FROM artifacts')
    assert db.one('SELECT status FROM generation_runs')['status']=='completed_invalid'
    assert db.one('SELECT budget_charge FROM generation_budget_usage')['budget_charge']==2
    assert client.post(f'/api/v1/tasks/{task["id"]}/retry',json={}).status_code==422


def test_non_json_success_response_is_not_treated_as_free_rejection(client,configured):
    _,answers=configured;answers.append(httpx.Response(200,text='<html>invalid success</html>'))
    pid=project(client);enqueue(client,pid);tasks.run_one()
    assert db.one('SELECT status FROM generation_runs')['status']=='completed_invalid'
    assert db.one('SELECT budget_charge FROM generation_budget_usage')['budget_charge']==2


def test_image_budget_and_idempotency_before_network(client,configured):
    calls,_=configured;pid=project(client);client.patch(f'/api/v1/projects/{pid}',json={'budget':.5})
    body={'kind':'er','instruction':'根据实际数据库设计作图','request_id':'same-image-request'}
    one=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json=body).json()
    two=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json=body).json()
    assert one['id']==two['id']
    tasks.run_one()
    assert not calls and db.one('SELECT status FROM tasks WHERE id=?',(one['id'],))['status']=='budget_blocked'


def test_image_source_permission_is_rechecked_after_enqueue(client,configured):
    calls,_=configured;pid=project(client);sid=section(client,pid);f=file(client,pid)
    from app import documents
    doc=documents.text_document('有据的设计');doc['content'][0]['attrs']['sourceIds']=[db.one('SELECT id FROM sources WHERE file_id=?',(f['id'],))['id']]
    client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None})
    task=enqueue(client,pid,section_id=sid)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(f['id'],))
    tasks.run_one()
    assert not calls and db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='failed'


def test_native_image_project_boundary_and_fixed_fee(client,configured):
    client.put('/api/v1/image-generation/settings',json={'provider':{'image_price':.25}})
    pid=project(client);other=project(client,'其他项目')
    result=image_generation.generate(enqueue(client,pid));asset=result['asset']
    assert db.one('SELECT cost FROM generation_runs')['cost']==.25
    invalid=client.post(f'/api/v1/projects/{other}/paper-visuals/image-tasks',json={'kind':'flowchart','instruction':'编辑','source_artifact_id':asset['id']})
    assert invalid.status_code==404
    assert client.get(f'/api/v1/projects/{other}/paper-visuals/{asset["id"]}/download?format=png').status_code==404


def test_independent_image_billing_uses_the_image_credential(client,configured):
    calls,answers=configured;answers.append(httpx.Response(404,json={'error':'billing not supported in fixture'}))
    pid=project(client)
    response=client.post(f'/api/v1/projects/{pid}/tasks/billing-reconcile',json={'profile':'image-generation'})
    assert response.status_code==200 and not response.json()['reconciled']
    assert len(calls)==1 and calls[0].method=='GET'
    assert calls[0].url.host=='api.hanhegufei.online'
    assert calls[0].headers['authorization']=='Bearer '+IMAGE_KEY


def test_oversized_prompt_and_currency_mismatch_stop_before_paid_request(client,configured):
    calls,_=configured;pid=project(client);sid=section(client,pid);save(client,sid,'原始设计说明'*6000)
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json={'kind':'er','section_id':sid})
    assert response.status_code==422 and not calls and not db.rows('SELECT * FROM tasks')
    client.put('/api/v1/image-generation/settings',json={'provider':{'currency':'CNY'}})
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals/image-tasks',json={'kind':'er','instruction':'绘制实际设计'})
    assert response.status_code==422 and '币种' in response.text and not calls


def test_image_settings_reject_secret_in_endpoint_and_invalid_price(client,configured):
    for body in ({'base_url':'https://user:secret@example.com/v1'}, {'base_url':'https://example.com/v1?key=secret'}, {'image_price':-1}, {'quality':[]}, {'timeout_seconds':1}):
        result=client.put('/api/v1/image-generation/settings',json={'provider':body})
        assert result.status_code==422,result.text
    assert security.get_key('image-generation')==IMAGE_KEY
