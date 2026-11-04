"""Delivery audit regressions use only synthetic files and a temporary DB."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import zipfile

import pytest


@pytest.fixture
def audited_delivery(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'isolated_final_delivery_audit', root / '.runtime/diagnostics/audit_final_delivery.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    isolated_root = tmp_path / 'app'
    (isolated_root / 'data').mkdir(parents=True)
    monkeypatch.setattr(module, 'ROOT', isolated_root)
    body = 'This synthetic paragraph preserves its original wording and ordinary references [1] 【待补证据】.'
    sections = [{'id': 's1', 'title': '第一章 Introduction'}]
    candidate = {'complete': True, 'paper_hash': 'synthetic-hash',
                 'paper_sections': [{'section_id': 's1', 'text': body}]}
    with sqlite3.connect(isolated_root / 'data/workbench.sqlite3') as connection:
        connection.execute('CREATE TABLE tasks(id TEXT,status TEXT,payload TEXT,result TEXT)')
        connection.execute('INSERT INTO tasks VALUES(?,?,?,?)', ('t1', 'succeeded',
            json.dumps({'snapshot': {'sections': sections}}), json.dumps(candidate)))
    directory = tmp_path / 'delivery'
    directory.mkdir()
    report = {'candidate_ready': True, 'task_id': 't1', 'paper_hash': 'synthetic-hash'}
    (directory / '候选验收.json').write_text(json.dumps(report), encoding='utf-8')
    (directory / '审阅说明.md').write_text('Synthetic candidate; human review pending.', encoding='utf-8')
    (directory / '检查报告.json').write_text(json.dumps({
        'pdf_status': 'generated', 'docx_property_checks': {'fail': 0}}), encoding='utf-8')
    for name in ('论文.docx', '论文.pdf', '版本清单.json', '句子—论断—证据映射.json'):
        (directory / name).write_bytes(('synthetic ' + name).encode('utf-8'))
    texts = {'docx': 'Introduction\n' + body, 'pdf': 'Introduction\n' + body}
    monkeypatch.setattr(module, 'docx_text', lambda path: (
        SimpleNamespace(paragraphs=[1], tables=[], sections=[1]), texts['docx']))

    class TextPage:
        def get_text_range(self): return texts['pdf']
        def close(self): pass

    class Page:
        def get_size(self): return (595, 842)
        def get_textpage(self): return TextPage()
        def close(self): pass

    class PDF:
        def __init__(self, *args): pass
        def __len__(self): return 1
        def __getitem__(self, index): return Page()
        def close(self): pass

    monkeypatch.setattr(module.pdfium, 'PdfDocument', PDF)
    receipt = tmp_path / 'synthetic-receipt.json'
    receipt.write_text(json.dumps({'pdf_sha256': hashlib.sha256(
        (directory / '论文.pdf').read_bytes()).hexdigest(),
        'pages': [{'page': 1, 'inspected': True, 'clean': True}]}), encoding='utf-8')
    args = SimpleNamespace(task_id='t1', delivery_dir=directory,
        candidate_report=directory / '候选验收.json', visual_receipt=receipt,
        output=tmp_path / 'audit.json')

    def rebuild_bundle(omit=()):
        with zipfile.ZipFile(directory / '论文交付包.zip', 'w') as archive:
            for path in directory.iterdir():
                if path.name != '论文交付包.zip' and path.name not in omit:
                    archive.write(path, path.name)

    rebuild_bundle()
    return SimpleNamespace(module=module, args=args, directory=directory,
                           texts=texts, rebuild_bundle=rebuild_bundle)


def test_synthetic_complete_delivery_passes_with_pending_human_review(audited_delivery):
    env = audited_delivery
    result = env.module.audit(env.args)
    assert result['ready_for_review_delivery'], result['errors']
    assert result['human_fact_review'] == 'pending'


@pytest.mark.parametrize('prefix', ['cite', 'figref', 'source', 'evidence',
                                  'knowledge_unit', 'document_reading'])
@pytest.mark.parametrize('format_name', ['docx', 'pdf'])
def test_every_internal_marker_is_rejected_in_both_formats(audited_delivery, prefix, format_name):
    env = audited_delivery
    env.texts[format_name] += f' [{prefix}:internal-id]'
    result = env.module.audit(env.args)
    assert not result['ready_for_review_delivery']
    assert f'{format_name.upper()} contains raw internal reference tokens' in result['errors']


def test_converted_internal_marker_does_not_break_prose_preservation(audited_delivery):
    env = audited_delivery
    with sqlite3.connect(env.module.ROOT / 'data/workbench.sqlite3') as connection:
        candidate = json.loads(connection.execute('SELECT result FROM tasks WHERE id=?', ('t1',)).fetchone()[0])
        text = candidate['paper_sections'][0]['text']
        candidate['paper_sections'][0]['text'] = text[:45] + '[knowledge_unit:internal-id]' + text[45:]
        connection.execute('UPDATE tasks SET result=? WHERE id=?', (json.dumps(candidate), 't1'))
    result = env.module.audit(env.args)
    assert result['ready_for_review_delivery'], result['errors']


@pytest.mark.parametrize('status', ['generated_independent_layout', 'office_render_failed', None])
def test_non_native_pdf_cannot_pass_as_a_warning(audited_delivery, status):
    env = audited_delivery
    (env.directory / '检查报告.json').write_text(json.dumps({
        'pdf_status': status, 'docx_property_checks': {'fail': 0}}), encoding='utf-8')
    env.rebuild_bundle()
    result = env.module.audit(env.args)
    assert not result['ready_for_review_delivery']
    assert any('native Office/LibreOffice' in error for error in result['errors'])


@pytest.mark.parametrize('name', ['候选验收.json', '审阅说明.md'])
def test_early_zip_missing_candidate_notes_is_rejected(audited_delivery, name):
    env = audited_delivery
    env.rebuild_bundle(omit=[name])
    result = env.module.audit(env.args)
    assert not result['ready_for_review_delivery']
    assert 'The delivery ZIP is missing ' + name in result['errors']


@pytest.mark.parametrize('name', ['候选验收.json', '审阅说明.md'])
def test_stale_candidate_notes_in_zip_are_rejected(audited_delivery, name):
    env = audited_delivery
    path = env.directory / name
    path.write_bytes(path.read_bytes() + b'\n')
    result = env.module.audit(env.args)
    assert not result['ready_for_review_delivery']
    assert 'The delivery ZIP contains a stale copy of ' + name in result['errors']
