"""Field-bearing exports prefer a document COM renderer over plain conversion."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from test_workbench import client, project, manual_package, section, save
from app import db, exports, office_renderer


@pytest.mark.parametrize('registered,has_powershell,expected', [
    (['Word.Application'], True, 'office-com'),
    (['KWPS.Application'], True, 'office-com'),
    (['PowerPoint.Application', 'KWPP.Application'], True, 'libreoffice'),
    (['Word.Application'], False, 'libreoffice'),
])
def test_toc_uses_document_com_when_available_without_using_ppt_registration(
        client, monkeypatch, registered, has_powershell, expected):
    pid = project(client)
    manual_package(client, pid)
    sid = section(client, pid)
    assert save(client, sid, '这是隔离测试中的正文，不调用真实Office进程。').status_code == 200
    monkeypatch.setattr(exports, 'renderer', lambda: 'synthetic-soffice')
    monkeypatch.setattr(office_renderer, 'capabilities', lambda: {
        'registered_com': registered, 'powershell': 'pwsh.exe' if has_powershell else None})
    calls = []

    def render_path(source, destination, kind, updated_docx=None):
        calls.append('office-com')
        assert kind == 'docx' and updated_docx is not None
        assert Path(source).resolve() != Path(updated_docx).resolve()
        shutil.copyfile(source, updated_docx)
        Path(destination).write_bytes(b'synthetic PDF; no real renderer')
        return {'engine': registered[0], 'source_readonly': True,
                'updated_docx_written': True, 'actual_pdf_pages': 1}

    def convert(arguments, **kwargs):
        calls.append('libreoffice')
        assert arguments[0] == 'synthetic-soffice'
        output = Path(arguments[arguments.index('--outdir') + 1])
        (output / '论文.pdf').write_bytes(b'synthetic PDF; no real converter')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(office_renderer, 'render_path', render_path)
    monkeypatch.setattr(exports.subprocess, 'run', convert)
    result = exports.export_project(pid, {'mode': 'draft', 'pdf': True, 'include_toc': True})
    assert calls == [expected]
    folder = exports.DATA / 'exports' / result['export_id']
    report = json.loads((folder / '检查报告.json').read_text(encoding='utf-8'))
    assert result['pdf'] is True and report['pdf_status'] == 'generated'
    if expected == 'office-com':
        assert report['office_render']['updated_docx_written'] is True
        assert '本次Office渲染中更新' in report['field_update']
    else:
        assert 'office_render' not in report
        assert report['field_update'].startswith('请在目标Word软件更新')
