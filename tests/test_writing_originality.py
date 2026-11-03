"""Local overlap recall must expose scope and never certify plagiarism/AIGC."""
import pytest

from app import db,model_install,retrieval
from app import writing_originality as originality
from test_workbench import client,project  # noqa: F401

PASSAGE='本系统将授权判断与数据写入分别交由不同模块负责，模块间只通过明确接口通信，并保留独立的输入检查和错误处理过程。'


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    monkeypatch.setattr(model_install,'status',lambda name:{'ready':False,'fingerprint':'not-installed'})
    monkeypatch.setattr(model_install,'download',lambda *a,**kw:pytest.fail('原创性检查不得自动下载模型'))


def test_exact_within_section_overlap_has_locations_and_no_score_claim():
    result=originality.check([{'text':PASSAGE},{'text':'😀'+PASSAGE}])
    assert any(f['scope']=='within_section' for f in result['findings'])
    match=result['matches'][0]
    assert match['paragraph_index']==1 and match['start_cp']==1
    assert match['matched_text']==PASSAGE.rstrip('。')
    assert result['coverage']['semantic']['reason']=='local_model_not_installed_no_download'
    assert result['coverage']['external_plagiarism_rate']=='not_measured'
    assert result['coverage']['aigc_rate']=='not_measured'


def test_near_literal_copy_is_recalled_without_claiming_semantic_redundancy():
    changed=PASSAGE.replace('明确接口','稳定接口').replace('错误处理','异常处理')
    result=originality.check([{'text':changed}],related=[{'section_id':'earlier','revision_id':'v1','title':'需求分析','text':PASSAGE}])
    assert result['matches'] and result['matches'][0]['scope']=='cross_section'
    assert result['matches'][0]['target_version']=='v1'
    assert result['matches'][0]['redundancy']=='unassessed'


def test_source_overlap_is_separate_from_cross_section_and_quotes_are_visible():
    candidate={'text':PASSAGE,'citation_intents':[{'claim_id':'c1','reference_id':'ref','kind':'quote','quote':PASSAGE}]}
    result=originality.check([candidate],sources=[{'id':'source','version':'frozen','text':PASSAGE}])
    assert result['matches'][0]['scope']=='source'
    assert result['matches'][0]['exemption']['kind']=='direct_quote'
    assert result['matches'][0]['exemption']['excluded_from_redundancy']
    assert not result['findings']
    # An ordinary citation flag does not exempt the entire copied passage.
    result=originality.check([{'text':PASSAGE,'reference_ids':['ref']}],sources=[{'id':'source','text':PASSAGE}])
    assert result['findings'][0]['kind']=='source_expression_overlap'


def test_conclusion_recap_is_visible_and_requires_review():
    result=originality.check([{'text':PASSAGE}],related=[{'section_id':'method','text':PASSAGE}],section_kind='conclusion')
    match=result['matches'][0]
    assert match['exemption']['kind']=='necessary_recap'
    assert not match['exemption']['excluded_from_redundancy']
    assert result['findings'][0]['severity']=='info'


def test_registered_term_is_marked_without_hiding_original_match(client):
    pid=project(client)
    term='面向教学场景的可验证学生成长记录'
    db.execute('INSERT INTO terms VALUES(?,?,?,?)',(db.uid(),pid,term,'Verifiable Learning Record'))
    db.set_setting('writing_originality:'+pid,{'min_characters':5,'semantic_enabled':False})
    result=originality.check([{'text':term}],sources=[{'id':'source','text':term}],pid=pid)
    assert result['matches'][0]['exemption']['kind']=='necessary_terminology'
    assert not result['findings']


def test_semantic_model_only_recalls_candidates_and_stays_local(monkeypatch):
    monkeypatch.setattr(model_install,'status',lambda name:{'ready':True,'fingerprint':'verified-local-model'})
    seen=[]
    def fake_embed(texts,name):
        seen.extend(texts)
        return [[1.0,0.02] for _ in texts]
    monkeypatch.setattr(retrieval,'embed',fake_embed)
    other='为了避免独立组件之间产生紧密依赖，平台通过公共协议传递消息，各组件自行验证收到的请求并统一报告失败信息。'
    result=originality.check([{'text':PASSAGE}],related=[{'section_id':'earlier','text':other}])
    assert seen and result['coverage']['semantic']['state']=='completed'
    match=next(m for m in result['matches'] if m['method']=='local_embedding_candidate')
    assert match['redundancy']=='requires_information_gain_review'
    assert result['findings'][0]['severity']=='info'


def test_local_model_failure_falls_back_and_preserves_literal_results(monkeypatch):
    monkeypatch.setattr(model_install,'status',lambda name:{'ready':True,'fingerprint':'model-v1'})
    monkeypatch.setattr(retrieval,'embed',lambda *a:(_ for _ in ()).throw(ValueError('invalid vector')))
    result=originality.check([{'text':PASSAGE}],sources=[{'id':'source','text':PASSAGE}])
    assert result['matches'] and result['coverage']['literal_complete']
    assert result['coverage']['semantic']['state']=='unavailable'


def test_comparison_limits_report_incomplete_scope(client):
    pid=project(client)
    db.set_setting('writing_originality:'+pid,{'max_targets':1,'max_pairs':1,'semantic_enabled':False})
    result=originality.check([{'text':PASSAGE},{'text':PASSAGE}],sources=[{'id':'a','text':PASSAGE},{'id':'b','text':PASSAGE}],pid=pid)
    assert not result['coverage']['literal_complete']
    assert result['coverage']['omitted_targets']==1
    assert result['coverage']['pair_limit_reached']


def test_unrelated_and_short_standard_terms_do_not_trigger_copy_findings():
    other='本文采用半结构化访谈收集学习者对课程反馈的看法，并对访谈记录进行主题编码，所有结论限于本次访谈样本。'
    result=originality.check([{'text':other},{'text':'灵魂绑定代币'}],related=[{'section_id':'a','text':PASSAGE}])
    assert not result['findings']
    assert result['coverage']['short_paragraphs_skipped']==[1]
    assert result['hash']==originality.check([{'text':other},{'text':'灵魂绑定代币'}],related=[{'section_id':'a','text':PASSAGE}])['hash']
