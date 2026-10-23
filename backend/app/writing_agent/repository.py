"""Read-only source capture. Captures bytes, not a promise of a filesystem instant."""
import fnmatch,os,re,subprocess
from pathlib import Path,PurePosixPath
from .. import db,security
from .common import blob,unblob,digest,owned,fail,state,update_state
from .models import RepositoryInput

EXCLUDED={'.git','node_modules','.runtime','.experimental','.next','dist','build','coverage','__pycache__','.pytest_cache','.codex','.agents','.aws','.venv','venv','.cache','.hardhat-global-cache','cache','artifacts','typechain-types','chroma-data'}
EXTENSIONS={'.ts','.tsx','.js','.jsx','.mjs','.cjs','.sol','.circom','.py','.md','.txt','.json','.yaml','.yml','.toml','.sql','.css','.html','.xml'}
SECRET=re.compile(r'(?:\bsk-[A-Za-z0-9_-]{20,}|(?:private[_-]?key|api[_-]?key|mnemonic|secret[_-]?key)\s*[=:]\s*[\"\'](?:0x)?[A-Za-z0-9 /+_=.-]{24,}[\"\'])',re.I)

def reparse(path):
    return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&0x400)

def safe_relative(value):
    if not isinstance(value,str) or '\\' in value or ':' in value or value.startswith('/') or '..' in PurePosixPath(value).parts:fail('INVALID_PATH','来源相对路径无效')
    return value

def bind(pid,body):
    data=RepositoryInput.model_validate(body);p=Path(data.root)
    if not p.is_absolute() or not p.is_dir() or reparse(p):fail('INVALID_ROOT','请选择真实存在且非链接的项目目录')
    root=p.resolve()
    if root==Path(root.anchor) or root==Path.home():fail('INVALID_ROOT','不能把磁盘根或整个用户目录作为项目')
    identity=db.uid();scope=data.model_dump(exclude={'root','egress'})
    db.execute('INSERT INTO repository_bindings VALUES(?,?,?,?,?,?,?)',(identity,pid,str(root),db.dumps(scope),1,data.egress,db.now()))
    security.require_project(pid)
    return owned('repository',identity,pid)

def set_egress(pid,rid,value):
    if value not in ('local_only','selected_content'):fail('INVALID_POLICY','外发策略无效')
    owned('repository',rid,pid)
    db.execute('UPDATE repository_bindings SET egress_policy=?,scope_revision=scope_revision+1 WHERE id=?',(value,rid))
    from .impact import changed
    changed(pid,'repository',rid,'来源外发策略已变化')
    return owned('repository',rid,pid)

def decode(raw):
    if b'\x00' in raw and not raw.startswith((b'\xff\xfe',b'\xfe\xff')):raise ValueError('二进制内容')
    encoding='utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
    text=raw.decode(encoding)
    return text,encoding

