"""Read-only preview of real chapter materials and deterministic diagrams."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import runner  # noqa: E402,F401
from app import db,chapter_materials  # noqa: E402
from app.writing_agent import evidence,repository  # noqa: E402
from app.writing_agent.common import digest  # noqa: E402
from app.paper_visual_render import render_diagram  # noqa: E402
from app.writing_visuals import _check_physical_er  # noqa: E402


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--project')
    args=parser.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect((ROOT/'data/workbench.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    db.one=lambda sql,params=(): next((dict(row) for row in connection.execute(sql,params)),None)
    db.rows=lambda sql,params=(): [dict(row) for row in connection.execute(sql,params)]
    def deny(*args,**kwargs):raise RuntimeError('Preview cannot write project records')
    db.execute=deny
    projects=db.rows('SELECT id FROM projects'+(' WHERE id=?' if args.project else ''),(args.project,) if args.project else ())
    if len(projects)!=1:parser.error('Select exactly one project with --project')
    pid=projects[0]['id'];snapshot=db.one('SELECT target_snapshot_id FROM writing_project_state WHERE project_id=?',(pid,))['target_snapshot_id']
    paths=[row['relative_path'] for row in db.rows('SELECT relative_path FROM repository_snapshot_files WHERE snapshot_id=?',(snapshot,))]
    def registration(project_id,body):
        loc=body['locator'];raw=repository.read(project_id,snapshot,loc['path'],loc['line_start'],loc['line_end'],remote=True)
        return {'id':'preview-'+digest(loc)[:24],'kind':body['kind'],'origin_type':'code','origin_id':snapshot,
                'payload':raw,'conditions':body['conditions'],'locator_json':json.dumps(loc)}
    evidence.create=registration
    results=[];report=['# 第三至第六章真实项目图表材料预览','',
        '此预览读取当前绑定快照的实际源码，未修改用户数据库、论文或绑定配置。证据登记仅在内存模拟；不是付费模型生成的论文，也不证明部署或测试执行。','']
    errors=[]
    def example(plan,materials):
        ids=set(plan['material_ids'])
        tables=[row for row in materials['database_tables'] if row['id'] in ids]
        if plan['spec']['kind']=='er':
            relationships=[f"{row['name']}.{','.join(fk['columns'])} 引用 {fk['target_table']}.{','.join(fk['target_columns'])}"
                           for row in tables for fk in row['foreign_keys'] if fk['target_table'] in {t['name'] for t in tables}]
            return '图中关系来自已读建表定义：'+'；'.join(relationships)+'。这些引用约束说明声明层的记录关联；未读取到的关系没有补画，图中仅列核心字段。'
        if tables:
            table=tables[0];fields=table['fields']
            key_fields=[field for field in fields if any(key in field['constraints'] for key in ('PK','FK','UNIQUE'))][:4]
            named='；'.join(field['name']+' 声明 '+('/'.join(field['constraints']) or field['data_type']) for field in key_fields)
            constraints=plan['spec'].get('note','')
            return f"{table['name']} 以关键标识及约束组织记录，表中选列 {len(plan['spec']['rows'])} 个字段。{named}。"+(constraints if constraints else '')
        pages=[row for row in materials['frontend_pages'] if row['id'] in ids]
        if pages:
            statements=[]
            for page in pages:
                if plan['spec']['kind']=='three_line_table':
                    statements.append('“'+(page.get('labels') or [page['route']])[0]+'”入口对应的界面文案包括'+'、'.join(page.get('labels',[])[1:4]))
                    continue
                names='、'.join(row['name'] for row in page['components']) or '本范围尚未解析的模块'
                calls='、'.join(row['symbol'] for row in page.get('api_calls',[]))
                statements.append(page['route']+' 在源码中导入 '+names+('，已读调用点包括 '+calls if calls else ''))
            if plan['spec']['kind']=='three_line_table':return '；'.join(statements)+'。这些要素可用于说明功能入口与操作位置；响应条件、角色权限和异常处置仍需结合处理逻辑分析。'
            return '；'.join(statements)+'。图中连接表示页面与实现模块、调用符号之间的静态关系，执行先后和处理结果需另据控制逻辑及运行记录核对。'
        cases=[row for row in materials['test_cases'] if row['id'] in ids]
        if cases:
            return '测试定义包括“'+'”“'.join(row['name'] for row in cases[:3])+'”。这些名称指出了用例关注的条件，断言方法及skip/todo状态在表中分别保留；未提供对应执行记录，因此不填写通过率或成功结论。'
        routes=[row for row in materials['api_routes'] if row['id'] in ids]
        if routes:return '已读路由声明包括 '+'、'.join(row['method']+' '+row['route'] for row in routes)+'。这些条目用于对照接口分工；未拼接挂载前缀，不能作为完整部署地址或执行结果。'
        return plan['paragraph_task']
    for kind,title in [('requirements','第三章 需求分析'),('system_design','第四章 系统设计'),
                       ('implementation','第五章 系统实现'),('results','第六章 系统测试与结果分析')]:
        materials,items=chapter_materials.collect(pid,snapshot,{'kind':kind,'title':title},paths,
                                             'student credential profile application',max_bytes=24000,max_ranges=14)
        results.append({'chapter':title,'materials':materials})
        report.extend(['## '+title,'',
            f"实际结构条目：数据库表 {len(materials['database_tables'])}；页面 {len(materials['frontend_pages'])}；路由 {len(materials['api_routes'])}；测试定义 {len(materials['test_cases'])}。",''])
        for plan in materials['visual_plans']:
            spec=plan['spec'];report.extend(['### '+spec['title'],'',plan['paragraph_task'],''])
            if spec['kind']=='three_line_table':
                escape=lambda value:str(value).replace('|','\\|').replace('\n',' ')
                report.append('| '+' | '.join(map(escape,spec['headers']))+' |')
                report.append('| '+' | '.join('---' for _ in spec['headers'])+' |')
                report.extend('| '+' | '.join(map(escape,row))+' |' for row in spec['rows'])
                report.extend(['',spec.get('note',''),''])
            else:
                try:
                    _check_physical_er(spec,[item for item in items if item['id'] in plan['evidence_ids']])
                    data,_=render_diagram(spec);image=out/(plan['id']+'.png');image.write_bytes(data)
                    report.extend(['!['+spec['title']+']('+image.as_posix()+')',''])
                except ValueError as error:
                    errors.append({'chapter':title,'plan_id':plan['id'],'reason':str(error)})
                    report.extend(['渲染未完成：'+str(error),''])
            related=[row for lane in ('database_tables','frontend_pages','api_routes','test_cases') for row in materials[lane] if row['id'] in plan['material_ids']]
            report.extend(['**依据图表组织的说明示例（本地静态整理，非模型验收稿）**', '',example(plan,materials),''])
            source_ranges=[r for r in materials['readings'] if r['evidence_id'] in plan['evidence_ids']]
            report.extend(['来源：'+'；'.join(dict.fromkeys(r['path']+':'+str(r['line_start'])+'–'+str(r['line_end']) for r in source_ranges)),''])
        report.extend(['已登记缺口：'+str(len(materials['gaps']))+' 项。本预览受材料预算限制，不是全项目结构清单，缺口为零也不表示已读完整项目。',''])
        print(json.dumps({'chapter':title,'tables':[row['name'] for row in materials['database_tables']],
                          'pages':[row['route'] for row in materials['frontend_pages']],
                          'test_names':[row['name'] for row in materials['test_cases']],
                          'visual_plans':len(materials['visual_plans']),'read_scope':materials['coverage']},ensure_ascii=False))
    (out/'preview.json').write_text(json.dumps({'snapshot_id':snapshot,'scope':'Read-only source preview; no real model call or project writes.',
                                               'chapters':results,'render_errors':errors},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out/'preview.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print(json.dumps({'render_errors':errors,'report':str(out/'preview.md')},ensure_ascii=False))


if __name__=='__main__':main()
