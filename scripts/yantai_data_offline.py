"""Only reads SQLite snapshots; never imports the application or credentials."""
import hashlib,json,sqlite3,sys,tempfile,zipfile
from pathlib import Path

def run(args):
    source=Path(args['snapshot']).resolve()
    with tempfile.TemporaryDirectory(prefix='yantai-offline-') as temporary:
        if source.is_dir():database=source/'workbench.sqlite3'
        elif source.suffix=='.zip':
            with zipfile.ZipFile(source) as archive:
                manifest=json.loads(archive.read('manifest.json'));expected=manifest.get('files',{}).get('workbench.sqlite3')
                info=archive.getinfo('workbench.sqlite3')
                if not expected or info.file_size>500*1024*1024:raise ValueError('离线备份缺少可校验数据库或数据库过大')
                raw=archive.read(info)
                if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('离线数据库哈希不匹配')
                database=Path(temporary)/'workbench.sqlite3';database.write_bytes(raw)
        else:raise ValueError('请提供完整备份ZIP或包含 workbench.sqlite3 的数据目录')
        connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
        try:
            rows=lambda sql,parameters=():[dict(row) for row in connection.execute(sql,parameters)]
            projects=rows('SELECT id,title,created_at,updated_at FROM projects WHERE archived=0');pid=args.get('project')
            if args['command']=='list' and args.get('section')=='projects':return projects
            if not pid:
                if len(projects)!=1:raise ValueError('请用 --project 指定目标项目')
                pid=projects[0]['id']
            if not any(row['id']==pid for row in projects):raise ValueError('项目不存在')
            import importlib.util
            spec=importlib.util.spec_from_file_location('query_projection',Path(__file__).with_name('yantai_query_projection.py'))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            return module.query(connection,pid,args)
        finally:connection.close()

if __name__=='__main__':
    try:print(json.dumps(run(json.loads(sys.argv[1])),ensure_ascii=False))
    except Exception as error:print(str(error),file=sys.stderr);raise SystemExit(1)
