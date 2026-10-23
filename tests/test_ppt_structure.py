import io,zipfile,json
from test_workbench import client,project,file
from app import db,security,ppt_objects,parsers

def ppt_fixture():
 parts={
 'ppt/presentation.xml':'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/></p:presentation>',
 'ppt/_rels/presentation.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>',
 'ppt/slides/slide1.xml':'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:d="http://schemas.openxmlformats.org/drawingml/2006/diagram"><p:cSld><p:spTree><c:chart r:id="chart"/><d:relIds r:dm="diagram"/></p:spTree></p:cSld></p:sld>',
 'ppt/slides/_rels/slide1.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="chart" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/><Relationship Id="orphan" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/unused.xml"/><Relationship Id="diagram" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData" Target="../diagrams/data1.xml"/></Relationships>',
 'ppt/charts/chart1.xml':'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:ser><c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>实验组</c:v></c:pt></c:strCache></c:strRef></c:tx><c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>12.5</c:v></c:pt><c:pt idx="1"><c:v>27</c:v></c:pt></c:numCache></c:numRef></c:val></c:ser></c:chartSpace>',
 'ppt/charts/unused.xml':'<unused/>',
 'ppt/diagrams/data1.xml':'<d:dataModel xmlns:d="http://schemas.openxmlformats.org/drawingml/2006/diagram" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><d:ptLst><d:pt modelId="A"><a:t>需求</a:t></d:pt><d:pt modelId="B"><a:t>设计</a:t></d:pt></d:ptLst><d:cxnLst><d:cxn srcId="A" destId="B" type="parOf"/></d:cxnLst></d:dataModel>'}
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w') as z:
  for name,text in parts.items():z.writestr(name,text)
 return b.getvalue()

def test_active_chart_cache_and_smartart_graph_are_read_without_activation(client):
 p=project(client);f,_=parsers.register_file(p,'graph.pptx',ppt_fixture(),'research');items=ppt_objects.objects_on_page(f,1);assert len(items)==2;chart=next(x for x in items if x['kind']=='chart_cache');assert [v['value'] for v in chart['series'][0]['val']]==['12.5','27'];diagram=next(x for x in items if x['kind']=='smart_art');assert diagram['connections']==[{'from':'A','to':'B','type':'parOf'}];assert all(x['review_state']=='pending' for x in items)

def test_ocr_columns_are_only_geometric_candidates(client):
 p=project(client);f=file(client,p,'表格OCR样例');page=db.one('SELECT id FROM pages');lines=[]
 for row in range(3):lines.append({'words':[{'text':str(row),'box':{'x':10,'y':row*40,'width':15,'height':20}},{'text':'数据','box':{'x':250,'y':row*40,'width':40,'height':20}}]})
 sid=db.uid();db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,f['id'],page['id'],db.dumps({'recognition':[{'lines':lines}]}),'fixture','fixture','evidence','windows_ocr',0))
 # More words make the repeated column layout independently observable.
 for line in lines:
  line['words'].append({'text':'单位','box':{'x':380,'y':line['words'][0]['box']['y'],'width':40,'height':20}})
 db.execute('UPDATE sources SET locator=? WHERE id=?',(db.dumps({'recognition':[{'lines':lines}]}),sid));tables=ppt_objects.ocr_tables(page['id']);assert len(tables)==1;assert len(tables[0]['rows'])==3 and tables[0]['requires_human_review'];assert db.one('SELECT review_state FROM pages')['review_state']=='unreviewed'