def capture(pid,rid,mode='working_tree',ref=None):
    binding=owned('repository',rid,pid);root=Path(binding['root_path_local']);scope=db.loads(binding['scope_json'],{})
    if not binding['root_path_local'] or not root.is_absolute():fail('REBIND_REQUIRED','恢复的项目需重新绑定本机代码目录')
    if mode not in ('working_tree','git_commit'):fail('INVALID_CAPTURE_MODE','快照模式无效')
    if not root.is_dir() or reparse(root):fail('INVALID_ROOT','来源目录已变化')
    snapshot=db.uid();start=db.now();raws={};entries=[];gaps=[];excluded=[];total=0;head=None
    def take(name,raw,observed):
        nonlocal total
        safe_relative(name)
        if len(raw)>scope.get('max_file_bytes',2097152) or total+len(raw)>scope.get('max_total_bytes',209715200):
            gaps.append({'path':name,'reason':'size_limit'});return
        try:text,encoding=decode(raw)
        except (UnicodeError,ValueError):gaps.append({'path':name,'reason':'decoding_failed'});return
        if SECRET.search(text):excluded.append({'path':name,'reason':'possible_secret'});return
        total+=len(raw);sha=security.put_blob(raw);raws[name]=raw
        entries.append({'path':name,'sha256':sha,'encoding':encoding,'observed':observed,'text_warnings':['original_contains_replacement_character'] if '\ufffd' in text else []})
    def included(name):
        p=PurePosixPath(name)
        if any(x in EXCLUDED for x in p.parts) or p.name.startswith('.env') or p.suffix.lower() in ('.key','.pem','.p12','.pfx','.dpapi','.sqlite3'):
            excluded.append({'path':name,'reason':'excluded_scope'});return False
        if p.suffix.lower() not in EXTENSIONS and p.name not in ('Dockerfile','AGENTS.md'):
            excluded.append({'path':name,'reason':'unsupported_format'});return False
        patterns=scope.get('include',[])
        return not patterns or any(fnmatch.fnmatchcase(name,x) for x in patterns)
    if mode=='git_commit':
        def git(*args):
            r=subprocess.run(['git','-c','core.excludesFile=','-C',str(root),*args],capture_output=True,timeout=30)
            if r.returncode:fail('CAPTURE_FAILED','无法读取指定Git对象')
            return r.stdout
        if not isinstance(ref,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}',ref) or ref.startswith('-'):fail('INVALID_REF','请指定提交或分支')
        head=git('rev-parse','--verify',ref+'^{commit}').decode().strip()
        listing=git('ls-tree','-r','-z',head).split(b'\x00')
        for item in listing:
            if not item:continue
            meta,name=item.split(b'\t',1);name=name.decode('utf-8');modebits,kind,obj=meta.decode().split()
            if modebits=='120000' or kind!='blob':gaps.append({'path':name,'reason':'link_or_submodule'});continue
            if included(name):take(name,git('cat-file','blob',obj),{'commit':head,'object':obj})
    else:
        scope_before=set()
        for directory,dirs,files in os.walk(root,followlinks=False):
            parent=Path(directory)
            for d in list(dirs):
                p=parent/d
                if d in EXCLUDED or reparse(p):excluded.append({'path':p.relative_to(root).as_posix(),'reason':'excluded_directory_or_link'});dirs.remove(d)
            for name in sorted(files):
                p=parent/name;relative=p.relative_to(root).as_posix()
                if not included(relative):continue
                scope_before.add(relative)
                try:
                    if reparse(p) or not p.resolve().is_relative_to(root):gaps.append({'path':relative,'reason':'path_boundary'});continue
                    before=p.stat()
                    if before.st_size>scope.get('max_file_bytes',2097152):gaps.append({'path':relative,'reason':'size_limit'});continue
                    raw=p.read_bytes();after=p.stat()
                    if (before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size):gaps.append({'path':relative,'reason':'changed_during_capture'});continue
                    take(relative,raw,{'size':after.st_size,'mtime_ns':after.st_mtime_ns,'read_at':db.now()})
                except OSError:gaps.append({'path':relative,'reason':'read_failed'})
        for entry in entries:
            p=root/entry['path']
            try:
                if reparse(p) or digest(p.read_bytes())!=entry['sha256']:gaps.append({'path':entry['path'],'reason':'changed_during_capture'})
            except OSError:gaps.append({'path':entry['path'],'reason':'disappeared'})
        scope_after=set()
        for directory,dirs,files in os.walk(root,followlinks=False):
            parent=Path(directory);dirs[:]=[d for d in dirs if d not in EXCLUDED and not reparse(parent/d)]
            for name in files:
                relative=(parent/name).relative_to(root).as_posix()
                if included(relative):scope_after.add(relative)
        for relative in sorted(scope_before^scope_after):gaps.append({'path':relative,'reason':'scope_changed_during_capture'})
    from .rule_loading import loaded_rules
    manifest={'schema':'repository.v1','mode':mode,'head_commit':head,'files':entries,'gaps':gaps,'excluded':excluded,'bytes':total,'rules':loaded_rules(root,raws),'coverage':'partial' if gaps else 'complete','atomic_filesystem_snapshot':mode=='git_commit','captured_at':db.now()}
    unstable=any(g['reason'] in ('changed_during_capture','disappeared','scope_changed_during_capture') for g in gaps)
    sha=blob(manifest)
    with db.transaction():
        db.execute('INSERT INTO repository_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)',(snapshot,pid,rid,mode,'unstable' if unstable else 'ready',sha,digest(manifest),head,db.dumps({'start':start,'end':db.now()}),db.now()))
        for entry in entries:db.execute('INSERT INTO repository_snapshot_files VALUES(?,?,?,?,?,?,?)',(snapshot,entry['path'],entry['sha256'],entry['encoding'],None,'pending',db.dumps(entry['observed'])))
        current=state(pid)
        if not current['target_snapshot_id'] and not unstable:update_state(pid,current['revision'],target_snapshot_id=snapshot)
    from .code_index import index_snapshot
    index_snapshot(pid,snapshot)
    return get_snapshot(pid,snapshot)

