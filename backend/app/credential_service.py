"""Shared encrypted credential store; existing vault format and environment keys stay compatible."""
import os,threading
from pathlib import Path
from fastapi import HTTPException
from . import db,security
_VAULT_LOCK=threading.RLock()

def read_secret(name):
    variable='PAPER_'+name.upper().replace(':','_').replace('-','_')
    if os.environ.get(variable):return os.environ[variable]
    from .config import DATA
    path=DATA/'studio-credentials.dpapi'
    from filelock import FileLock
    with _VAULT_LOCK,FileLock(str(path)+'.lock',timeout=15):
        values=db.loads(security.protect(path.read_bytes(),True).decode(),{}) if path.exists() else {}
        return values.get(name,'')

def write_secret(name,value):
    if not isinstance(value,str) or len(value)>12000:raise HTTPException(422,'凭证格式无效')
    from .config import DATA
    import tempfile
    path=DATA/'studio-credentials.dpapi'
    from filelock import FileLock
    with _VAULT_LOCK,FileLock(str(path)+'.lock',timeout=15):
        values=db.loads(security.protect(path.read_bytes(),True).decode(),{}) if path.exists() else {}
        if value:values[name]=value
        else:values.pop(name,None)
        encrypted=security.protect(db.dumps(values).encode())
        with tempfile.NamedTemporaryFile(dir=DATA,prefix='.studio-secret-',delete=False) as file:
            temporary=Path(file.name);file.write(encrypted);file.flush();os.fsync(file.fileno())
        try:os.replace(temporary,path)
        finally:temporary.unlink(missing_ok=True)
