"""Create a local, read-only review aid. Never writes human grades or qualifications."""
import html
import json
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'.runtime/writing-v4-live-data'

def main():
    con=sqlite3.connect((DATA/'workbench.sqlite3').as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
    saved=json.loads(con.execute("SELECT value FROM settings WHERE key='writing_completion_live_v41'").fetchone()[0]);pid=saved['project_id']
    def blob(sha):return json.loads((DATA/'objects'/sha).read_text(encoding='utf-8'))
    def plain(node):return node.get('text','') if node.get('type')=='text' else ('\n\n' if node.get('type')=='doc' else '').join(plain(n) for n in node.get('content',[]))
    records=[]
    for run in con.execute('SELECT * FROM diagnostic_runs WHERE project_id=? ORDER BY created_at',(pid,)):
        policy=json.loads(run['policy_json']);dataset=blob(policy['dataset_blob']) if policy.get('dataset_blob') else json.loads((ROOT/'prompts/writing-diagnostics.v3.json').read_text(encoding='utf-8'));cases={c['id']:c for c in dataset['cases']}
        for item in con.execute('SELECT * FROM diagnostic_items WHERE run_id=? ORDER BY case_id,variant_id,repetition',(run['id'],)):
            case=cases[item['case_id']];variant=next(v for v in case['variants'] if v['name']==item['variant_id']);result=blob(item['result_blob']) if item['result_blob'] else {}
            records.append({'case':item['case_id'],'variant':item['variant_id'],'repetition':item['repetition']+1,'status':item['status'],'instruction':case['prompt'],'context':variant['context'],'answer':result.get('answer',result.get('error','尚未完成')),'standard':case.get('gold'),'human_reviewed':item['human_review_json'] is not None})
    pilots=[]
    for section in con.execute('SELECT * FROM sections WHERE project_id=? ORDER BY position',(pid,)):
        revision=con.execute('SELECT document FROM revisions WHERE id=?',(section['current_revision_id'],)).fetchone() if section['current_revision_id'] else None
        pilots.append({'title':section['title'],'goal':section['goal'],'text':plain(json.loads(revision['document'])) if revision else '该小节尚无采用的正文，具体失败保留在工作台。'})
    human=con.execute("SELECT COUNT(*) FROM diagnostic_reviews WHERE project_id=? AND method='human_reviewed'",(pid,)).fetchone()[0]
    con.close()
    payload=json.dumps({'samples':records,'pilots':pilots,'human_reviews':human},ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>真实验收审阅材料</title><style>
body{font:16px/1.7 system-ui,sans-serif;color:#22342d;background:#f6f5ef;margin:auto;max-width:1050px;padding:24px}h1{font-size:27px}section,article{background:white;padding:20px;border-radius:12px;margin:18px 0;border:1px solid #dce4dd}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f3;padding:14px;border-radius:8px}button,select,input{font:inherit;padding:8px;margin:5px;max-width:100%}.notice{border-left:5px solid #51735e;padding:14px;background:#e9f0e8}.row{display:flex;flex-wrap:wrap;align-items:center;gap:12px}small{color:#596f62}summary{cursor:pointer}label{display:block}</style>
<h1>真实验收审阅材料</h1><p class="notice">本页只提供实际回答、给定材料和评估标准，不代填人工判断。模型及实验组标签在样本列表中隐藏。正式分项评分请在工作台“模型诊断”的盲评入口提交。</p><p id="summary"></p><section><h2>实际项目正文</h2><div id="pilots"></div></section><section><h2>诊断样本</h2><div class="row"><label>场景<select id="case"><option value="">全部</option></select></label><label>状态<select id="status"><option value="">全部</option><option value="completed">已生成</option><option value="failed">失败</option><option value="budget_blocked">预算等待</option><option value="outcome_unknown">远端未知</option></select></label><label>搜索<input id="query" placeholder="检索题目、材料或回答"></label></div><p id="count"></p><div id="records"></div></section><script type="application/json" id="data">PAYLOAD</script><script>
const data=JSON.parse(document.getElementById('data').textContent),byId=id=>document.getElementById(id);function node(tag,text){const n=document.createElement(tag);n.textContent=text;return n}byId('summary').textContent=`已保存 ${data.samples.length} 个样本；实际人工分项记录 ${data.human_reviews} 条。未审阅不能当作通过。`;
for(const p of data.pilots){const a=node('article','');a.append(node('h3',p.title),node('p',p.goal),node('pre',p.text));byId('pilots').append(a)}for(const c of [...new Set(data.samples.map(x=>x.case))]){const o=node('option',c);o.value=c;byId('case').append(o)}function render(){const q=byId('query').value.trim().toLowerCase(),rows=data.samples.filter(s=>(!byId('case').value||s.case===byId('case').value)&&(!byId('status').value||s.status===byId('status').value)&&(!q||JSON.stringify(s).toLowerCase().includes(q)));byId('records').replaceChildren();byId('count').textContent=`显示 ${rows.length} 份回答。`;rows.forEach((s,i)=>{const d=node('details','');d.append(node('summary',`${s.case} · ${s.variant} · 第${s.repetition}次 · ${s.status} · ${s.human_reviewed?'已有人工记录':'未人工评分'}`));const a=node('article','');a.append(node('h3','任务'),node('pre',s.instruction),node('h3','给定材料'),node('pre',typeof s.context==='string'?s.context:JSON.stringify(s.context,null,2)),node('h3','实际回答或错误'),node('pre',s.answer),node('h3','评估侧标准'),node('pre',typeof s.standard==='string'?s.standard:JSON.stringify(s.standard,null,2)));d.append(a);byId('records').append(d)})}for(const id of ['case','status','query'])byId(id).addEventListener('input',render);render();</script></html>'''.replace('PAYLOAD',payload)
    target=ROOT/'docs/writing-agent/真实验收人工审阅材料_0.4.1.html';target.write_text(page,encoding='utf-8')
    print(json.dumps({'artifact':str(target),'samples':len(records),'human_reviews':human,'human_grades_written':0},ensure_ascii=False))

if __name__=='__main__':main()
