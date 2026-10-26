import json
import pytest
from fastapi import HTTPException
from test_writing_agent import env,start
from test_workbench import file
from app import db,document_reading as reader,model_install
from app.writing_agent import tools,evidence,coordinator


@pytest.fixture(autouse=True)
def local_models(monkeypatch):
    old=model_install.status
    monkeypatch.setattr(model_install,'status',lambda name:{**old(name),'ready':False})


def flow(env,role='research'):
    original=file(env['client'],env['pid'],'研究问题是验证范围。\n\n方法为案例分析。\n\n条件限于单用户。\n\n结果仅适用于模拟样例。\n\n局限是不包含并发验证。',role=role)
    session=start(env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(session['id'],))
    def call(name,args,cid):return tools.execute(env['pid'],step['id'],name,args,cid,env['snapshot']['id'])['data']
    return original,call


def test_document_tools_cache_versions_and_current_observation(env):
    original,call=flow(env)
    opened=call('open_literature_document',{'file_id':original['id'],'question':'哪些方法与限制支持本段？'},'open')
    assert call('open_literature_document',{'file_id':original['id'],'question':'哪些方法与限制支持本段？'},'open')==opened
    rid=opened['reading_id'];directory=call('read_document_outline',{'reading_id':rid},'directory')
    assert directory['total']==5 and not reader.context(env['pid'],rid)['passages']
    first=call('read_document_range',{'reading_id':rid,'ordinal_start':1,'ordinal_end':1},'read-first')
    assert call('read_document_range',{'reading_id':rid,'ordinal_start':1,'ordinal_end':1},'read-first')==first
    note={'topic':'research_question','quote':first['passages'][0]['text'],'passage_ids':[first['passages'][0]['passage_id']],'note':'仅限本次问题的候选记录'}
    args={'reading_id':rid,'findings_json':json.dumps([note],ensure_ascii=False)}
    found=call('record_document_findings',args,'note-first')
    assert call('record_document_findings',args,'note-first')==found
    old=evidence.get(env['pid'],found['evidence_id'])
    last=call('read_document_range',{'reading_id':rid,'ordinal_start':5,'ordinal_end':5},'read-last')
    assert not evidence.get(env['pid'],old['id'])['version_current']
    current_ids=coordinator.current_investigation_evidence(env['pid'],[old['id'],old['id']])
    assert len(current_ids)==1 and current_ids[0]!=old['id']
    current=evidence.get(env['pid'],current_ids[0]);assert current['version_current'] and len(current['payload']['passages'])==2
    assert db.one('SELECT count(*) AS n FROM tool_observations')['n']==5
    env['client'].patch('/api/v1/files/'+original['id'],json={'egress':'local_only'})
    with pytest.raises(HTTPException):call('read_document_range',{'reading_id':rid,'ordinal_start':5,'ordinal_end':5},'read-last')


def test_guidance_tools_cannot_mint_factual_evidence_or_human_review(env):
    original,call=flow(env,'rules')
    opened=call('open_literature_document',{'file_id':original['id'],'question':'资料的教学目的是什么？','intent':'guidance'},'open-guide')
    rid=opened['reading_id'];read=call('read_document_range',{'reading_id':rid,'ordinal_start':1,'ordinal_end':1},'read-guide')
    note={'topic':'purpose','quote':read['passages'][0]['text'],'passage_ids':[read['passages'][0]['passage_id']],'note':'候选用途，不能证明项目成果','method':'human','reviewer':'不得采信模型填的人名'}
    found=call('record_document_findings',{'reading_id':rid,'findings_json':json.dumps([note],ensure_ascii=False)},'guide-note')
    assert 'guidance_evidence_id' in found and 'evidence_id' not in found
    assert reader.context(env['pid'],rid)['findings'][0]['method']=='candidate'
    observed=call('get_evidence',{'evidence_id':found['guidance_evidence_id']},'get-guide')
    assert 'evidence_id' not in observed and observed['guidance_evidence_id']==found['guidance_evidence_id']
    assert coordinator.current_investigation_evidence(env['pid'],[found['guidance_evidence_id']])==[]


def test_oversized_tool_read_is_not_saved_as_actually_read(env):
    original=file(env['client'],env['pid'],'条件不允许省略。'*1500)
    session=start(env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(session['id'],))
    opened=tools.execute(env['pid'],step['id'],'open_literature_document',{'file_id':original['id'],'question':'完整条件是什么？'},'open-large',env['snapshot']['id'])['data']
    result=tools.execute(env['pid'],step['id'],'read_document_range',{'reading_id':opened['reading_id'],'ordinal_start':1,'ordinal_end':1,'max_chars':20000},'read-large',env['snapshot']['id'])['data']
    assert result['status']=='needs_narrower_range' and result['passages']==[]
    assert not reader.context(env['pid'],opened['reading_id'])['passages']


def test_budget_packing_handles_document_and_unit_origins_without_clipping():
    from app.writing_agent.packing import fit,estimate
    payload={'section':{'id':'chapter','title':'相关研究'},'contract':{'goal':'研究条件'},'rules':[],
        'evidence':[{'id':'reading-evidence','kind':'literature','conditions':[],'payload':{'reading_id':'reading','question':'方法与局限','passages':[{'text':'方法条件与局限。'*6000}]}}],
        'references':[{'id':'ref','source_ids':[],'document_reading_ids':['reading']}],
        'project_inputs':{'evidence_ids':['reading-evidence'],'reference_ids':['ref'],'material_evidence_ids':['reading-evidence'],'rule_ids':[]}}
    manifest={'stage':'investigate','source_evidence':{'reading-evidence':{'reading_id':'reading','file_id':'file','title':'研究文献'}},'evidence_versions':{'reading-evidence':'version'}}
    cfg={'context_limit':32000,'max_output_tokens':2048}
    fit(payload,manifest,'研究上下文',cfg)
    assert payload['evidence']==[] and payload['references']==[]
    assert payload['available_sources'][0]['reading_id']=='reading'
    assert manifest['source_selection']['no_original_text_clipped']
    assert estimate(payload,manifest,'研究上下文',cfg)+2048<=32000
