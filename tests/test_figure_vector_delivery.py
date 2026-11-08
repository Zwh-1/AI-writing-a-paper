import io
import copy
import pytest
from lxml import etree
from PIL import Image
from test_workbench import client,project
from app import db,security
from app.paper_visuals import TEMPLATES
from app.visual_delivery import quality,render_vector


@pytest.mark.parametrize('kind',['flowchart','use_case','er'])
def test_vectors_share_source_and_have_real_text_and_geometry(client,kind):
    pid=project(client)
    response=client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':copy.deepcopy(TEMPLATES[kind]),'delivery_spec':{'width_mm':130}})
    assert response.status_code==200,response.text
    row=response.json();meta=row['metadata']
    assert meta['svg_sha256'] and meta['delivery_spec']['width_mm']==130
    svg=client.get(f'/api/v1/projects/{pid}/paper-visuals/{row["id"]}/download?format=svg')
    assert svg.status_code==200,svg.text
    root=etree.fromstring(svg.content)
    assert root.findall('.//{http://www.w3.org/2000/svg}text')
    assert not root.findall('.//{http://www.w3.org/2000/svg}image')
    assert '始' in svg.content.decode() or kind!='flowchart'
    pdf=client.get(f'/api/v1/projects/{pid}/paper-visuals/{row["id"]}/download?format=pdf')
    assert pdf.status_code==200,pdf.text[:100]
    assert pdf.content.startswith(b'%PDF')


def test_effective_resolution_uses_physical_width():
    result=quality({'width':1080,'delivery_spec':{'width_mm':150}})
    assert result['effective_dpi']==182.88 and result['resolution_status']=='fail'
    assert result['visual_verification']=='not_run'


def test_vector_download_uses_immutable_saved_bytes(client,monkeypatch):
    from app import visual_delivery
    pid=project(client)
    asset=client.post(f'/api/v1/projects/{pid}/paper-visuals',json={'spec':TEMPLATES['flowchart']}).json()
    expected=security.safe_object(asset['metadata']['svg_sha256']).read_bytes()
    expected_pdf=security.safe_object(asset['metadata']['pdf_sha256']).read_bytes()
    monkeypatch.setattr(visual_delivery,'render_vector',lambda *a,**k:pytest.fail('saved vector must be reused'))
    monkeypatch.setattr(visual_delivery,'svg_pdf',lambda *a,**k:pytest.fail('saved PDF must be reused'))
    assert client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download?format=svg').content==expected
    assert client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download?format=pdf').content==expected_pdf
    record=client.get(f'/api/v1/projects/{pid}/paper-visuals/{asset["id"]}/download?format=record').json()
    assert record['metadata']['font_provenance']['raster_measurement']['sha256']


@pytest.mark.parametrize('kind',['histogram','line','scatter','bar'])
def test_statistical_outputs_keep_filtering_and_reproducible_data(client,kind):
    pid=project(client)
    response=client.post(f'/api/v1/projects/{pid}/files',files={'file':('values.csv',b'x,y,lower,upper\n1,3,2,4\n2,5,4,6\n3,bad,1,2\n4,,0,1\n','text/csv')})
    assert response.status_code==200,response.text
    fid=response.json()['file']['id']
    body={'file_id':fid,'kind':kind,'column':'y','title':'Measured values','delivery_spec':{'x_label':'x','y_label':'y','width_mm':130}}
    if kind!='histogram':body['x_column']='x';body.update(lower_column='lower',upper_column='upper')
    result=client.post(f'/api/v1/projects/{pid}/charts',json=body)
    assert result.status_code==200,result.text
    chart=result.json();meta=chart['metadata']
    assert meta['n']==2 and meta['missing_or_non_numeric_rows']==2
    assert meta['source_hash'] and meta['svg_sha256'] and meta['pdf_sha256']
    if kind=='histogram':assert sum(meta['data_processing']['histograms']['all']['counts'])==2
    for format in ('png','svg','pdf','source'):
        output=client.get(f'/api/v1/projects/{pid}/charts/{chart["id"]}/download?format={format}')
        assert output.status_code==200,output.text[:100] if format=='source' else output.status_code
