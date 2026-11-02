"""Regression coverage for the writing failures shown in the task center."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_workbench import client, project, manual_package, section
from app import db, provider, tasks, security, summary_billing as billing
from app import knowledge_service
from app.writing_request import pack


def readings(text='原文的适用条件及否定必须保留。', selected=False, explicit=False):
    unit={'id':'unit','unit_id':'unit','file_id':'literature','text':text,
          'unit_hash':'version','parse_id':'parse','scope':'full_parsed_document_text',
          'locator':{'page':1,'recognition':[{'text':'仅供本地核验'}]},
          'passages':[{'id':'unit','text':text,'locator':{'page':1}}],
          'material_use':{'can_prove_project_execution':False},'issues':[]}
    if explicit:unit['reading_id']='reading'
    return {'sources':[{'id':'source','file_id':'literature','text':text,'locator':'{}'}],
            'knowledge_units':[unit], 'gaps':[],
            'selected_unit_ids':['unit'] if selected else [],
            'document_readings':[], 'knowledge_refs':[]}


def request(knowledge):
    return {'rules':'[mandatory] 必须保留限定与否定。',
            'related_sections':[{'section_id':'previous','text':'所有已保存正文。中部唯一标记。结尾。'}],
            'paper_context':{'complete':True},'knowledge_context':{},
            'knowledge_units':[knowledge_service.for_model(u) for u in knowledge['knowledge_units']],
            'sources':[{'id':s['id'],'file_id':s['file_id'],'text':s['text']} for s in knowledge['sources']],
            'references':[{'id':'book','file_id':'literature'}],
            'evidence_gaps':knowledge['gaps'],'writing_examples':[]}


def test_identical_source_text_is_sent_once_and_full_unit_is_preserved():
    knowledge=readings('完整文献原文及否定条件。'*100)
    original=deepcopy(knowledge);context=request(knowledge)
    report=pack(context,knowledge,'写作',{'context_limit':20000,'max_output_tokens':1000})
    assert context['knowledge_units'][0]['text']==original['knowledge_units'][0]['text']
    assert context['sources'][0]['text_in_knowledge_units']==['unit']
    assert 'text' not in context['sources'][0]
    assert 'recognition' not in db.dumps(context)
    assert report['loaded_unit_ids']==['unit'] and not report['deferred_documents']
    assert knowledge==original


def test_large_derived_navigation_is_deferred_before_actual_automatic_reading():
    knowledge=readings('原文方法、适用条件与局限全部保留。',explicit=True)
    knowledge['automatic_reading_ids']=['reading']
    knowledge['document_readings']=[{'reading_id':'reading','file_id':'literature','passages':[{'text':knowledge['knowledge_units'][0]['text']}]}]
    context=request(knowledge);context['document_readings']=[{'reading_id':'reading'}]
    context['knowledge_context']={'wiki':'派生导航内容。'*3000}
    report=pack(context,knowledge,'写作',{'context_limit':8000,'max_output_tokens':1000})
    assert context['knowledge_units'][0]['text']=='原文方法、适用条件与局限全部保留。'
    assert context['document_readings']==[{'reading_id':'reading'}]
    assert context['knowledge_context']=={}
    assert not any(item['kind']=='automatic_reading_deferred' for item in report['deferred_documents'])


def test_large_automatic_document_is_deferred_whole_with_exact_coverage():
    knowledge=readings('整篇文献不可截断。'*4000)
    knowledge['gaps']=[{'file_id':'literature','reason':'已提供该文献的全部解析文本，仍需核对条件'}]
    context=request(knowledge);original=deepcopy(context['related_sections'])
    report=pack(context,knowledge,'写作',{'context_limit':8000,'max_output_tokens':1000})
    assert context['rules']=='[mandatory] 必须保留限定与否定。'
    assert context['related_sections']==original and report['manuscript_complete']
    assert context['knowledge_units']==knowledge['knowledge_units']==[]
    assert context['sources']==knowledge['sources']==[] and not context['references']
    assert report['deferred_documents'][0]['characters']==len('整篇文献不可截断。'*4000)
    assert report['input_upper_bound']+report['reserved_output']<=report['context_limit']
    assert not report['loaded_unit_ids'] and not report['loaded_source_ids']
    assert '已提供该文献' not in db.dumps(context['evidence_gaps'])
    assert context['evidence_gaps'][0]['kind']=='automatic_document_deferred'


@pytest.mark.parametrize('selected,explicit',[(True,False),(False,True)])
def test_selected_or_explicit_reading_cannot_be_silently_removed(selected,explicit):
    knowledge=readings('已选证据完整条件。'*4000,selected,explicit)
    context=request(knowledge);original=context['knowledge_units'][0]['text']
    with pytest.raises(HTTPException) as error:
        pack(context,knowledge,'写作',{'context_limit':8000,'max_output_tokens':1000})
    assert error.value.detail['code']=='WRITING_CONTEXT_BUDGET_EXCEEDED'
    assert '已选定或已阅读的证据' in error.value.detail['message']
    assert context['knowledge_units'][0]['text']==original


def no_readings(monkeypatch):
    monkeypatch.setattr(knowledge_service,'prepare_writing',lambda *a,**kw:{
        'sources':[],'knowledge_units':[],'gaps':[],'selected_unit_ids':[],
        'document_candidates':[],'document_readings':[]})


def fake_response(monkeypatch):
    from app import expense_evidence
    monkeypatch.setattr(expense_evidence,'preflight',lambda *a,**k:None)
    captured=[]
    def generate(system,user,images=None):
        captured.append({'payload':db.loads(user),'cfg':provider.config()})
        return {'text':db.dumps({'paragraphs':[{'text':'【待补证据】研究成果需提供项目事实。'}]}),
                'usage':{},'cost':0.001,'remote_id':'fixture','request_hash':'fixture'}
    monkeypatch.setattr(provider,'generate',generate)
    return captured


def write_task(pid,sid,**kwargs):
    return tasks.enqueue(pid,'write',{'section_id':sid,'operation':'generate','base_revision_id':None,**kwargs})


def test_gpt_retry_prepares_with_gpt_limits_instead_of_primary(client,monkeypatch):
    pid=project(client);manual_package(client,pid);sid=section(client,pid)
    no_readings(monkeypatch);captured=fake_response(monkeypatch)
    db.set_setting('provider',{**provider.config(),'context_limit':1000,'max_output_tokens':512})
    db.set_setting('gpt_fallback_provider',{'model':'gpt-fixture','context_limit':40000,'max_output_tokens':2048})
    monkeypatch.setattr(security,'get_key',lambda profile='':'fixture-key')
    task=write_task(pid,sid,provider_profile='gpt');assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='succeeded',saved['error']
    assert captured[0]['cfg']['model']=='gpt-fixture'
    assert db.loads(saved['result'])['manifest']['context_budget']['context_limit']==40000


def test_writing_uses_verified_prices_without_changing_budget_or_releasing_unknowns(client,monkeypatch):
    pid=project(client);manual_package(client,pid);sid=section(client,pid)
    no_readings(monkeypatch);fake_response(monkeypatch)
    cfg={**provider.config(),'model':'fixture','context_limit':32000,'max_output_tokens':8192,
         'input_price':None,'output_price':None,'max_request_cost':2}
    db.set_setting('provider',cfg)
    monkeypatch.setattr(provider,'get_key',lambda:'pricing-key')
    db.set_setting('ai_autopilot_reference_pricing',{
        'verified':True,'model':cfg['model'],'base_url':cfg['base_url'].rstrip('/').removesuffix('/v1'),
        'currency':cfg['currency'],'credential_hash':billing.key_hash('pricing-key'),
        'input_price':1.,'output_price':4.})
    db.execute('UPDATE projects SET budget=25 WHERE id=?',(pid,))
    prior=tasks.enqueue(pid,'provider_probe',{})
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
               ('unknown',prior['id'],pid,'outcome_unknown','fixture','openai','{}',24.56,db.now()))
    db.execute("UPDATE tasks SET status='outcome_unknown' WHERE id=?",(prior['id'],))
    task=write_task(pid,sid);assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='succeeded',saved['error']
    run=db.one('SELECT * FROM generation_runs WHERE task_id=?',(task['id'],))
    assert 0<run['reserved']<0.44
    assert db.loads(run['manifest'])['cost_evidence']['prices_known']
    assert db.one('SELECT reserved,status FROM generation_runs WHERE id=?',('unknown',))=={'reserved':24.56,'status':'outcome_unknown'}
    assert billing.budget(pid)['limit']==25 and provider.config()['input_price'] is None


@pytest.mark.parametrize('change',[{'model':'different'},{'currency':'CNY'},{'credential_hash':'different'},{'base_url':'https://other.example'}])
def test_reference_prices_require_matching_account_endpoint_model_and_currency(client,change):
    cfg=provider.config()
    evidence={'verified':True,'model':cfg['model'],'currency':cfg['currency'],
              'base_url':cfg['base_url'].rstrip('/').removesuffix('/v1'),
              'credential_hash':billing.key_hash('key'),'input_price':1.,'output_price':4.,**change}
    db.set_setting('ai_autopilot_reference_pricing',evidence)
    assert billing.priced_config(cfg,'key')==cfg


def test_writing_budget_failures_have_recoverable_status(client,monkeypatch):
    pid=project(client);manual_package(client,pid);sid=section(client,pid)
    no_readings(monkeypatch)
    db.execute('UPDATE projects SET budget=0 WHERE id=?',(pid,))
    task=write_task(pid,sid);assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='budget_blocked' and '项目预算不足' in saved['error']
    assert not db.rows('SELECT * FROM generation_runs WHERE task_id=?',(task['id'],))


def test_required_context_overflow_saves_diagnostics_before_any_request(client,monkeypatch):
    pid=project(client);manual_package(client,pid);sid=section(client,pid)
    no_readings(monkeypatch)
    db.set_setting('provider',{**provider.config(),'context_limit':1024,'max_output_tokens':512})
    task=write_task(pid,sid);assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='failed' and 'WRITING_CONTEXT_BUDGET_EXCEEDED' in saved['error']
    report=db.loads(saved['result'])['context_budget']
    assert report['input_upper_bound']+report['reserved_output']>report['context_limit']
    assert report['component_bytes']['rules']>0
    assert not db.rows('SELECT * FROM generation_runs WHERE task_id=?',(task['id'],))


def test_configured_prices_take_precedence_over_reference_prices(client):
    cfg={**provider.config(),'input_price':0.1,'output_price':0.2}
    db.set_setting('ai_autopilot_reference_pricing',{'verified':True,'input_price':1.,'output_price':4.})
    assert billing.priced_config(cfg,'key')==cfg


def test_invalid_section_replies_are_not_marked_succeeded_or_salvaged(client,monkeypatch):
    pid=project(client);manual_package(client,pid);sid=section(client,pid)
    no_readings(monkeypatch);fake_response(monkeypatch)
    db.set_setting('provider',{**provider.config(),'model':'fixture','context_limit':32000,
                              'max_output_tokens':2048,'input_price':1.,'output_price':4.})
    monkeypatch.setattr(provider,'get_key',lambda:'fixture-key')
    replies=[]
    def prose_only(system,user,images=None):
        replies.append(True)
        return {'text':'模型返回的是纯文字说明，不是章节JSON。',
                'usage':{'input_tokens':1,'output_tokens':1},'cost':0.001,
                'remote_id':'fixture','request_hash':'fixture'}
    monkeypatch.setattr(provider,'generate',prose_only)
    task=write_task(pid,sid);assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    result=db.loads(saved['result'],{})
    assert saved['status']=='failed'
    assert len(replies)==tasks.CANDIDATE_FORMAT_RETRIES+1
    assert result['document'] is None and result['raw_text']=='模型返回的是纯文字说明，不是章节JSON。'
    assert len(result['validation_attempts'])==tasks.CANDIDATE_FORMAT_RETRIES+1
    assert all(not attempt['valid'] and attempt['error'] for attempt in result['validation_attempts'])
    assert db.one('SELECT COUNT(*) n FROM generation_runs WHERE task_id=?',(task['id'],))['n']==len(replies)
    assert not db.rows('SELECT * FROM revisions WHERE section_id=?',(sid,))
