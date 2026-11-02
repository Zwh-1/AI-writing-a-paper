"""Local Windows OCR. Recognition never marks a page as human reviewed."""
import asyncio,io,os,re,json,hashlib,tempfile,unicodedata,subprocess,sys,time
from pathlib import Path
from . import db
from .config import DATA
from .security import safe_object
from .parsers import tokens

def _native_capabilities():
    if os.name!='nt':return {'available':False,'reason':'本地OCR需要Windows'}
    try:
        from winrt.windows.media.ocr import OcrEngine
        languages=[{'tag':x.language_tag,'name':x.display_name} for x in OcrEngine.available_recognizer_languages]
        return {'available':bool(languages),'languages':languages,'engine':'Windows.Media.Ocr','external_requests':False}
    except Exception as e:return {'available':False,'reason':type(e).__name__+': '+str(e)[:180]}

async def _recognize_path_native(path,language='zh-Hans-CN'):
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder,BitmapPixelFormat,BitmapAlphaMode
    from winrt.windows.storage import StorageFile,FileAccessMode
    engine=OcrEngine.try_create_from_language(Language(language)) if language else OcrEngine.try_create_from_user_profile_languages()
    if engine is None:raise ValueError('当前Windows没有所需OCR语言包，请选择已安装语言')
    file=await StorageFile.get_file_from_path_async(str(Path(path).resolve()));stream=await file.open_async(FileAccessMode.READ)
    try:
        decoder=await BitmapDecoder.create_async(stream)
        bitmap=await decoder.get_software_bitmap_converted_async(BitmapPixelFormat.BGRA8,BitmapAlphaMode.PREMULTIPLIED)
        try:
            result=await engine.recognize_async(bitmap)
            lines=[]
            for line in result.lines:
                words=[{'text':w.text,'box':{'x':w.bounding_rect.x,'y':w.bounding_rect.y,'width':w.bounding_rect.width,'height':w.bounding_rect.height}} for w in line.words]
                recognized=re.sub(r'(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff，。；：、（）])','',line.text)
                recognized=re.sub(r'(?<=[，。；：、（]) +(?=[\u4e00-\u9fff])','',recognized)
                lines.append({'text':recognized,'raw_text':line.text,'words':words})
            return {'text':'\n'.join(x['text'] for x in lines),'lines':lines,'language':engine.recognizer_language.language_tag}
        finally:bitmap.close()
    finally:stream.close()

_CAPABILITY_CACHE = None

def _ocr_process(mode, payload=None, timeout=60):
    """WinRT stays in a short-lived process, separate from ONNX/native research libraries."""
    from . import security
    from .config import ROOT
    env={**os.environ,'PAPER_DATA_DIR':str(security.DATA)}
    env.pop('PAPER_API_KEY',None);env.pop('PAPER_API_KEY_FILE',None)
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    try:
        process=subprocess.run([sys.executable,'-I','-X','utf8',str(ROOT/'runner.py'),mode],input=json.dumps(payload or {},ensure_ascii=False),text=True,encoding='utf-8',capture_output=True,timeout=timeout,env=env,**flags)
    except subprocess.TimeoutExpired as error:raise ValueError('本地OCR子进程超时，已终止本次识别，可重试或人工转录') from error
    if process.returncode:
        raise ValueError(f'本地OCR子进程异常退出（{process.returncode}），主工作进程已保留；'+process.stderr[-350:])
    if len(process.stdout)>8*1024*1024:raise ValueError('本地OCR返回内容超过上限')
    try:value=json.loads(process.stdout)
    except ValueError as error:raise ValueError('本地OCR子进程未返回有效识别结果') from error
    if not isinstance(value,dict):raise ValueError('本地OCR结果类型无效')
    return value

def capabilities():
    global _CAPABILITY_CACHE
    if os.name!='nt':return {'available':False,'reason':'本地OCR需要Windows'}
    if _CAPABILITY_CACHE and time.monotonic()-_CAPABILITY_CACHE[0]<60:return dict(_CAPABILITY_CACHE[1])
    try:value=_ocr_process('ocr-capabilities',timeout=20)
    except Exception as error:value={'available':False,'reason':str(error)[:350],'external_requests':False}
    value['execution']='separate_local_process';_CAPABILITY_CACHE=(time.monotonic(),value)
    return dict(value)

async def recognize_path(path,language='zh-Hans-CN'):
    return await asyncio.to_thread(_ocr_process,'ocr-image',{'path':str(Path(path).resolve()),'language':language})

OCR_VERSION = 2

def _normal(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))

