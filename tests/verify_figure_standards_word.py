"""Native Word layout QA of chapter captions, REF fields and diagram directories."""
import json,os,sys,shutil,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'.runtime'/'python-deps'),str(ROOT/'.runtime'/'advanced-deps'),str(ROOT/'backend')]
if __name__=='__main__':
    fixture=json.loads((ROOT/'.runtime/paper-visuals-qa/fixture.json').read_text(encoding='utf-8'))
    os.environ['PAPER_DATA_DIR']=fixture['directory'];os.environ['PAPER_PORT']=str(fixture['port'])
    from app import db,documents,security,exports
    from app.paper_visuals import TEMPLATES,create_visual,explanation_draft
    from app.figure_standards import save_policy
    from app.office_renderer import render_path
    import pypdfium2 as pdfium
    folder=ROOT/'.runtime/figure-standards-qa';folder.mkdir(exist_ok=True)
    pid=db.uid();stamp=db.now();db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'论文配图规范导出验收',stamp,stamp))
    sids=[]
    for position,title in enumerate(('第三章 系统设计','第四章 系统实现')):
        sid=db.uid();sids.append(sid);db.execute('INSERT INTO sections(id,project_id,title,kind,position) VALUES(?,?,?,?,?)',(sid,pid,title,'system_design' if not position else 'implementation',position))
    save_policy(pid,{'policy':{'numbering':'chapter','caption_space':False,'include_directory':True,'ai_image_policy':'forbidden'}})
    content=[];first=None
    for kind in ('architecture','module','use_case','flowchart','er','class_diagram','data_flow','contract_interaction','circuit','structure'):
        explanation=explanation_draft(pid,{'spec':TEMPLATES[kind]})['explanation']
        asset=create_visual(pid,{'spec':TEMPLATES[kind],'explanation':explanation})
        content+=asset['insert_nodes']
        if first is None:first=asset
    table=create_visual(pid,{'spec':TEMPLATES['three_line_table']});content+=table['insert_nodes']
    documents.save(security.require_section(sids[0]),{'type':'doc','content':content},None)
    asset=create_visual(pid,{'spec':TEMPLATES['sequence'],'explanation':explanation_draft(pid,{'spec':TEMPLATES['sequence']})['explanation']})
    content=asset['insert_nodes']+[{'type':'paragraph','content':[{'type':'text','text':'总体设计参见'},{'type':'crossReference','attrs':{'targetSectionId':sids[0],'targetId':first['node']['attrs']['nodeId']}}]}]
    documents.save(security.require_section(sids[1]),{'type':'doc','content':content},None)
    result=exports.export_project(pid,{'mode':'draft','pdf':False});source=exports.DATA/'exports'/result['export_id']/'论文.docx'
    shutil.copyfile(source,folder/'chapter-figures.docx');receipt=render_path(folder/'chapter-figures.docx',folder/'chapter-figures.pdf','docx')
    pdf=pdfium.PdfDocument(str(folder/'chapter-figures.pdf'));texts=[];images=[]
    for i in range(len(pdf)):
        page=pdf[i];text=page.get_textpage();texts.append(text.get_text_range());text.close()
        bitmap=page.render(scale=1.4);image=folder/f'chapter-figures-page-{i+1}.png';bitmap.to_pil().save(image);bitmap.close();page.close();images.append(str(image))
    pdf.close();full='\n'.join(texts);(folder/'word-text.txt').write_text(full,encoding='utf-8')
    assert '插图目录' in full and '表格目录' in full
    normalized=re.sub(r'([图表])\s+(\d)',r'\1\2',full)
    assert '图3-1' in normalized and '图4-1' in normalized and '表3-1' in normalized
    assert '未定义书签' not in full and 'Error!' not in full
    assert '总体设计参见图3-1' in re.sub(r'\s+','',normalized)
    (folder/'word-report.json').write_text(db.dumps({'receipt':receipt,'pages':images,'synthetic_data':True,'chapter_labels_verified':True,'directories_rendered':True,'cross_reference_verified':True}),encoding='utf-8')
    print(db.dumps({'pages':len(images),'engine':receipt.get('engine'),'chapter_labels_verified':True,'directories_rendered':True}))
