from test_workbench import client,project,file
from app import db,rule_governance as governance


def seed_rules(client):
    pid=project(client);f=file(client,pid,'摘要必须交代研究方法。\n论文必须记录真实数据。\n结论必须回应研究问题。',role='rules')
    source=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    for text in source['text'].splitlines():
        db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(db.uid(),pid,source['id'],text,'pending',db.now()))
    governance.review_project(pid)
    return pid,source,db.rows("SELECT * FROM rules WHERE project_id=? AND state='machine_verified'",(pid,))


def test_bulk_validity_matches_single_rule_checks_with_constant_queries(client,monkeypatch):
    pid,source,items=seed_rules(client)
    assert len(items)==3
    expected={r['id']:governance.machine_valid(r) for r in items}
    original=db.rows;queries=[]
    def counted(sql,args=()):queries.append(sql);return original(sql,args)
    monkeypatch.setattr(db,'rows',counted)
    assert governance.machine_validity(pid,items)==expected
    assert len(queries)==2
    assert all(expected.values())


def test_bulk_validity_does_not_accept_changed_rules_or_stale_source(client):
    pid,source,items=seed_rules(client)
    changed={**items[0],'statement':'论文必须编造数据。'}
    assert not governance.machine_validity(pid,[changed])[changed['id']]
    db.execute('UPDATE sources SET text=? WHERE id=?',('来源已改变',source['id']))
    validity=governance.machine_validity(pid,items)
    assert not any(validity.values())
    assert validity=={r['id']:governance.machine_valid(r) for r in items}


def test_latest_decision_lookup_respects_restored_history(client):
    pid,source,items=seed_rules(client);rule=items[0]
    previous=governance.current_decisions(pid)[rule['id']]
    governance.record(pid,rule,rule,{'id':source['id'],'text':source['text'],'method':source['method']},'needs_resolution',['后续判定'])
    latest=governance.current_decisions(pid)[rule['id']]
    assert latest['id']>previous['id'] and latest['outcome']=='needs_resolution'
    db.execute('UPDATE rule_decisions SET restored=1 WHERE id=?',(latest['id'],))
    assert governance.current_decisions(pid)[rule['id']]==previous