def _recognize_image(path, asset_sha, language):
    """Tile large images instead of shrinking fine print; cache only successful local OCR."""
    from PIL import Image
    from . import security
    cache_dir=security.DATA/'ocr-cache';cache_dir.mkdir(parents=True,exist_ok=True)
    cache_key=hashlib.sha256(f'{asset_sha}:{language}:{OCR_VERSION}'.encode()).hexdigest()
    cache_path=cache_dir/(cache_key+'.json')
    if cache_path.is_file():
        try:
            cached=json.loads(cache_path.read_text(encoding='utf-8'))
            if cached.get('asset_sha256')==asset_sha and cached.get('ocr_version')==OCR_VERSION:return {**cached,'cached':True}
        except (ValueError,OSError):pass
    with Image.open(path) as original:
        if original.width*original.height>50_000_000:raise ValueError('图片超过5000万像素，需人工拆分后识别')
        image=original.convert('RGB');width,height=image.size
    if width*height<256 or min(width,height)<8:
        return {'asset_sha256':asset_sha,'ocr_version':OCR_VERSION,'text':'','lines':[],'language':language,'skipped':'图片尺寸不足以可靠识别','image_size':[width,height],'tiles':0}
    # Upscale small text; keep larger source pixels and use overlapping crops.
    scale=min(2.0,2000/max(width,height)) if max(width,height)<1600 else 1.0
    if scale!=1:image=image.resize((round(width*scale),round(height*scale)),Image.Resampling.LANCZOS)
    edge=2200;overlap=160;regions=[]
    def starts(length):
        if length<=edge:return [0]
        values=list(range(0,length-edge+1,edge-overlap))
        if values[-1]!=length-edge:values.append(length-edge)
        return values
    for y in starts(image.height):
        for x in starts(image.width):regions.append((x,y,min(x+edge,image.width),min(y+edge,image.height)))
    if len(regions)>64:raise ValueError('图片需要超过64个识别分块，请拆分后重试')
    lines=[]
    for region in regions:
        handle=tempfile.NamedTemporaryFile(prefix='ocr-',suffix='.png',dir=security.DATA/'tmp',delete=False);temporary=Path(handle.name);handle.close()
        try:
            with image.crop(region) as crop:crop.save(temporary)
            result=asyncio.run(recognize_path(temporary,language))
        finally:temporary.unlink(missing_ok=True)
        for line in result['lines']:
            adjusted=[]
            for word in line.get('words',[]):
                box=word['box'];adjusted.append({**word,'box':{'x':(box['x']+region[0])/scale,'y':(box['y']+region[1])/scale,'width':box['width']/scale,'height':box['height']/scale}})
            current={**line,'words':adjusted,'tile_origin':[region[0]/scale,region[1]/scale]}
            if adjusted:
                current['x']=min(w['box']['x'] for w in adjusted);current['y']=min(w['box']['y'] for w in adjusted)
                # Only remove the same text in the same geometric location, never repeated table values.
                duplicate=any(_normal(old['text'])==_normal(current['text']) and abs(old.get('x',-9999)-current['x'])<20 and abs(old.get('y',-9999)-current['y'])<15 for old in lines)
                if duplicate:continue
            lines.append(current)
    image.close()
    lines.sort(key=lambda line:(round(line.get('y',0)/10),line.get('x',0)))
    result={'asset_sha256':asset_sha,'ocr_version':OCR_VERSION,'text':'\n'.join(line['text'] for line in lines),'lines':lines,'language':language,'image_size':[width,height],'tiles':len(regions),'external_requests':False}
    temp=cache_path.with_name(cache_path.name+'.'+db.uid()+'.tmp')
    try:
        temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');os.replace(temp,cache_path)
    finally:temp.unlink(missing_ok=True)
    return result

