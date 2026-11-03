"""Persistent Surya CLI job service with a layout/recognition normalization contract."""
import json,os,secrets,subprocess,tempfile,threading,uuid,shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import FastAPI,File,UploadFile,Header,HTTPException
from fastapi.responses import FileResponse

ROOT=Path(os.environ.get('SURYA_DATA_ROOT','/data/surya')).resolve();ROOT.mkdir(parents=True,exist_ok=True)
TOKEN=os.environ.get('PAPER_PARSE_TOKEN','');pool=ThreadPoolExecutor(max_workers=int(os.environ.get('SURYA_WORKERS','1')))
app=FastAPI(title='Yantai Surya layout provider')

def auth(value):
    if TOKEN and not secrets.compare_digest(value or '','Bearer '+TOKEN):raise HTTPException(401,'Invalid parsing credential')
def folder(identity):
    if len(identity)!=32 or any(char not in '0123456789abcdef' for char in identity):raise HTTPException(404,'Job not found')
    return ROOT/identity
def write(identity,value):
    target=folder(identity)/'job.json';temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(value),encoding='utf-8');os.replace(temporary,target)

def normalize_results(layout,ocr):
    def bbox(item):
        if item.get('bbox'):return item['bbox']
        points=item.get('polygon',[])
        return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)] if points else None
    blocks=[]
    for index,page in enumerate(layout):
        recognised=ocr[index].get('text_lines',[]) if index<len(ocr) else [];regions=page.get('bboxes',page.get('boxes',[]));assigned=set()
        ordered=sorted(enumerate(regions),key=lambda item:item[1].get('position',item[0]))
        for order,region in ordered:
            box=bbox(region)
            if box is None:continue
            lines=[]
            for number,line in enumerate(recognised):
                bounds=bbox(line)
                if number not in assigned and bounds and box[0]<=(bounds[0]+bounds[2])/2<=box[2] and box[1]<=(bounds[1]+bounds[3])/2<=box[3]:lines.append((bounds[1],bounds[0],line.get('text','')));assigned.add(number)
            text='\n'.join(item[2] for item in sorted(lines))
            if text.strip():blocks.append({'page':index+1,'type':region.get('label','paragraph'),'text':text,'bbox':box,'order':region.get('position',order),'coordinate_system':'image_pixels_top_left','page_size':page.get('image_bbox',[0,0,0,0])[2:],'confidence':region.get('confidence')})
        for number,line in enumerate(recognised):
            if number not in assigned and line.get('text','').strip():blocks.append({'page':index+1,'type':'paragraph','text':line['text'],'bbox':bbox(line),'order':len(regions)+number,'coordinate_system':'image_pixels_top_left','confidence':line.get('confidence')})
    for index,page in enumerate(ocr[len(layout):],len(layout)):
        for order,line in enumerate(page.get('text_lines',[])):
            if line.get('text','').strip():blocks.append({'page':index+1,'type':'paragraph','text':line['text'],'bbox':bbox(line),'order':order,'coordinate_system':'image_pixels_top_left'})
    return blocks

def process(identity):
    target=folder(identity)
    try:
        write(identity,{'job_id':identity,'status':'processing'})
        for tool in ('surya_layout','surya_ocr'):
            output=target/tool;output.mkdir(exist_ok=True)
            result=subprocess.run([tool,str(target/'document.pdf'),'--output_dir',str(output)],capture_output=True,timeout=3600)
            if result.returncode:raise RuntimeError(tool+' failed: '+result.stderr.decode(errors='replace')[-1000:])
        def read_result(tool):
            files=list((target/tool).rglob('results.json'))
            if not files:raise ValueError(tool+' returned no results.json')
            value=json.loads(files[0].read_text(encoding='utf-8'));return next(iter(value.values())) if isinstance(value,dict) else value
        layout=read_result('surya_layout');ocr=read_result('surya_ocr');blocks=normalize_results(layout,ocr)
        value={'blocks':blocks,'engine':'surya-cli','human_reviewed':False};(target/'result.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');write(identity,{'job_id':identity,'status':'completed'})
    except Exception as error:write(identity,{'job_id':identity,'status':'failed','error':str(error)[:1500]})

@app.get('/health')
def health():return {'ready':bool(shutil.which('surya_layout') and shutil.which('surya_ocr')),'engine':'surya-cli','layout_available':bool(shutil.which('surya_layout')),'ocr_available':bool(shutil.which('surya_ocr'))}
@app.post('/parse')
async def parse(file:UploadFile=File(...),authorization:str=Header(default='')):
    auth(authorization);raw=await file.read(120*1024*1024+1)
    if len(raw)>120*1024*1024 or not raw.lstrip().startswith(b'%PDF-'):raise HTTPException(422,'A PDF up to 120MB is required')
    identity=uuid.uuid4().hex;target=folder(identity);target.mkdir();(target/'document.pdf').write_bytes(raw);write(identity,{'job_id':identity,'status':'queued'});pool.submit(process,identity)
    return {'job_id':identity,'status':'queued'}
@app.get('/jobs/{identity}')
def status(identity:str,authorization:str=Header(default='')):
    auth(authorization);path=folder(identity)/'job.json'
    if not path.exists():raise HTTPException(404,'Job not found')
    return json.loads(path.read_text(encoding='utf-8'))
@app.get('/jobs/{identity}/result')
def result(identity:str,authorization:str=Header(default='')):
    auth(authorization);path=folder(identity)/'result.json'
    if not path.exists():raise HTTPException(409,'Result not ready')
    return FileResponse(path,media_type='application/json')
