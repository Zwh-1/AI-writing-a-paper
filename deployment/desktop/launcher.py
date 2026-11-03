"""A PyInstaller desktop entry; worker/OCR children retain the normal runner modes."""
import multiprocessing
import os
import runpy
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

def main():
    multiprocessing.freeze_support()
    bundle=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
    if not getattr(sys,'frozen',False):bundle=Path(__file__).resolve().parents[2]
    user=Path(os.environ.get('APPDATA',Path.home()/'Library/Application Support' if sys.platform=='darwin' else Path.home()/'.local/share'))/'Yantai'
    user.mkdir(parents=True,exist_ok=True);os.environ.setdefault('PAPER_DATA_DIR',str(user/'data'));os.environ.setdefault('PAPER_SESSION_DIR',str(user/'runtime'))
    if os.name!='nt' and not os.environ.get('PAPER_CREDENTIAL_KEY_FILE'):
        from cryptography.fernet import Fernet
        key=user/'credential.key'
        if not key.exists():key.write_bytes(Fernet.generate_key());key.chmod(0o600)
        os.environ['PAPER_CREDENTIAL_KEY_FILE']=str(key)
    sys.path[:0]=[str(bundle/'backend'),str(bundle/'.runtime/python-deps'),str(bundle/'.runtime/advanced-deps')]
    runner=next((i for i,value in enumerate(sys.argv) if value.endswith('runner.py')),None)
    if runner is not None:
        sys.argv=[str(bundle/'runner.py'),*sys.argv[runner+1:]];runpy.run_path(str(bundle/'runner.py'),run_name='__main__');return
    from app import db,security,tasks,config
    db.init();port=int(os.environ.get('PAPER_PORT','8765'))
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',port))
        except OSError:raise RuntimeError('端口已被占用，请关闭已运行的工作台或设置 PAPER_PORT。')
    threading.Thread(target=tasks.run_forever,daemon=True).start()
    def open_browser():
        if os.environ.get('PAPER_NO_BROWSER')=='1':return
        time.sleep(1.5);webbrowser.open(f'http://127.0.0.1:{port}/#token={security.BOOTSTRAP_TOKEN}')
    threading.Thread(target=open_browser,daemon=True).start()
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=port,log_level='warning')

if __name__=='__main__':main()
