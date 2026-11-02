"""An isolated full-paper UI fixture. All model output is synthetic."""
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.runtime/python-deps'), str(ROOT / '.runtime/advanced-deps'),
                str(ROOT / 'backend'), str(ROOT / 'tests')]

if __name__ == '__main__':
    import tempfile
    import pytest
    import uvicorn
    from test_workbench import client
    from test_paper_writing import manuscript
    from app import db, tasks
    import app.main as main
    port = int(os.environ.get('PAPER_TEST_PORT', '8879'))
    directory = Path(tempfile.mkdtemp(prefix='whole-paper-browser-', dir=ROOT / '.runtime'))
    patch = pytest.MonkeyPatch()
    fixture = client.__wrapped__(directory, patch)
    c = next(fixture)
    env = manuscript.__wrapped__(c, patch)
    original_generate = tasks.model_call
    paused = set()
    def generate(task,system, user,manifest, images=None):
        payload = db.loads(user)
        if '预算中断样例' in payload.get('instruction', '') and payload.get('section', {}).get('kind') == 'implementation' and not paused:
            from app.summary_billing import BudgetExceeded
            paused.add('once')
            raise BudgetExceeded('模拟预算暂停；整篇已完成候选会保留')
        return original_generate(task,system, user,manifest, images)
    from app import provider
    patch.setattr(tasks, 'model_call', generate)
    patch.setattr(main, 'PORT', port)
    (ROOT / '.runtime/whole-paper-browser-fixture.json').write_text(db.dumps({
        'port': port, 'project_id': env['pid'], 'section_id': env['sections'][2]['id'],
        'data_directory': str(directory), 'synthetic_model': True}), encoding='utf-8')
    worker = threading.Thread(target=tasks.run_forever, daemon=True)
    worker.start()
    try:
        uvicorn.run(main.app, host='127.0.0.1', port=port, log_level='warning', access_log=False)
    finally:
        tasks.STOP.set()
        worker.join(timeout=5)
        try:
            next(fixture)
        except StopIteration:
            pass
        patch.undo()
