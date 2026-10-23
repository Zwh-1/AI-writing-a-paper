import re,subprocess,shutil,json
from pathlib import Path
from .. import db,security
from .common import owned,digest

PARSER='structural-lines.v1'
PATTERNS=(('contract',r'\b(?:contract|interface|library|template|class)\s+(\w+)'),('function',r'\b(?:function|def)\s+(\w+)'),('variable',r'\b(?:export\s+)?(?:const|let|var)\s+(\w+)'),('import',r'\bimport\s+(?:[\s\S]*?\bfrom\s*)?[\"\']([^\"\']+)[\"\']'))

def index_snapshot(pid,sid):
    owned('snapshot',sid,pid);counts={'files':0,'units':0,'unresolved':0};ast={};ast_version=''
    files=db.rows('SELECT * FROM repository_snapshot_files WHERE snapshot_id=?',(sid,))
    base=Path(__file__).resolve().parents[3];bundled_node=base/'.runtime'/'writing-agent'/'node.exe';node=str(bundled_node) if bundled_node.is_file() else shutil.which('node.exe') or shutil.which('node');helper=base/'scripts'/'writing-agent'/'parse-typescript.mjs'
    batches=[];batch=[];size=0
    for f in files:
        if Path(f['relative_path']).suffix not in ('.ts','.tsx','.js','.jsx','.mjs','.cjs'):continue
        text=security.safe_object(f['blob_sha256']).read_bytes().decode(f['encoding'])
        if size+len(text.encode())>4000000 and batch:batches.append(batch);batch=[];size=0
        batch.append({'path':f['relative_path'],'text':text});size+=len(text.encode())
    if batch:batches.append(batch)
    if node and helper.is_file():
        for batch in batches:
            try:
                r=subprocess.run([node,str(helper)],input=json.dumps({'files':batch}).encode(),capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if r.returncode==0:
                    parsed=json.loads(r.stdout);ast_version=parsed['parser'];ast.update({x['path']:x for x in parsed['files']})
            except (OSError,subprocess.TimeoutExpired,ValueError):pass
    path_units={};pending_edges=[];import_aliases={}
    for f in files:
        text=security.safe_object(f['blob_sha256']).read_bytes().decode(f['encoding']);lines=text.splitlines();found=[]
        syntax=ast.get(f['relative_path'])
        for i,line in enumerate(lines):
            for kind,pattern in PATTERNS:
                m=re.search(pattern,line)
                if m:found.append((i,kind,m.group(1)))
        definitions=syntax['definitions'] if syntax else [{'start':i+1,'end':found[n+1][0] if n+1<len(found) else len(lines),'kind':kind,'symbol':symbol} for n,(i,kind,symbol) in enumerate(found)]
        with db.transaction():
            existing=db.rows('SELECT id FROM code_units WHERE snapshot_id=? AND relative_path=?',(sid,f['relative_path']))
            if existing:continue
            file_id=db.uid()
            path_units[f['relative_path']]=file_id
            db.execute('INSERT INTO code_units VALUES(?,?,?,?,?,?,?,?,?,?)',(file_id,pid,sid,f['relative_path'],'file',f['relative_path'],1,max(1,len(lines)),digest(text),PARSER))
            for definition in definitions:
                i=definition['start']-1;end=definition['end'];kind=definition['kind'];symbol=definition['symbol'];uid=db.uid()
                db.execute('INSERT INTO code_units VALUES(?,?,?,?,?,?,?,?,?,?)',(uid,pid,sid,f['relative_path'],kind,symbol,i+1,max(i+1,end),digest('\n'.join(lines[i:end])),ast_version if syntax else PARSER))
                if kind=='import':
                    db.execute('INSERT INTO code_edges VALUES(?,?,?,?,?,?,?)',(db.uid(),sid,uid,None,'imports','unresolved',db.dumps({'target':symbol,'reason':'lexical_import_requires_resolution'})));counts['unresolved']+=1
            if syntax:
                for entry in syntax['imports']:
                    pending_edges.append((file_id,f['relative_path'],'imports',entry['target'],entry['start']))
                    for binding in entry.get('bindings',[]):import_aliases[(f['relative_path'],binding['local'])]=(entry['target'],binding['imported'])
                for entry in syntax['calls']:pending_edges.append((file_id,f['relative_path'],'references_symbol',entry['target'],entry['start']))
            db.execute('UPDATE repository_snapshot_files SET parse_state=? WHERE snapshot_id=? AND relative_path=?',('syntax_with_gaps' if syntax and(syntax['truncated'] or syntax['diagnostics']) else 'syntax_indexed' if syntax else 'structural_only',sid,f['relative_path']))
        counts['files']+=1;counts['units']+=1+len(definitions)
    with db.transaction():
        for from_id,path,relation,target,line in pending_edges:
            to_id=None;resolution='unresolved'
            if relation=='imports' and target.startswith('.'):
                from pathlib import PurePosixPath
                import posixpath
                base=posixpath.normpath(str(PurePosixPath(path).parent/target))
                matches=[path_units[p] for p in (base,base+'.ts',base+'.tsx',base+'.js',base+'/index.ts',base+'/index.tsx') if p in path_units]
                if len(set(matches))==1:to_id=matches[0];resolution='resolved_import_path'
            elif relation=='references_symbol' and re.fullmatch(r'\w+',target):
                match=db.rows('SELECT id FROM code_units WHERE snapshot_id=? AND relative_path=? AND symbol=? AND kind IN (\'function\',\'variable\')',(sid,path,target))
                if len(match)==1:to_id=match[0]['id'];resolution='candidate_static'
                elif not match and (path,target) in import_aliases:
                    target_path,exported=import_aliases[(path,target)]
                    if target_path.startswith('.'):
                        from pathlib import PurePosixPath
                        import posixpath
                        base=posixpath.normpath(str(PurePosixPath(path).parent/target_path))
                        candidates=[p for p in (base,base+'.ts',base+'.tsx',base+'.js',base+'/index.ts',base+'/index.tsx') if p in path_units]
                        if len(candidates)==1:
                            match=db.rows('SELECT id FROM code_units WHERE snapshot_id=? AND relative_path=? AND symbol=?',(sid,candidates[0],exported))
                            if len(match)==1:to_id=match[0]['id'];resolution='candidate_import_alias'
            db.execute('INSERT INTO code_edges VALUES(?,?,?,?,?,?,?)',(db.uid(),sid,from_id,to_id,relation,resolution,db.dumps({'target':target,'line':line,'runtime_call_graph':False})))
    return counts

def resolve(pid,sid,unit_id):
    owned('snapshot',sid,pid);unit=db.one('SELECT * FROM code_units WHERE id=? AND snapshot_id=?',(unit_id,sid))
    if not unit:raise ValueError('符号不在此快照')
    return {'unit':unit,'edges':db.rows('SELECT * FROM code_edges WHERE from_unit_id=?',(unit_id,)),'limitations':['静态线索不是完整运行时调用图']}
