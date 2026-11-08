import pytest
from test_workbench import client,project
from app.bibliography_format import render,legacy
from app import academic_standards,db


def test_journal_volume_issue_and_web_access_are_retained():
    journal=render({'authors':['甲','乙'],'title':'题名','type':'J','journal':'期刊','year':2026,'volume':12,'issue':4,'pages':'1-9'})
    assert '12(4): 1-9' in journal['text'] and not journal['issues']
    web=render({'authors':'机构','title':'网页','type':'EB/OL','publication_date':'2026-10-01','access_date':'2026-11-07','url':'https://example.test/page'})
    assert '2026-10-01[2026-11-07]' in web['text'] and 'https://example.test/page' in web['text']
    assert web['source_authenticity']=='not_assessed'


@pytest.mark.parametrize('kind,extra,fragment',[
    ('M',{'place':'北京','publisher':'出版社','edition':'2版'},'北京: 出版社'),
    ('D',{'place':'南京','publisher':'某大学'},'[D]'),
    ('C',{'container_title':'会议论文集','place':'上海','publisher':'出版社'},'//会议论文集'),
    ('S',{'standard_number':'GB/T 7714—2025'},'GB/T 7714—2025'),
])
def test_supported_types_and_original_data(kind,extra,fragment):
    meta={'authors':['作者'],'title':'题名','type':kind,'year':2026,**extra}
    result=render(meta)
    assert fragment in result['text'] and result['normalized']['original']==meta
    assert render(meta,'legacy')['text']==legacy(meta)


def test_raw_format_is_preserved_without_claiming_verified_and_corrections_are_separate():
    raw=render({'citation_raw':'自定义 著录格式'})
    assert raw['text']=='自定义 著录格式' and raw['format_status']=='needs_review'
    meta={'authors':'作者','title':'旧题名','type':'J','year':2026,'journal':'刊名','corrections':{'title':'更正题名'}}
    result=render(meta)
    assert '更正题名' in result['text'] and meta['title']=='旧题名'


def test_standard_api_conflicts_preview_and_snapshot(client):
    pid=project(client)
    body={'bibliography_version':'gbt7714_2025','conflicts':[{'id':'one','statement':'编号要求冲突','sources':['学校条文','院系条文']}]}
    result=client.put(f'/api/v1/projects/{pid}/academic-standards',json=body)
    assert result.status_code==200,result.text
    preview=client.post(f'/api/v1/projects/{pid}/bibliography/preview',json={'metadata':{'title':'缺项文章','type':'J'}})
    assert preview.status_code==200 and preview.json()['format_status']=='needs_correction'
    assert academic_standards.report([],academic_standards.profile(pid))['unresolved_conflicts']
    from app.export_checks import freeze
    snapshot=freeze(pid)
    client.put(f'/api/v1/projects/{pid}/academic-standards',json={'bibliography_version':'legacy'})
    assert snapshot['settings']['academic_standards:'+pid]['bibliography_version']=='gbt7714_2025'
