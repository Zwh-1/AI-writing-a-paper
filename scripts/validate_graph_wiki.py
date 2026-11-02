"""Audit immutable user records and actual knowledge coverage, without AI calls."""
import argparse,hashlib,json,sqlite3,runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,config

def fingerprint(connection,table):
    digest=hashlib.sha256();count=0
    for row in connection.execute('SELECT * FROM '+table+' ORDER BY rowid'):
        digest.update(json.dumps(tuple(row),ensure_ascii=False,default=str,separators=(',',':')).encode());digest.update(b'\n');count+=1
    return {'count':count,'sha256':digest.hexdigest()}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--before',type=Path);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    c=sqlite3.connect(Path(db.DB_PATH).as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    protected=['files','sources','pages','bibliography','sections','revisions','citations','rules','facts','artifacts'];current={t:fingerprint(c,t) for t in protected};baseline={}
    if args.before:
        before=sqlite3.connect(args.before.resolve().as_uri()+'?mode=ro',uri=True)
        try:baseline={t:fingerprint(before,t) for t in protected}
        finally:before.close()
    tables=['knowledge_observations','kg_entities','kg_assertions','wiki_pages','wiki_versions','kg_support_assessments','knowledge_evaluation_cases','knowledge_reviews','knowledge_claim_checks','document_readings']
    result={'database':str(db.DB_PATH),'schema_version':c.execute('PRAGMA user_version').fetchone()[0],'counts':{t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in tables},
        'foreign_key_errors':[tuple(r) for r in c.execute('PRAGMA foreign_key_check')],
        'protected_tables':current,'before':baseline,'unchanged':{t:current[t]==baseline[t] for t in protected} if baseline else {},
        'observations_by_scope':[dict(r) for r in c.execute('SELECT namespace,COUNT(*) n,COUNT(DISTINCT file_id) files FROM knowledge_observations GROUP BY namespace')],
        'assertions_by_status':[dict(r) for r in c.execute('SELECT namespace,method,lifecycle,COUNT(*) n FROM kg_assertions GROUP BY namespace,method,lifecycle')],
        'wiki_by_type':[dict(r) for r in c.execute('SELECT namespace,kind,COUNT(*) n FROM wiki_pages GROUP BY namespace,kind')],
        'human_model_checks':[dict(r) for r in c.execute('SELECT method,support,COUNT(*) n FROM kg_support_assessments GROUP BY method,support')],
        'evaluation_gold':[dict(r) for r in c.execute('SELECT state,COUNT(*) n FROM knowledge_evaluation_cases GROUP BY state')],
        'billing':[dict(r) for r in c.execute("SELECT status,SUM(budget_charge) charge FROM generation_budget_usage GROUP BY status")],
        'semantic_quality_verified':False,'note':'记录数量、结构与指纹不替代真实论文或人工语义验收；计费预留不等于实际已扣费'}
    c.rollback();c.close();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('schema_version','counts','unchanged','foreign_key_errors','evaluation_gold')},ensure_ascii=False));return 0 if not result['foreign_key_errors'] else 1
if __name__=='__main__':raise SystemExit(main())
