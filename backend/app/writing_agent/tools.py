import json
from .. import db
from .common import owned,blob,unblob,digest,fail
from . import repository,evidence,code_index

SPECS={
 'list_repository':({'snapshot_id':'string'},{'prefix':'string','cursor':'string'}),
 'search_code':({'snapshot_id':'string','query':'string'},{}),
 'search_literature':({'query':'string'},{'limit':'integer'}),
 'read_literature':({'source_id':'string'},{}),
 'read_code':({'snapshot_id':'string','relative_path':'string','line_start':'integer','line_end':'integer'},{}),
 'resolve_symbol':({'snapshot_id':'string','unit_id':'string'},{}),
 'get_evidence':({'evidence_id':'string'},{}),
 'read_section':({'section_id':'string','revision_id':'string'},{}),
 'read_section_range':({'section_id':'string','revision_id':'string','node_id':'string'},{'before':'integer','after':'integer'}),
 'get_run_evidence':({'evidence_id':'string'},{}),
 'request_check':({'registered_check_id':'string','snapshot_id':'string'},{}),
}

def schemas():
    descriptions={'search_literature':'检索当前项目文献知识库，返回原文片段、source_id、书目及页码；通过read_literature读取需要引用的片段并取得文献证据ID','read_literature':'读取知识库source_id对应的完整原文片段，保留原文页码并建立literature文献证据；文献不是本项目已实现或已测量的结果'}
    return [{'type':'function','function':{'name':name,'description':descriptions.get(name,'读取本次授权范围内的'+name+('；单次完整请求片段最多6000 UTF-8字节，超限只返回缩小范围提示，不读取给模型；请先检索再定位必要行' if name=='read_code' else '')),'parameters':{'type':'object','properties':{k:{'type':t} for k,t in {**required,**optional}.items()},'required':list(required),'additionalProperties':False}}} for name,(required,optional) in SPECS.items()]

