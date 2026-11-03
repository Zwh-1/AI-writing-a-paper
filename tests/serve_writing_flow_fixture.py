"""Disposable UI acceptance server. All model replies are synthetic and local."""
import json
import os
import runpy
import socket
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get('PAPER_DATA_DIR') or not os.environ.get('PAPER_SESSION_DIR'):
    raise RuntimeError('Explicit disposable PAPER_DATA_DIR and PAPER_SESSION_DIR are required')
if Path(os.environ['PAPER_DATA_DIR']).resolve() == (ROOT / 'data').resolve():
    raise RuntimeError('This fixture must never use the real project data directory')
if not os.environ.get('PAPER_PORT'):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0));os.environ['PAPER_PORT'] = str(listener.getsockname()[1])
runpy.run_path(str(ROOT / 'runner.py'), run_name='runtime_setup')

from app import db, config, security, documents, parsers, knowledge_units, tasks, provider, model_install
from app import rules, reference_service, research_records
from app.main import create_project, add_section, add_rule

db.init()
project = create_project({'title': '写作核心验收 · 合成论文', 'budget': 50,
                          'brief': {'question': '如何保留合成案例的适用条件？', 'method': '仅使用合成材料验证界面和数据流'}})
pid = project['id']
sections = [add_section(pid, {'title': title, 'kind': kind, 'target_words': 800,
                             'goal': '解释合成案例的研究问题、依据和适用条件；不能声称为真实运行或性能结果。'})
            for title, kind in [('第一章 合成案例方法', 'methods'), ('第二章 讨论与局限', 'discussion')]]
sid = sections[0]['id']
add_rule(pid, {'statement': '必须保留合成材料的适用条件，不得虚构实际运行结果。', 'modality': 'mandatory'})
rules.publish(pid)
original, _ = parsers.register_file(pid, '合成案例论文.txt', '研究问题是如何说明合成案例的适用范围。\n\n合成案例包含十二条记录，只用于界面验收，不代表实际运行或性能结果。\n\n研究局限是不能将合成记录推广到真实运行环境。'.encode(), 'research')
parsers.parse_file(original['id']);knowledge_units.build(pid, original['id'])
book = reference_service.upsert_external(pid, {'title': '合成案例范围研究', 'authors': ['验收作者'], 'year': '2026'}, original['id'])
guidance, _ = parsers.register_file(pid, '合成案例写作指导.txt', '写作指导：先说明研究问题，再说明论据。\n\n错误示例：合成案例证明真实系统性能良好。\n\n正确示例：合成案例仅用于界面验收。\n\n本示例不证明任何实际项目成果。'.encode(), 'example')
parsers.parse_file(guidance['id'])
research_records.save(pid, 'note', {'title': '合成案例补证笔记', 'text': '合成记录必须保留适用范围，不能当作实测性能结果。'})
db.set_setting('provider', {**provider.config(), 'model': 'synthetic-writing-only', 'base_url': 'http://127.0.0.1:1/v1',
                           'context_limit': 100000, 'max_output_tokens': 4096, 'streaming': False,
                           'input_price': 1., 'output_price': 1., 'max_attempts': 1})
db.set_setting('expense_policy:' + pid, {'require_pricing_basis': False, 'block_on_unresolved': False})
security.get_key = lambda profile='': 'synthetic-writing-fixture-key'
provider.get_key = lambda: 'synthetic-writing-fixture-key'
model_install.status = lambda name: {'ready': False}

output = Path(os.environ['PAPER_SESSION_DIR']);output.mkdir(parents=True, exist_ok=True)
calls = []


def record(kind, value):
    calls.append({'kind': kind, **value})
    (output / 'calls.json').write_text(db.dumps(calls), encoding='utf-8')


def result(text):
    return {'text': text, 'usage': {'input_tokens': 0, 'output_tokens': 0}, 'cost': .0001,
            'remote_id': 'synthetic-' + str(len(calls)), 'request_hash': 'synthetic-local-only'}


