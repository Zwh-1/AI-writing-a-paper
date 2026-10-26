import os, ctypes, secrets, hashlib
from pathlib import Path
from ctypes import wintypes
from fastapi import HTTPException
from .config import DATA
from . import db
SESSION_TOKEN=secrets.token_urlsafe(32)
BOOTSTRAP_TOKEN=secrets.token_urlsafe(32)

class Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]

def _blob(data):
    array=ctypes.create_string_buffer(data)
    return Blob(len(data),ctypes.cast(array,ctypes.POINTER(ctypes.c_ubyte))),array

def protect(data, decrypt=False):
    if os.name!='nt': raise RuntimeError('密钥持久化需要 Windows DPAPI；其他平台请使用环境变量 PAPER_API_KEY')
    inp,keep=_blob(data); out=Blob()
    crypt=ctypes.windll.crypt32
    if decrypt:
        ok=crypt.CryptUnprotectData(ctypes.byref(inp),None,None,None,None,1,ctypes.byref(out))
    else:
        ok=crypt.CryptProtectData(ctypes.byref(inp),'Paper Workbench',None,None,None,1,ctypes.byref(out))
    if not ok: raise RuntimeError('Windows 凭据加密/解密失败')
    try: return ctypes.string_at(out.pbData,out.cbData)
    finally: ctypes.windll.kernel32.LocalFree(out.pbData)

def save_key(value,profile=""):
    if profile not in ("","material-summary","gpt-fallback","image-generation"):raise ValueError("未知凭据类型")
    import tempfile
    path=DATA/(('credential' if not profile else profile)+'.dpapi')
    if not isinstance(value,str) or len(value)>4096 or '\n' in value or '\r' in value:raise HTTPException(422,'密钥格式无效，请粘贴完整的单行密钥')
    if not value:
        try:path.unlink(missing_ok=True)
        except OSError as error:raise HTTPException(503,'无法删除密钥文件，请检查数据目录写入权限') from error
        return
    try:
        encrypted=protect(value.encode('utf-8'))
        restored=protect(encrypted,True).decode('utf-8')
        if not secrets.compare_digest(restored.encode('utf-8'),value.encode('utf-8')):raise RuntimeError('加密回读验证不一致')
    except Exception as error:raise HTTPException(503,'密钥加密保存校验失败，原密钥未改动；请检查当前Windows用户环境后重试') from error
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(prefix='.credential-',suffix='.tmp',dir=DATA,delete=False) as file:
            temporary=Path(file.name);file.write(encrypted);file.flush();os.fsync(file.fileno())
        os.replace(temporary,path)
    except OSError as error:raise HTTPException(503,'密钥无法写入数据目录，原密钥未改动；请检查目录权限和磁盘空间') from error
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)

def get_key(profile=""):
    if profile not in ("","material-summary","gpt-fallback","image-generation"):raise ValueError("未知凭据类型")
    if profile:
        variable={'gpt-fallback':'PAPER_GPT_API_KEY','material-summary':'PAPER_SUMMARY_API_KEY','image-generation':'PAPER_IMAGE_API_KEY'}[profile]
        value=os.environ.get(variable, "")
        if value:return value
        path=DATA/(profile+".dpapi")
        return protect(path.read_bytes(),True).decode() if path.exists() else ""
    if os.environ.get('PAPER_API_KEY'): return os.environ['PAPER_API_KEY']
    if os.environ.get('PAPER_API_KEY_FILE'):
        path=Path(os.environ['PAPER_API_KEY_FILE']);value=path.read_text(encoding='utf-8-sig').strip()
        if '\n' in value or '\r' in value:raise RuntimeError('密钥文件必须包含单行密钥')
        return value
    path=DATA/'credential.dpapi'
    return protect(path.read_bytes(),True).decode() if path.exists() else ''

def key_status(profile=""):
    try:
        key=get_key(profile) if profile else get_key(); return {'configured':bool(key),'masked':('••••'+key[-4:]) if key else '', 'storage':'Windows DPAPI'}
    except Exception: return {'configured':False,'masked':'','storage':'解密失败，请重新填写密钥'}

def safe_object(sha):
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha): raise HTTPException(422,'资源标识无效')
    return DATA/'objects'/sha

def put_blob(data):
    sha=hashlib.sha256(data).hexdigest(); path=safe_object(sha)
    if not path.exists(): path.write_bytes(data)
    return sha

def require_project(pid):
    project=db.one('SELECT * FROM projects WHERE id=?',(pid,))
    if not project: raise HTTPException(404,'论文项目不存在')
    return project

def require_section(sid):
    section=db.one('SELECT * FROM sections WHERE id=?',(sid,))
    if not section: raise HTTPException(404,'章节不存在')
    return section

def redact(message):
    try: key=get_key()
    except Exception: key=''
    result=str(message).replace(key,'[已隐藏密钥]') if key else str(message)
    try:summary_key=get_key('material-summary')
    except Exception:summary_key=''
    result=result.replace(summary_key,'[已隐藏密钥]') if summary_key else result
    try:gpt_key=get_key('gpt-fallback')
    except Exception:gpt_key=''
    result=result.replace(gpt_key,'[已隐藏密钥]') if gpt_key else result
    try:image_key=get_key('image-generation')
    except Exception:image_key=''
    return (result.replace(image_key,'[已隐藏密钥]') if image_key else result)[:1500]


def file_allows_remote(fid,pid=None,seen=None):
    seen=set() if seen is None else set(seen)
    if fid in seen:return False
    seen.add(fid);row=db.one('SELECT * FROM files WHERE id=?',(fid,))
    if not row or not row['active'] or row['egress']=='local_only' or pid is not None and row['project_id']!=pid:return False
    for parent in db.rows('SELECT parent_file_id FROM file_derivations WHERE child_file_id=?',(fid,)):
        if not file_allows_remote(parent['parent_file_id'],row['project_id'],seen):return False
    return True


def link_derivation(child,parent,reason):
    if child==parent:return
    # Returning to an already-known identical version must not create a provenance cycle.
    cycle=db.one('WITH RECURSIVE origin(id) AS (SELECT ? UNION SELECT d.parent_file_id FROM file_derivations d JOIN origin o ON d.child_file_id=o.id) SELECT id FROM origin WHERE id=?',(parent,child))
    if cycle:return
    db.execute('INSERT OR IGNORE INTO file_derivations VALUES(?,?,?)',(child,parent,reason))