def page_ocr(page_id,language='zh-Hans-CN'):
    from . import security,material_quality
    page=db.one('SELECT p.*,f.project_id,f.sha256,f.extension,f.role FROM pages p JOIN files f ON f.id=p.file_id WHERE p.id=?',(page_id,))
    if not page:raise ValueError('页面不存在')
    file=db.one('SELECT * FROM files WHERE id=?',(page['file_id'],))
    if page['review_state'] in ('reviewed','not_applicable'):
        return {'page_id':page_id,'text':'','source_ids':[],'skipped':'保留人工核对或排除状态','diagnostic':{'ocr_state':'reviewed' if page['review_state']=='reviewed' else 'excluded','errors':[]},'external_requests':False}
    images=db.loads(page['images'],[]);assets=[];temporary=[];errors=[];warnings=[]
    if page['extension']=='.pdf':
        import pypdfium2 as pdfium
        from PIL import Image
        pdf=pdfium.PdfDocument(str(safe_object(page['sha256'])))
        try:
            p=pdf[page['page_number']-1]
            # 200 dpi retains small-print detail; tiling handles large pages.
            bitmap=p.render(scale=200/72)
            try:
                path=security.DATA/'tmp'/f'ocr-pdf-{db.uid()}.png';bitmap.to_pil().convert('RGB').save(path);temporary.append(path)
                asset_sha=hashlib.sha256(f"pdf:{page['sha256']}:{page['page_number']}:200dpi".encode()).hexdigest();assets.append((path,asset_sha,{'kind':'pdf_page','page':page['page_number']}))
            finally:bitmap.close();p.close()
        finally:pdf.close()
    else:
        for image in images:
            assets.append((safe_object(image['sha256']),image['sha256'],{'kind':'embedded_image','name':image.get('name'),'source_part':image.get('source_part'),'inherited':image.get('inherited',False)}))
    recognized=[]
    try:
        for path,asset_sha,origin in assets:
            try:recognized.append({**_recognize_image(path,asset_sha,language),'origin':origin})
            except InterruptedError:raise
            except Exception as error:errors.append(f"{origin.get('name') or origin['kind']}：{str(error)[:220]}")
        if errors and page['extension']=='.pptx':
            # Unsupported EMF/WMF/SVG assets can be read via the rendered original slide.
            try:
                from .office_renderer import render_file,page_image
                render_file(file);path=page_image(file,page['page_number'])
                asset_sha=hashlib.sha256(f"slide:{page['sha256']}:{page['page_number']}:office".encode()).hexdigest()
                recognized.append({**_recognize_image(path,asset_sha,language),'origin':{'kind':'office_slide','page':page['page_number']}})
                warnings=errors;errors=[]
            except Exception as error:errors.append('整页渲染补识别失败：'+str(error)[:250])
    finally:
        for path in temporary:path.unlink(missing_ok=True)
    if not assets and not recognized and page['has_visual']:
        errors.append('页面标记含视觉内容，但没有可识别图片；请查看原件或人工转录')
    source_ids=[];all_text=[];new_parts=[]
    kind='rule_candidate' if page['role']=='rules' else 'example' if page['role']=='example' else 'commentary' if page['role'] in ('draft','supervisor') else 'evidence'
    with db.transaction() as c:
        for result in recognized:
            all_text.append(result['text'])
            # Store the complete image observation. A line repeated in native
            # text still has its own position and cannot be dropped from OCR.
            candidate=result['text']
            added=False
            for start,fragment in [(0,candidate)]:
                if not fragment.strip():continue
                old=c.execute("SELECT id FROM sources WHERE page_id=? AND method='windows_ocr' AND text=?",(page_id,fragment)).fetchone()
                if old:source_ids.append(old['id']);continue
                sid=db.uid();locator={'kind':'windows_ocr','page':page['page_number'],'asset_sha256':result['asset_sha256'],'ocr_version':OCR_VERSION,'char_start':start,'char_end':len(candidate),'storage_scope':'complete_image_observation','origin':result['origin'],'recognition':[result]}
                c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,page['file_id'],page_id,db.dumps(locator),fragment,fragment,kind,'windows_ocr',0));c.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(fragment)));source_ids.append(sid);added=True
            if added:new_parts.append(candidate)
        if new_parts:
            c.execute("UPDATE pages SET text=text||?,review_state='unreviewed' WHERE id=?",('\n[本地OCR候选，需对照原页核对]\n'+'\n\n'.join(new_parts),page_id))
    text='\n\n'.join(x for x in all_text if x.strip())
    diagnostic={'ocr_state':'partial' if errors and recognized else 'failed' if errors else 'complete' if text else 'no_text','ocr_chars':len(text),'asset_count':len(assets),'recognized_assets':len(recognized),'errors':errors,'warnings':warnings,'language':language,'ocr_version':OCR_VERSION,'completed_at':db.now()}
    record=db.setting(material_quality.key(file['id']),{'sha256':file['sha256'],'page_states':{}})
    record.setdefault('page_states',{}).setdefault(page_id,{}).update(diagnostic);material_quality.save(file['id'],record)
    from .reference_knowledge import sync_document
    sync_document(file['id'])
    return {'page_id':page_id,'text':text,'source_ids':source_ids,'engine':'Windows.Media.Ocr','external_requests':False,'requires_human_review':True,'language':language,'diagnostic':diagnostic}