def fake_transport(system, user, cfg, images=None):
    frame = db.loads(user, {});messages = frame.get('messages', [])
    observed = []
    for message in messages:
        parsed = db.loads(message.get('content'), {})
        if isinstance(parsed, dict) and parsed.get('tool_result'):observed.append(parsed['tool_result'])
    if not observed:
        action = {'action': 'tool', 'name': 'search_writing_materials', 'arguments': {'query': '合成案例', 'limit': 4}}
    elif not any(item.get('data', {}).get('schema') == 'writing.evidence_bundle.v1' for item in observed):
        hits = next((item.get('data', {}).get('items', []) for item in reversed(observed) if item.get('data', {}).get('items')), [])
        action = {'action': 'tool', 'name': 'read_writing_materials',
                  'arguments': {'query': '合成案例的适用范围', 'candidates_json': db.dumps(hits[:2]), 'max_chars': 6000}}
    else:action = {'action': 'done', 'summary': '已实际读取合成案例与指导材料，保留适用范围。'}
    record('tool_model', {'action': action.get('name') or action['action']})
    return result(db.dumps({'agent_transport': True, 'text': db.dumps(action), 'tool_calls': [],
                            'message': None, 'items': [], 'finish': 'stop', 'reported_model': cfg['model']}))


def generate(system, user, images=None):
    if provider.config().get('agent_transport'):return fake_transport(system, user, provider.config(), images)
    payload = db.loads(user, {})
    if 'actual_sources' in payload:
        source = payload['actual_sources'][0]
        value = {'claim_reviews': [{'claim_id': claim['claim_id'], 'support': 'supported',
                                   'source_quotes': [{'source_id': source['id'], 'quote': source['text']}],
                                   'reason': '合成验收回复：保留来源范围。'} for claim in payload.get('claims', [])],
                 'point_reviews': [{'point_id': point['id'], 'status': 'complete', 'paragraph_indices': [0],
                                    'evidence_text': payload['candidate_paragraphs'][0]['text'],
                                    'reason': '合成验收回复'} for point in payload.get('task_points', [])],
                 'findings': [], 'summary': '本回复仅验证界面和数据流。'}
        record('semantic_model', {'claims': len(payload.get('claims', []))})
    elif payload.get('candidate_to_repair'):
        value = {**payload['candidate_to_repair'], 'retrieval_requests': []}
        record('repair_model', {'iteration': payload.get('repair_iteration')})
    elif 'source_abstract' in payload:
        value = {'paragraphs': [{'text': 'Synthetic records illustrate the local acceptance flow under the specified conditions.'}]}
        record('translation_model', {})
    else:
        source = next((row for row in payload.get('knowledge_units', []) if '十二' in row.get('text', '')), next(iter(payload.get('knowledge_units', [])), {}))
        source_id = next((row['id'] for row in payload.get('sources', [])), None)
        uid = source.get('unit_id') or source.get('id')
        text = '合成案例中的十二条记录用于演示写作流程，适用范围仅限本次界面验收。'
        paragraph = {'text': text, 'brief_points': [point['id'] for point in payload.get('writing_brief', {}).get('points', [])],
                     'source_ids': [source_id] if source_id else [], 'knowledge_unit_ids': [uid] if uid else [],
                     'reference_ids': [book['id']],
                     'claim_spans': [{'local_id': 'synthetic-claim', 'text': text, 'role': 'fact',
                                      'source_ids': [source_id] if source_id else [], 'knowledge_unit_ids': [uid] if uid else []}],
                     'citation_intents': [{'claim_id': 'synthetic-claim', 'reference_id': book['id'],
                                           'source_ids': [source_id] if source_id else [], 'knowledge_unit_ids': [uid] if uid else []}]}
        value = {'paragraphs': [paragraph], 'missing_materials': [],
                 'retrieval_requests': [{'query': '合成案例适用范围与研究局限'}]}
        record('draft_model', {'section_id': payload.get('section', {}).get('id'), 'sources': len(payload.get('sources', [])),
                                'knowledge_units': len(payload.get('knowledge_units', [])), 'references': len(payload.get('references', []))})
    return result(db.dumps(value))


provider.generate = generate
from app.writing_agent import provider_adapter
provider_adapter.transport = fake_transport


def deny_network(*args, **kwargs):
    raise AssertionError('The writing UI fixture forbids external provider requests')


provider.client = deny_network
(output / 'fixture.json').write_text(db.dumps({'project_id': pid, 'section_id': sid,
    'file_id': original['id'], 'reference_id': book['id'], 'port': config.PORT,
    'url': f'http://127.0.0.1:{config.PORT}/#token={security.BOOTSTRAP_TOKEN}'}), encoding='utf-8')
threading.Thread(target=tasks.run_forever, daemon=True).start()
import uvicorn
uvicorn.run('app.main:app', host='127.0.0.1', port=config.PORT, log_level='warning', access_log=False)
