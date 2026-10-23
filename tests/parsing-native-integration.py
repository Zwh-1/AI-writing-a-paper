from pathlib import Path
import sys,os,io,json,uuid,faulthandler
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'.runtime'/('parse-native-'+uuid.uuid4().hex[:8]);os.environ['PAPER_DATA_DIR']=str(DATA)
os.environ.pop('PAPER_API_KEY',None);os.environ.pop('PAPER_API_KEY_FILE',None)
for folder in ['.runtime/python-deps','.runtime/advanced-deps','backend','tests']:sys.path.insert(0,str(ROOT/folder))
faulthandler.dump_traceback_later(60,exit=True)
from PIL import Image,ImageDraw,ImageFont
from app import db,parsers,tasks,local_ocr
from test_material_parsing import deck

db.init();pid=db.uid();stamp=db.now();db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'本机OCR与ONNX同一工作进程回归',stamp,stamp))
image=Image.new('RGB',(1100,220),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',38);draw.text((20,35),'本机识别必须保留来源，不得遗漏图片中的要求。',font=font,fill='black');raw=io.BytesIO();image.save(raw,format='PNG')
f,_=parsers.register_file(pid,'原生库隔离测试.pptx',deck(raw.getvalue()),'rules')
parse=tasks.enqueue(pid,'parse',{'file_id':f['id']});tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(parse['id'],));assert row['status']=='succeeded',row.get('error');assert '图片中的要求' in db.one('SELECT text FROM pages')['text']
assert not any(name.startswith('winrt.windows.media.ocr') for name in sys.modules),'WinRT leaked into the research task process'
print('Actual OCR child process passed; starting local ONNX inference in the same task process.',flush=True)
index=tasks.enqueue(pid,'advanced_index',{});tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(index['id'],));assert row['status']=='succeeded',row.get('error')
count=db.one('SELECT COUNT(*) n FROM neural_vectors')['n'];assert count>=2
report={'passed':True,'version':'0.3.1','actual_windows_ocr_child':True,'actual_local_onnx_after_ocr_in_same_worker':True,'winrt_loaded_in_main_worker':False,'neural_vectors':count,'external_model_requests':0,'tested_at':db.now()}
(ROOT/'docs/parsing-native-integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2));faulthandler.cancel_dump_traceback_later()
