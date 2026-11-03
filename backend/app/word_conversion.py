"""Legacy Word parser adapter independent of research HTTP and provider services."""
import os,shutil,subprocess,tempfile
from pathlib import Path

def convert_doc(data):
    if not data.startswith(bytes.fromhex('d0cf11e0a1b11ae')):raise ValueError('文件不是有效的旧版二进制 Word DOC')
    executable=os.environ.get('PAPER_LIBREOFFICE_PATH') or shutil.which('soffice')
    if not executable:
        executable=next((str(p) for p in [Path(r'C:\Program Files\LibreOffice\program\soffice.exe'),Path('/Applications/LibreOffice.app/Contents/MacOS/soffice')] if p.is_file()),None)
    if not executable:raise ValueError('旧 DOC 转换需要 LibreOffice；请安装后重试，或先另存为 DOCX/PDF')
    from .config import DATA
    with tempfile.TemporaryDirectory(prefix='legacy-doc-',dir=DATA/'tmp') as temporary:
        folder=Path(temporary);source=folder/'original.doc';source.write_bytes(data)
        result=subprocess.run([executable,'-env:UserInstallation='+(folder/'profile').as_uri(),'--headless','--convert-to','docx','--outdir',str(folder),str(source)],capture_output=True,timeout=120,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        target=folder/'original.docx'
        if result.returncode or not target.is_file():raise ValueError('LibreOffice 未能转换旧 DOC，原文件已保留')
        return target.read_bytes()
