"""Render an isolated full thesis to check figure and native-table export layout."""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'.runtime'/'python-deps'),str(ROOT/'.runtime'/'advanced-deps'),str(ROOT/'backend')]

if __name__ == '__main__':
    folder = ROOT/'.runtime'/'paper-visuals-qa'
    fixture = json.loads((folder/'fixture.json').read_text(encoding='utf-8'))
    os.environ['PAPER_DATA_DIR'] = fixture['directory']
    os.environ['PAPER_PORT'] = str(fixture['port'])
    import pypdfium2 as pdfium
    from app import db,documents,exports,security
    from app.paper_visuals import create_visual,TEMPLATES
    from app.office_renderer import render_path
    pid,sid = fixture['project_id'],fixture['section_id']
    section = security.require_section(sid)
    current = documents.current(section)
    content = [documents.text_document('本节展示隔离验收项目的流程、参与者、数据实体与字段。')['content'][0]]
    for kind in ('three_line_table','flowchart','use_case','er'):
        content.append(create_visual(pid,{'spec':TEMPLATES[kind]})['node'])
    documents.save(section,{'type':'doc','content':content},current['id'])
    result = exports.export_project(pid,{'mode':'draft','pdf':False})
    source = exports.DATA/'exports'/result['export_id']/'论文.docx'
    shutil.copyfile(source,folder/'thesis.docx')
    receipt = render_path(folder/'thesis.docx',folder/'thesis.pdf','docx')
    pdf = pdfium.PdfDocument(str(folder/'thesis.pdf'))
    paths = []
    for i in range(len(pdf)):
        page = pdf[i]
        bitmap = page.render(scale=1.7)
        path = folder/f'word-thesis-page-{i+1}.png'
        bitmap.to_pil().save(path)
        bitmap.close();page.close();paths.append(str(path))
    pdf.close()
    (folder/'word-render-report.json').write_text(db.dumps({'receipt':receipt,'pages':paths,'synthetic_data':True}),encoding='utf-8')
    print(db.dumps({'pages':len(paths),'engine':receipt.get('engine'),'synthetic_data':True}))
