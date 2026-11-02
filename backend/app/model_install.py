"""Download data-only ONNX models, verifying Git/LFS content hashes before activation."""
import concurrent.futures,hashlib,json,os,urllib.parse
from pathlib import Path
import httpx
from .config import ROOT
SPECS={
 'BAAI/bge-small-zh-v1.5':('Qdrant/bge-small-zh-v1.5','model_optimized.onnx'),
 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2':('Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q','model_optimized.onnx'),
 'BAAI/bge-reranker-base':('Xenova/bge-reranker-base','onnx/model_quantized.onnx','onnx/model.onnx'),
 'Xenova/ms-marco-MiniLM-L-6-v2':('Xenova/ms-marco-MiniLM-L-6-v2','onnx/model.onnx'),
}
def directory(model):return ROOT/'.runtime/models/local'/hashlib.sha256(model.encode()).hexdigest()[:20]
def status(model):
    folder=directory(model);p=folder/'verified-model.json'
    try:manifest=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else None
    except ValueError:manifest=None
    ready=bool(manifest and all((folder/name).is_file() for name in manifest.get('files',{})))
    fingerprint=hashlib.sha256(json.dumps(manifest.get('files',{}),sort_keys=True).encode()).hexdigest()[:20] if manifest else None
    return {'ready':ready,'model':model,'path':str(folder),'fingerprint':fingerprint,'manifest':manifest}
def download(model,endpoint='https://hf-mirror.com'):
    from filelock import FileLock
    folder=directory(model);folder.mkdir(parents=True,exist_ok=True)
    with FileLock(folder/'download.lock',timeout=1800):
        return _download(model,endpoint)

def _download(model,endpoint):
    if model not in SPECS:raise ValueError('当前下载器仅支持已登记的模型')
    if endpoint not in ('https://hf-mirror.com','https://huggingface.co'):raise ValueError('模型下载地址未登记')
    spec=SPECS[model];repo,model_file=spec[:2];local_file=spec[2] if len(spec)>2 else model_file;folder=directory(model);folder.mkdir(parents=True,exist_ok=True)
    cached=ROOT/'.runtime/neural-model-info-mirror.json'
    saved=json.loads(cached.read_text(encoding='utf-8')) if cached.exists() else {}
    if saved.get('id')==repo and saved.get('sha'):info=saved
    else:
        from .network import proxy_for
        with httpx.Client(timeout=httpx.Timeout(60,connect=20),proxy=proxy_for(endpoint),trust_env=False,follow_redirects=True) as c:
            response=c.get(endpoint+'/api/models/'+repo+'?blobs=true');response.raise_for_status();info=response.json()
    required={'README.md','LICENSE','LICENSE.txt','config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json',model_file,'vocab.txt'}
    entries=[item for item in info.get('siblings',[]) if item['rfilename'] in required]
    if model_file not in {item['rfilename'] for item in entries}:raise ValueError('模型仓库缺少已登记的ONNX文件')
    def fetch(item):
        import time,re
        name=item['rfilename'];target=folder/(local_file if name==model_file else name);target.parent.mkdir(parents=True,exist_ok=True);part=target.with_suffix(target.suffix+'.part');expected=int(item.get('size',0))
        if not 0<expected<=600*1024*1024:raise ValueError('模型文件大小无效或超出下载上限')
        if target.exists() and verify(target,item):return target.relative_to(folder).as_posix(),hashlib.sha256(target.read_bytes()).hexdigest()
        if part.exists() and part.stat().st_size>=expected:
            if part.stat().st_size==expected and verify(part,item):
                os.replace(part,target);return target.relative_to(folder).as_posix(),hashlib.sha256(target.read_bytes()).hexdigest()
            part.unlink()
        from .network import proxy_for
        url=endpoint+'/'+repo+'/resolve/'+info['sha']+'/'+urllib.parse.quote(name,safe='/')
        failures=0
        while not part.exists() or part.stat().st_size<expected:
            offset=part.stat().st_size if part.exists() else 0;end=min(expected-1,offset+8*1024*1024-1)
            try:
                with httpx.Client(timeout=httpx.Timeout(90,connect=20),proxy=proxy_for(endpoint),trust_env=False,follow_redirects=True) as c:
                    with c.stream('GET',url,headers={'Range':f'bytes={offset}-{end}','Accept-Encoding':'identity'}) as response:
                        response.raise_for_status()
                        if response.status_code==206:
                            match=re.match(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('content-range',''))
                            if not match or int(match.group(1))!=offset or int(match.group(3))!=expected:raise ValueError('模型分段响应范围不匹配')
                            mode='ab' if offset else 'wb'
                        elif response.status_code==200:mode='wb';offset=0
                        else:raise ValueError('模型下载响应状态无效')
                        size=offset
                        with part.open(mode) as out:
                            for chunk in response.iter_raw(256*1024):
                                size+=len(chunk)
                                if size>expected:raise ValueError('模型响应超过声明大小')
                                out.write(chunk)
                failures=0
            except httpx.HTTPError:
                failures+=1
                if failures>=3:raise
                time.sleep(1)
        if not verify(part,item):
            part.unlink(missing_ok=True)
            raise ValueError('模型文件Git/LFS哈希校验失败：'+name)
        os.replace(part,target);return target.relative_to(folder).as_posix(),hashlib.sha256(target.read_bytes()).hexdigest()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:hashes=dict(pool.map(fetch,entries))
    manifest={'model':model,'repository':repo,'repository_revision':info['sha'],'download_endpoint':endpoint,'license':info.get('cardData',{}).get('license'),'model_file':local_file,'remote_model_file':model_file,'files':hashes,'paper_content_sent':False,'api_key_used':False}
    temporary=folder/'verified-model.json.tmp';temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temporary,folder/'verified-model.json');return manifest

def verify(path,item):
    data=path.read_bytes()
    if len(data)!=int(item.get('size',len(data))):return False
    if item.get('lfs'):return hashlib.sha256(data).hexdigest()==item['lfs']['sha256']
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==item.get('blobId')
