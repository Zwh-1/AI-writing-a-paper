"""Release provenance is useful in real checkouts and optional in fixture roots."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('release_package_source', Path(__file__).resolve().parents[1] / 'scripts/package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def test_source_revision_matches_real_checkout():
    result = package.source_revision(Path(__file__).resolve().parents[1])
    assert result['commit'] and len(result['commit']) == 40
    assert isinstance(result['dirty'], bool)


def test_source_revision_does_not_borrow_an_ancestor_checkout(tmp_path):
    assert package.source_revision(tmp_path) == {'commit': None, 'dirty': None}


def test_release_guides_include_the_writing_delivery():
    assert 'docs/核心写作与素材闭环实施记录_2026-11-03.md' in package.GUIDES
    assert 'docs/0.10.0完整整合便携版使用说明.md' in package.GUIDES


def test_package_privacy_guard_covers_environment_variants_and_database_sidecars():
    for name in ('backend/.env.production', 'backend/session.json', 'backend/project.db',
                 'private/account.json', 'backend/project.sqlite3-wal', 'backend/credential.dpapi'):
        assert package._private(name), name
    assert not package._private('deployment/.env.example')
    assert not package._private('.runtime/python-deps/certifi/cacert.pem')