def get_snapshot(pid,sid):
    r=owned('snapshot',sid,pid)
    return {**r,'manifest':unblob(r['manifest_blob']),'parse_states':db.rows('SELECT parse_state,COUNT(*) n FROM repository_snapshot_files WHERE snapshot_id=? GROUP BY parse_state',(sid,)),'index_limitations':['静态关系及导入别名只提供调查线索，解析缺口或动态调用不能用来认定功能不存在']}

def read(pid,sid,path,line_start=1,line_end=None,remote=False):
    s=owned('snapshot',sid,pid);binding=owned('repository',s['binding_id'],pid)
    if remote and binding['egress_policy']!='selected_content':fail('SOURCE_ACCESS_REVOKED','该代码来源禁止外发',403)
    if s['state']!='ready':fail('SNAPSHOT_UNSTABLE','快照尚未稳定',409)
    safe_relative(path)
    f=db.one('SELECT * FROM repository_snapshot_files WHERE snapshot_id=? AND relative_path=?',(sid,path))
    if not f:fail('NOT_FOUND','此文件未纳入冻结范围',404)
    text=security.safe_object(f['blob_sha256']).read_bytes().decode(f['encoding']);lines=text.splitlines(keepends=True)
    end=min(len(lines),line_end if line_end is not None else len(lines))
    if not isinstance(line_start,int) or line_start<1 or end<line_start-1 or end-line_start>2000:fail('INVALID_RANGE','行范围无效或过大')
    return {'path':path,'text':''.join(lines[line_start-1:end]),'line_start':line_start,'line_end':end,'file_sha256':f['blob_sha256'],'snapshot_id':sid,'binding_revision':binding['scope_revision'],'parse_state':f['parse_state'],'total_lines':len(lines),'truncated':False,'requested_range_complete':True,'more_lines_available':end<len(lines),'next_line':end+1 if end<len(lines) else None,'file_complete_in_result':line_start==1 and end==len(lines),'text_warnings':['原始文件已含U+FFFD；未修复或替换，不应推断丢失部分'] if '\ufffd' in text else []}

def search(pid,sid,query,limit=15,remote=False):
    if not isinstance(query,str) or not query.strip() or len(query)>1000:fail('INVALID_QUERY','请输入检索问题')
    words=re.findall(r'[\w]+',query.lower());hits=[]
    files=db.rows('SELECT * FROM repository_snapshot_files WHERE snapshot_id=?',(sid,));owned('snapshot',sid,pid)
    for f in files:
        data=read(pid,sid,f['relative_path'],1,1,remote=remote)
        lines=security.safe_object(f['blob_sha256']).read_bytes().decode(f['encoding']).splitlines();matches=[i for i,l in enumerate(lines) if any(w in l.lower() for w in words)]
        for i in matches[:3]:
            start=max(0,i-6);end=min(len(lines),i+18)
            hits.append({**data,'text':'\n'.join(lines[start:end]),'line_start':start+1,'line_end':end,'more_lines_available':end<len(lines),'next_line':end+1 if end<len(lines) else None,'file_complete_in_result':start==0 and end==len(lines),'score':sum(w in ('\n'.join(lines[start:end])).lower() for w in words)})
    return sorted(hits,key=lambda x:x['score'],reverse=True)[:max(1,min(limit,40))]
