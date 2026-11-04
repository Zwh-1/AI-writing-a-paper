"""Candidate delivery must remain read-only and separate from adoption."""
import zipfile
import pytest

from test_workbench import client  # noqa: F401
from test_paper_writing import manuscript, finish  # noqa: F401
from app import db
from scripts.verify_whole_paper_candidate import read_only_database, verify, export_candidate


def test_incomplete_candidate_is_refused_without_changing_project(manuscript, tmp_path):
    task = db.one('SELECT * FROM tasks WHERE project_id=? AND kind=\'write_paper\' LIMIT 1',
                  (manuscript['pid'],))
    if task is None:
        from test_paper_writing import launch
        response = launch(manuscript)
        task = db.one('SELECT * FROM tasks WHERE id=?', (response.json()['id'],))
    with read_only_database():
        report = verify(task)
        assert not report['candidate_ready']
        with pytest.raises(ValueError):
            export_candidate(task, report, tmp_path / 'delivery')
    assert not db.rows('SELECT * FROM exports WHERE project_id=?', (manuscript['pid'],))


def test_complete_candidate_exports_copy_without_adopting_or_registering_export(manuscript, tmp_path):
    tid, output = finish(manuscript)
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    baseline = {row['id']: row['current_revision_id'] for row in
                db.rows('SELECT id,current_revision_id FROM sections WHERE project_id=?', (manuscript['pid'],))}
    target = tmp_path / 'delivery'
    with read_only_database():
        report = verify(task)
        assert report['candidate_ready'] and report['completed_sections'] == 5, report['errors']
        result = export_candidate(task, report, target, pdf=False)
        assert '论文.md' in result['files'] and any(name.endswith('.docx') for name in result['files'])
        assert '候选验收.json' in result['files']
        assert (target / '论文.md').read_text(encoding='utf-8').count('完整候选') >= 5
        with zipfile.ZipFile(target / '论文交付包.zip') as bundle:
            expected = set(result['files']) - {'论文交付包.zip'}
            assert set(bundle.namelist()) == expected
            for name in expected:
                assert bundle.read(name) == (target / name).read_bytes()
    assert not db.rows('SELECT * FROM exports WHERE project_id=?', (manuscript['pid'],))
    assert not db.rows('SELECT * FROM revisions WHERE section_id IN (' + ','.join('?' for _ in baseline) + ')', tuple(baseline))
    assert baseline == {row['id']: row['current_revision_id'] for row in
                        db.rows('SELECT id,current_revision_id FROM sections WHERE project_id=?', (manuscript['pid'],))}
    assert db.loads(db.one('SELECT result FROM tasks WHERE id=?', (tid,))['result']) == output


def test_bundle_replaces_early_archive_and_includes_only_generated_files(tmp_path, monkeypatch):
    from app import exports
    from scripts import verify_whole_paper_candidate as delivery
    target = tmp_path / 'delivery'
    original_data = exports.DATA
    monkeypatch.setattr(delivery, 'candidate_snapshot', lambda *args: {})

    def early_export(*args, **kwargs):
        folder = exports.DATA / 'exports' / 'synthetic'
        folder.mkdir()
        (folder / '论文.docx').write_bytes(b'current document')
        (folder / 'render.pending.pdf').write_bytes(b'incomplete render')
        with zipfile.ZipFile(folder / '论文交付包.zip', 'w') as archive:
            archive.writestr('论文.docx', b'stale document')
        # A different process creating a file here must not add it to the ZIP.
        (target / 'unrelated.txt').write_text('not an exporter output', encoding='utf-8')
        return {'export_id': 'synthetic'}

    monkeypatch.setattr(exports, 'export_project', early_export)
    delivery.export_candidate({'project_id': 'isolated'}, {'candidate_ready': True}, target)
    assert exports.DATA == original_data
    with zipfile.ZipFile(target / '论文交付包.zip') as archive:
        assert set(archive.namelist()) == {'论文.docx', '候选验收.json', '审阅说明.md'}
        for name in archive.namelist():
            assert archive.read(name) == (target / name).read_bytes()
