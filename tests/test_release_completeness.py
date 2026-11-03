"""Release metadata and artifact workflows must cover the advertised outputs."""
import json,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_version_update_keeps_native_installer_and_api_in_sync(tmp_path):
    for folder in ('frontend','backend/app','deployment/desktop','scripts'):(tmp_path/folder).mkdir(parents=True,exist_ok=True)
    (tmp_path/'scripts/release-version.mjs').write_text((ROOT/'scripts/release-version.mjs').read_text(encoding='utf-8'),encoding='utf-8')
    for filename in ('frontend/package.json','frontend/package-lock.json'):
        (tmp_path/filename).write_text(json.dumps({'version':'0.9.0','packages':{'':{'version':'0.9.0'}}}),encoding='utf-8')
    (tmp_path/'backend/app/config.py').write_text("APP_VERSION = '0.9.0'\n",encoding='utf-8')
    (tmp_path/'deployment/desktop/windows.nsi').write_text('!define VERSION "0.9.0"\n',encoding='utf-8')
    result=subprocess.run(['node',str(tmp_path/'scripts/release-version.mjs'),'0.9.1'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads((tmp_path/'frontend/package.json').read_text())['version']=='0.9.1'
    assert json.loads((tmp_path/'frontend/package-lock.json').read_text())['packages']['']['version']=='0.9.1'
    assert "APP_VERSION = '0.9.1'" in (tmp_path/'backend/app/config.py').read_text()
    assert '!define VERSION "0.9.1"' in (tmp_path/'deployment/desktop/windows.nsi').read_text()


def test_workflows_include_pr_updates_and_both_macos_archive_types():
    pr=(ROOT/'.github/workflows/auto-pr.yml').read_text(encoding='utf-8')
    release=(ROOT/'.github/workflows/release.yml').read_text(encoding='utf-8')
    assert 'gh pr create' in pr and 'gh pr edit' in pr and '--body-file' in pr
    assert 'hdiutil create' in release and 'ditto -c -k' in release and '*-macOS.zip' in release
    assert 'deployment/desktop/windows.nsi' in release
