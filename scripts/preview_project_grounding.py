"""Preview actual snapshot reads without writing registries or calling a model."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import runner  # noqa: E402,F401
from app import db,project_grounding  # noqa: E402
from app.writing_agent import evidence,repository  # noqa: E402
from app.writing_agent.common import digest  # noqa: E402


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=ROOT/'data/workbench.sqlite3')
    parser.add_argument('--project')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve()==args.database.resolve() or args.output.suffix.lower()!='.json':
        parser.error('Output must be a JSON report separate from the database')
    connection=sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    db.one=lambda query,params=(): next((dict(row) for row in connection.execute(query,params)),None)
    db.rows=lambda query,params=(): [dict(row) for row in connection.execute(query,params)]
    def no_write(*unused,**kwargs):raise RuntimeError('Read-only preview forbids registry writes')
    db.execute=no_write
    projects=db.rows('SELECT id FROM projects'+(' WHERE id=?' if args.project else ''),(args.project,) if args.project else ())
    if len(projects)!=1:parser.error('Choose exactly one existing project with --project')
    pid=projects[0]['id']
    head=db.one('SELECT target_snapshot_id FROM writing_project_state WHERE project_id=?',(pid,))
    if not head or not head['target_snapshot_id']:parser.error('Project has no bound snapshot')
    snapshot=head['target_snapshot_id']
    def simulate_registration(project_id,body):
        row=body['locator']
        raw=repository.read(project_id,body['origin_id'],row['path'],row['line_start'],row['line_end'],remote=True)
        return {'id':'preview-'+digest(row)[:24],'kind':body['kind'],'origin_type':'code','origin_id':body['origin_id'],
                'locator_json':json.dumps(row),'payload':raw,'conditions':body['conditions']}
    evidence.create=simulate_registration
    results=[]
    for kind,title in [('requirements','第三章 需求分析'),('system_design','第四章 系统设计'),
                       ('implementation','第五章 系统实现'),('results','第六章 系统测试与结果分析')]:
        dossier,_=project_grounding.collect(pid,snapshot,{'kind':kind,'title':title},title)
        results.append({'section':title,'coverage':dossier['coverage'],'source_binding_kind':dossier['source_binding_kind'],
            'readings':[{key:row[key] for key in ('path','categories','line_start','line_end','file_sha256')}
                        for row in dossier['readings']],
            'questions':[{key:row[key] for key in ('id','question','status')} for row in dossier['questions']],
            'gaps':dossier['gaps']})
    result={'snapshot_id':snapshot,
            'scope':'Actual snapshot bytes read locally. Evidence registration simulated in memory; no settings, source, draft or database writes. No model call or semantic certification.',
            'sections':results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for row in results:
        print(json.dumps({'section':row['section'],'coverage':row['coverage'],
                          'paths':[reading['path'] for reading in row['readings']]},ensure_ascii=False))


if __name__=='__main__':main()
