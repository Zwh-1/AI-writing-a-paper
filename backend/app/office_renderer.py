"""Read-only Office rendering. Every PowerShell child verifies the required version."""
import json,os,re,shutil,subprocess,tempfile,threading,zipfile
from pathlib import Path
from . import db,security
from .config import ROOT
_LOCK=threading.Lock()
def capabilities():
    available=[]
    if os.name=='nt':
        import winreg
        for name in ('PowerPoint.Application','KWPP.Application','Word.Application','KWPS.Application'):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,name):available.append(name)
            except OSError:pass
    return {'registered_com':available,'powershell':shutil.which('pwsh.exe'),'libreoffice':shutil.which('soffice'),'registration_is_not_a_render_test':True}

def render_path(source,destination,kind):
    with zipfile.ZipFile(source) as z:
        if any('vbaproject' in name.lower() for name in z.namelist()):raise ValueError('含宏文档不进入自动版式渲染')
        from lxml import etree as E
        for name in z.namelist():
            if name.endswith('.rels'):
                rr=E.fromstring(z.read(name),E.XMLParser(resolve_entities=False,no_network=True))
                if any(n.get('TargetMode')=='External' and not n.get('Type','').endswith('/hyperlink') for n in rr):raise ValueError('文档存在自动外部资源关系，需先人工审核后再渲染')
            if name.startswith('word/') and name.endswith('.xml'):
                rr=E.fromstring(z.read(name),E.XMLParser(resolve_entities=False,no_network=True));instructions=' '.join(rr.xpath('//*[local-name()="instrText"]/text()'))
                if re.search(r'\b(DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|DATABASE|MACROBUTTON|LINK)\b',instructions,re.I):raise ValueError('文档包含外部内容或命令域，拒绝自动更新和渲染')
    pwsh=shutil.which('pwsh.exe')
    if not pwsh:raise ValueError('未找到必需的PowerShell 7.6.5')
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    pending=destination.with_name(destination.stem+'.pending.pdf')
    with _LOCK:
        result=subprocess.run([pwsh,'-NoLogo','-NoProfile','-File',str(ROOT/'scripts/office-render.ps1'),'-Source',str(source),'-Output',str(pending),'-Kind',kind],capture_output=True,timeout=150,**flags)
    if result.returncode or not pending.exists():raise ValueError('本地Office渲染失败：'+result.stderr.decode('utf-8',errors='replace')[-700:])
    try:receipt=json.loads(result.stdout.decode('utf-8-sig').strip())
    except ValueError:receipt={'engine':'Office COM','diagnostic':'进程已完成，未返回引擎元数据'}
    import pypdfium2 as pdfium
    pdf=pdfium.PdfDocument(str(pending));receipt['actual_pdf_pages']=len(pdf);pdf.close()
    if kind=='pptx' and receipt.get('pages') and receipt['pages']!=receipt['actual_pdf_pages']:raise ValueError('渲染页数与源幻灯片不一致，拒绝错误页码映射')
    os.replace(pending,destination);receipt['visual_acceptance']='pending';return receipt

def render_file(file):
    if file['extension'] not in ('.pptx','.docx','.pdf'):raise ValueError('整页预览需要PPTX、DOCX或PDF')
    destination=security.DATA/'previews'/(file['sha256']+'-office.pdf');metadata=destination.with_suffix('.json')
    if destination.exists() and metadata.exists():return {**json.loads(metadata.read_text(encoding='utf-8')),'cached':True}
    if file['extension']=='.pdf':
        shutil.copyfile(security.safe_object(file['sha256']),destination)
        import pypdfium2 as pdfium
        pdf=pdfium.PdfDocument(str(destination));receipt={'engine':'original_pdf','actual_pdf_pages':len(pdf),'source_readonly':True,'source_sha256':file['sha256'],'visual_acceptance':'pending'};pdf.close();metadata.write_text(json.dumps(receipt),encoding='utf-8');return receipt
    folder=Path(tempfile.mkdtemp(prefix='office-',dir=security.DATA/'tmp'));source=folder/('source'+file['extension']);shutil.copyfile(security.safe_object(file['sha256']),source)
    receipt=render_path(source,destination,file['extension'][1:]);receipt.update({'file_id':file['id'],'source_sha256':file['sha256'],'rendered_at':db.now()});metadata.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8');return receipt

def page_image(file,number):
    pdfpath=security.DATA/'previews'/(file['sha256']+'-office.pdf')
    if not pdfpath.exists():raise FileNotFoundError('请先完成Office整页渲染')
    import pypdfium2 as pdfium
    pdf=pdfium.PdfDocument(str(pdfpath))
    try:
        if not 1<=number<=len(pdf):raise ValueError('页码超出渲染结果')
        page=pdf[number-1];bitmap=page.render(scale=1.6)
        try:
            output=security.DATA/'previews'/(file['sha256']+f'-office-{number}.png');bitmap.to_pil().save(output);return output
        finally:bitmap.close();page.close()
    finally:pdf.close()