def execute(pid,step_id,name,arguments,call_id,snapshot_id):
    if not isinstance(call_id,str) or not 1<=len(call_id)<=200:fail('INVALID_TOOL_CALL','工具调用标识无效')
    if name not in SPECS:fail('TOOL_DENIED','工具不在允许列表',403)
    required,optional=SPECS[name]
    if not isinstance(arguments,dict) or set(arguments)-set(required)-set(optional) or set(required)-set(arguments):fail('INVALID_TOOL_ARGUMENTS','工具参数字段无效')
    for key,value in arguments.items():
        typ=(required|optional)[key]
        if (typ=='string' and (not isinstance(value,str) or len(value)>2000)) or (typ=='integer' and type(value) is not int):fail('INVALID_TOOL_ARGUMENTS','工具参数类型无效')
    if arguments.get('snapshot_id',snapshot_id)!=snapshot_id:fail('TOOL_DENIED','不能读取本次研究基线之外的快照',403)
    s=owned('snapshot',snapshot_id,pid);b=owned('repository',s['binding_id'],pid)
    if b['egress_policy']!='selected_content':fail('SOURCE_ACCESS_REVOKED','代码来源禁止外发',403)
    extra=''
    if name in ('search_literature','read_literature'):
        from .. import reference_knowledge
        extra=reference_knowledge.permission_epoch(pid)
        if name=='read_literature':reference_knowledge.get_chunk(pid,arguments['source_id'],remote=True)
    if name in ('get_evidence','get_run_evidence'):
        evidence.get(pid,arguments['evidence_id'],True);extra=evidence.permission_stamp(pid,arguments['evidence_id'])
    if name in ('read_section','read_section_range'):
        from .provenance import revision_permissions
        extra=revision_permissions(pid,arguments['revision_id'])
    epoch=digest([b['scope_revision'],extra]);old=db.one('SELECT * FROM tool_observations WHERE step_id=? AND call_id=?',(step_id,call_id))
    if old:
        if old['arguments_blob']!=blob(arguments) or old['tool_name']!=name:fail('TOOL_CALL_CONFLICT','同一调用ID不能改变参数',409)
        if old['egress_revision']!=epoch:fail('SOURCE_ACCESS_REVOKED','缓存来源权限已变化',403)
        return unblob(old['result_blob'])
    if name=='list_repository':
        entries=db.rows('SELECT relative_path,parse_state FROM repository_snapshot_files WHERE snapshot_id=? AND relative_path LIKE ? AND relative_path>? ORDER BY relative_path LIMIT 201',(snapshot_id,arguments.get('prefix','')+'%',arguments.get('cursor','')))
        data={'files':entries[:200],'coverage':unblob(s['manifest_blob'])['coverage'],'more_results':len(entries)>200,'next_cursor':entries[199]['relative_path'] if len(entries)>200 else None}
    elif name=='search_code':data={'hits':repository.search(pid,snapshot_id,arguments['query'],remote=True)}
    elif name=='search_literature':
        from ..reference_knowledge import search
        limit=arguments.get('limit',8)
        if not 1<=limit<=20:fail('INVALID_TOOL_ARGUMENTS','文献检索条数为1至20')
        found=search(pid,arguments['query'],limit,remote=True)
        data={'hits':found['items'],'mode':found['mode'],'warnings':found['warnings'],'collection_id':found['collection_id']}
    elif name=='read_literature':
        from ..reference_knowledge import get_chunk
        original=get_chunk(pid,arguments['source_id'],remote=True)
        old_e=db.one("SELECT id FROM evidence_items WHERE project_id=? AND kind='literature' AND origin_type='source' AND origin_id=? AND validity='current' ORDER BY created_at DESC LIMIT 1",(pid,original['id']))
        if old_e and not evidence.get(pid,old_e['id'])['version_current']:old_e=None
        observed=evidence.get(pid,old_e['id']) if old_e else evidence.create(pid,{'kind':'literature','origin_type':'source','origin_id':original['id'],'locator':db.loads(original['locator'],{}),'title':original['reference_title']})
        data={**original,'evidence_id':observed['id']}
    elif name=='read_code':
        data=repository.read(pid,snapshot_id,arguments['relative_path'],arguments['line_start'],arguments['line_end'],True)
        if len(data['text'].encode('utf-8'))>6000:
            data={'status':'needs_narrower_range','path':arguments['relative_path'],'requested_line_start':arguments['line_start'],'requested_line_end':arguments['line_end'],'requested_range_complete':False,'text_not_provided':True,'max_snippet_bytes':6000,'action':'先search_code定位与当前命题有关的函数，再读取较小的完整行范围；该请求不生成已观察证据。'}
        else:
            old_e=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='code' AND origin_id=? AND locator_json=? ORDER BY created_at DESC LIMIT 1",(pid,snapshot_id,db.dumps({'path':arguments['relative_path'],'line_start':arguments['line_start'],'line_end':arguments['line_end']})))
            if old_e and 'requested_range_complete' not in evidence.get(pid,old_e['id'])['payload']:old_e=None
            e=evidence.get(pid,old_e['id']) if old_e else evidence.create(pid,{'kind':'test_definition' if '/test' in arguments['relative_path'] else 'code_observation','origin_type':'code','origin_id':snapshot_id,'locator':{'path':arguments['relative_path'],'line_start':arguments['line_start'],'line_end':arguments['line_end']}});data['evidence_id']=e['id']
    elif name=='resolve_symbol':data=code_index.resolve(pid,snapshot_id,arguments['unit_id'])
    elif name in ('get_evidence','get_run_evidence'):
        original=evidence.get(pid,arguments['evidence_id'],True)
        data={**evidence.for_model(original),'origin_type':original['origin_type'],'origin_version':original['origin_version'],'original_payload_hash':original['payload_hash'],'validity':original['validity'],'version_current':original['version_current']}
    elif name in ('read_section','read_section_range'):
        from .common import section
        section(arguments['section_id'],pid)
        r=db.one('SELECT id,text,document FROM revisions WHERE id=? AND section_id=?',(arguments['revision_id'],arguments['section_id']))
        if not r:fail('NOT_FOUND','正文版本不存在',404)
        for dep in db.rows("SELECT upstream_id FROM dependencies WHERE revision_id=? AND upstream_type='source'",(r['id'],)):
            from .. import security
            src=db.one('SELECT file_id FROM sources WHERE id=?',(dep['upstream_id'],))
            if not src or not security.file_allows_remote(src['file_id'],pid):fail('SOURCE_ACCESS_REVOKED','关联正文禁止外发',403)
        if name=='read_section':data={k:r[k] for k in ('id','text')}
        else:
            from .sentences import paragraphs,paragraph_text
            before=arguments.get('before',2);after=arguments.get('after',2)
            if not 0<=before<=8 or not 0<=after<=8:fail('INVALID_TOOL_ARGUMENTS','扩展范围限前后各0至8个完整段落')
            blocks=paragraphs(db.loads(r['document'],{}));anchor=arguments['node_id']
            at=next((i for i,n in enumerate(blocks) if n['attrs']['nodeId']==anchor),None) if anchor else 0
            if at is None:fail('INVALID_SELECTION','扩展锚点不在该正文版本内')
            a=max(0,at-before);b=min(len(blocks),at+after+1)
            data={'section_id':arguments['section_id'],'revision_id':r['id'],'paragraphs':[{'node_id':n['attrs']['nodeId'],'text':paragraph_text(n)} for n in blocks[a:b]],'start_index':a,'end_index':b,'total_paragraphs':len(blocks),'complete_section':a==0 and b==len(blocks),'previous_node_id':blocks[a-1]['attrs']['nodeId'] if a else None,'next_node_id':blocks[b]['attrs']['nodeId'] if b<len(blocks) else None}
    else:
        from .check_registry import execute as check
        data=check(pid,arguments['registered_check_id'],snapshot_id)
    result={'status':data.get('status','ok'),'data':data,'provenance':{'snapshot_id':snapshot_id,'payload_hash':digest(data)},'truncated':False}
    if len(db.dumps(result).encode())>64000:fail('TOOL_RESULT_TOO_LARGE','结果过长，请缩小范围')
    db.execute('INSERT INTO tool_observations VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),step_id,call_id,name,blob(arguments),blob(result),'ok',digest(data),epoch))
    return result
