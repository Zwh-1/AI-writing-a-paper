from pathlib import Path
import os,sys,json,io
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'.runtime/parsing-browser-v031';DATA.mkdir(parents=True,exist_ok=True)
os.environ['PAPER_DATA_DIR']=str(DATA)
for folder in ['.runtime/python-deps','.runtime/advanced-deps','backend','tests']:sys.path.insert(0,str(ROOT/folder))
from PIL import Image,ImageDraw,ImageFont
from app import db,parsers,security
from test_material_parsing import deck

db.init();pid=db.uid();stamp=db.now();db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'资料解析回归验收',stamp,stamp))
real=next((ROOT.parent/'格式').glob('*关键词分析*1.pptx'))
f,_=parsers.register_file(pid,real.name,real.read_bytes(),'rules');result=parsers.parse_file(f['id'])
image=Image.new('RGB',(1100,220),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',38);draw.text((20,35),'参考文献必须保留来源，图片要求不得漏读。',font=font,fill='black');buf=io.BytesIO();image.save(buf,format='PNG')
legacy,_=parsers.register_file(pid,'旧版漏识别样例.pptx',deck(buf.getvalue()),'rules')
page=db.uid();sha=security.put_blob(buf.getvalue());db.execute('INSERT INTO pages(id,file_id,page_number,title,text,images,has_visual) VALUES(?,?,?,?,?,?,1)',(page,legacy['id'],1,'论文要求','论文要求',db.dumps([{'sha256':sha,'extension':'.png','name':'image1.png'}])))
db.execute("UPDATE files SET status='ready',pages=1 WHERE id=?",(legacy['id'],))
failed,_=parsers.register_file(pid,'图片损坏样例.pptx',deck(b'not an image'),'rules');parsers.parse_file(failed['id'])
fixture={'project_id':pid,'real_file_id':f['id'],'real_name':real.name,'legacy_file_id':legacy['id'],'legacy_page_id':page,'failed_file_id':failed['id']}
(ROOT/'.runtime/parsing-browser-fixture.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'fixture_ready':True,'actual_file':real.name,'actual_quality':result['quality']},ensure_ascii=False))
