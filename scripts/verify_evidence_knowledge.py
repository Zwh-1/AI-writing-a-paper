"""Read-only live smoke check. All requests stay on the local workbench."""
import argparse,http.cookiejar,json,sqlite3,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--repeat',type=int,default=2);parser.add_argument('--query',default='灵魂绑定代币 学历验证');args=parser.parse_args()
if not 1<=args.repeat<=5:raise SystemExit('repeat must be 1..5')
origin=f'http://127.0.0.1:{args.port}'
session=json.loads((ROOT/f'.runtime/session-{args.port}.json').read_text(encoding='utf-8-sig'))
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));csrf=''
def request(path,data=None):
    headers={'Content-Type':'application/json'}
    if csrf:headers['X-CSRF-Token']=csrf
    req=urllib.request.Request(origin+'/api/v1'+path,data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None,headers=headers)
    with opener.open(req,timeout=180) as response:return json.load(response)
csrf=request('/session',{'token':session['bootstrap_token']})['csrf']
health=request('/health');pid=next(p['id'] for p in request('/projects') if not p.get('archived'));overview=request('/projects/'+pid+'/evidence-knowledge')
db_path=Path(session['data_directory'])/'workbench.sqlite3'
with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as c:before=c.execute('SELECT count(*) FROM generation_runs').fetchone()[0]
report={'version':health['version'],'units':overview['total'],'pending':overview['pending'],'indexed_windows':overview['indexed_windows'],'search_windows':overview['search_windows'],'queries':[]}
for number in range(args.repeat):
    started=time.perf_counter();found=request('/projects/'+pid+'/evidence-knowledge/search',{'query':args.query,'limit':5})
    assert found['items']
    hit=next(r for r in found['items'] if r.get('knowledge_unit_id'))
    reading=request('/projects/'+pid+'/evidence-knowledge/units/'+hit['knowledge_unit_id']+'/read',{'before':0,'after':0})
    assert reading['status'] in ('read','needs_narrower_range')
    item={'iteration':number+1,'seconds':round(time.perf_counter()-started,3),'query_items':len(found['items']),'unit_rerank':found.get('unit_pipeline',{}).get('rerank'),'source_rerank':found.get('pipeline',{}).get('rerank'),'timings_ms':found.get('timings_ms'),'reading_status':reading['status'],'reading_complete':reading.get('complete'),'external_requests':found.get('external_requests')}
    report['queries'].append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as c:after=c.execute('SELECT count(*) FROM generation_runs').fetchone()[0]
report['paid_generation_records_unchanged']=before==after
(ROOT/'.runtime/evidence-knowledge-live-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'version':report['version'],'units':report['units'],'indexed_windows':report['indexed_windows'],'paid_generation_records_unchanged':report['paid_generation_records_unchanged']},ensure_ascii=False),flush=True)
