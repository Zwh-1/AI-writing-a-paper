from pathlib import Path
import json
import runpy
import zipfile
import hashlib

from test_workbench import client,project,file
from app import db,provider,knowledge_units
from app import knowledge_comparisons


def test_handoff_never_calls_model_labels_cases_or_modifies_projects(client,monkeypatch,tmp_path):
    pid=project(client);source=file(client,pid,'研究仅验证单用户环境；并发情况未测试。'*12)
    knowledge_units.build(pid,source['id']);knowledge_comparisons.seed(pid)
    monkeypatch.setattr(provider,'generate',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('No generation in a handoff export')))
    module=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/prepare_knowledge_acceptance_handoff.py'))
    output=tmp_path/'handoff';summary=module['generate'](pid,output,{'paper':'not_found_user_unsure','reviewers':'none','bill_files':'none'})
    assert summary['integrity']=={'unchanged':True,'new_model_requests':0}
    assert summary['human_annotation_drafts']==80 and summary['real_human_labels']==0 and summary['real_human_review_records']==0
    assert not summary['all_four_accepted'] and not summary['provider_contacted']
    assert summary['real_paper_prerequisites']['nonempty_current_revisions']==0
    requests=json.loads((output/'provider/request-evidence.json').read_text(encoding='utf-8'))
    assert requests['not_an_invoice'] and requests['not_sent_to_provider']
    reviewer=json.loads((output/'independent-reviewer/blind-review-readiness.json').read_text(encoding='utf-8'))
    assert not reviewer['gold_included'] and not reviewer['variant_mapping_included'] and not reviewer['group_answers']
    with zipfile.ZipFile(output/'独立盲审交接包_待评审.zip') as archive:
        assert all('annotator' not in name and 'provider' not in name for name in archive.namelist())
    manifest=json.loads((output/'MANIFEST.sha256.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256((output/name).read_bytes()).hexdigest()==digest for name,digest in manifest.items())
    assert not db.rows('SELECT * FROM generation_runs')


def test_provider_packet_does_not_invent_sent_or_provider_request_ids(client,tmp_path):
    from app import tasks
    pid=project(client);task=tasks.enqueue(pid,'synthetic-billing-fixture',{})
    run_id=db.uid();db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
        (run_id,task['id'],pid,'outcome_unknown','fixture-model','openai',db.dumps({'provider':{'base_url':'https://provider.example','currency':'USD'}}),.02,db.now()))
    module=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/prepare_knowledge_acceptance_handoff.py'))
    records=module['provider_requests'](pid)
    assert len(records)==1 and records[0]['id']==run_id
    assert records[0]['client_request_id'] is None and records[0]['provider_request_id'] is None
    assert records[0]['actual_charge'] is None and not records[0]['has_saved_matching_request_id']
    assert records[0]['local_run_id_is_not_provider_id']
