"""An isolated browser fixture; model responses are synthetic and never sent remotely."""
import os,sys,threading
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
for folder in (ROOT/'.runtime'/'python-deps',ROOT/'.runtime'/'advanced-deps',ROOT/'backend',ROOT/'tests'):
    sys.path.insert(0,str(folder))

if __name__=='__main__':
    import tempfile,pytest,uvicorn
    from test_writing_agent import env,fake_model
    from app import db,tasks
    import app.main as main
    port=int(os.environ.get('PAPER_TEST_PORT','8801'))
    directory=Path(tempfile.mkdtemp(prefix='writing-launch-fixture-',dir=ROOT/'.runtime'))
    patch=pytest.MonkeyPatch();fixture=env.__wrapped__(directory,patch)
    test=next(fixture);fake_model(patch);patch.setattr(main,'PORT',port)
    db.execute('DELETE FROM writing_profiles WHERE project_id=?',(test['pid'],))
    (ROOT/'.runtime'/'writing-launch-fixture.json').write_text(db.dumps({'port':port,'project_id':test['pid'],'section_id':test['sid'],'data_directory':str(directory),'synthetic_model':True}),encoding='utf-8')
    worker=threading.Thread(target=tasks.run_forever,daemon=True);worker.start()
    try:uvicorn.run(main.app,host='127.0.0.1',port=port,log_level='warning',access_log=False)
    finally:
        tasks.STOP.set();worker.join(timeout=5)
        try:next(fixture)
        except StopIteration:pass
        patch.undo()
