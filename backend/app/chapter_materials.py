"""Actual database/page/route/test materials and source-bound visual plans."""
from copy import deepcopy
from pathlib import PurePosixPath
import json
import posixpath
import re
from . import db
from . import project_structures as structures
from .writing_agent.common import digest

KINDS={'requirements','system_design','implementation','results'}
LANES=('database_tables','frontend_pages','api_routes','test_cases','execution_records')
PROFILES={
    'requirements':{'tables':1,'pages':3,'routes':4,'tests':0},
    'system_design':{'tables':4,'pages':2,'routes':4,'tests':0},
    'implementation':{'tables':2,'pages':3,'routes':6,'tests':1},
    'results':{'tables':0,'pages':0,'routes':0,'tests':8},
}


def empty(snapshot_id):
    return {'schema':'chapter_materials.v1','snapshot_id':snapshot_id,'database_tables':[],
            'frontend_pages':[],'api_routes':[],'test_cases':[],'execution_records':[],'visual_plans':[],
            'gaps':[],'candidate_files':[],'readings':[],'required_evidence_ids':[],
            'coverage':{'read_bytes':0,'read_ranges':0,'complete_project_structure':False},
            'policy':'字段、页面与测试均是给定快照中的静态声明；不是部署库、界面运行截图或实测结果。图表只组织已读材料，段落须解释其具体内容和限制。'}


def collect(pid,snapshot_id,section,paths,query='',existing=(),max_bytes=24000,max_ranges=14):
    from .writing_agent import repository,evidence
    result=empty(snapshot_id);profile=PROFILES.get(section.get('kind'))
    if not profile:return result,[]
    from .chapter_visual_plans import target_groups
    if not target_groups(section):return result,[]
    path_set=set(paths);cache={};items=[];readings={};used=0;count=0
    existing_ranges={}
    for item in existing:
        payload=item.get('payload',{})
        if item.get('origin_type')=='code' and item.get('origin_id')==snapshot_id:
            existing_ranges[(payload.get('path'),payload.get('line_start'),payload.get('line_end'))]=item

    def read(path):
        if path not in cache:
            cache[path]=repository.read(pid,snapshot_id,path,1,2000,remote=True)
            if cache[path].get('more_lines_available'):
                result['gaps'].append({'path':path,'reason':'结构定位仅扫描前2000行，后续范围需定向补读；未视作完整文件结构'})
        return cache[path]['text']

    def register(path,start_cp,end_cp):
        nonlocal used,count
        text=read(path);lines=text.splitlines(keepends=True)
        start=text.count('\n',0,start_cp)+1;end=text.count('\n',0,max(start_cp,end_cp-1))+1
        ranges=[];at=start
        while at<=end:
            stop=at;size=0
            while stop<=end and stop-at<100 and size+len(lines[stop-1].encode('utf-8'))<=6000:
                size+=len(lines[stop-1].encode('utf-8'));stop+=1
            if stop==at:
                result['gaps'].append({'path':path,'reason':'单行原文超过6000字节，结构条目未作为已读材料提供'});return None
            ranges.append((at,stop-1,size));at=stop
        def covering(a,b):
            return next((key for key in readings if key[0]==path and key[1]<=a and key[2]>=b),None)
        fresh=[r for r in ranges if covering(r[0],r[1]) is None]
        if used+sum(r[2] for r in fresh)>max_bytes or count+len(fresh)>max_ranges:
            result['gaps'].append({'path':path,'line_start':start,'line_end':end,'reason':'章节结构阅读预算未覆盖完整条目，未提供字段或图表计划'});return None
        ids=[]
        for a,b,size in ranges:
            key=covering(a,b) or (path,a,b)
            if key not in readings:
                locator={'path':path,'line_start':a,'line_end':b}
                item=existing_ranges.get(key)
                if item is None:
                    prior=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='code' AND origin_id=? AND locator_json=? AND validity='current'",(pid,snapshot_id,db.dumps(locator)))
                    item=evidence.get(pid,prior['id'],remote=True) if prior else evidence.create(pid,{
                        'kind':'test_definition' if re.search(r'(?:^|/)(?:tests?|__tests__|spec)/|\.(?:test|spec)\.',path) else 'code_observation',
                        'origin_type':'code','origin_id':snapshot_id,'locator':locator,
                        'conditions':['实际数据库声明、前端代码或测试定义的明确范围；不证明部署库状态、界面已运行或测试已执行。']})
                readings[key]={**locator,'evidence_id':item['id'],'file_sha256':cache[path]['file_sha256']}
                items.append(item);used+=size;count+=1
            ids.append(readings[key]['evidence_id'])
        return list(dict.fromkeys(ids))

    def add(row,path,lane,start=None,end=None):
        a=row.get('source_start',0) if start is None else start
        b=row.get('source_end',len(read(path))) if end is None else end
        ids=register(path,a,b)
        if not ids:return None
        value={key:deepcopy(val) for key,val in row.items() if key not in {'source_start','source_end','imports'}}
        value.update(id='material-'+digest([snapshot_id,path,a,b,row.get('name'),row.get('kind')])[:24],
                     path=path,evidence_ids=ids,source_scope='exact_registered_source_ranges',semantic_support='not_assessed')
        result[lane].append(value)
        return value

    production=[path for path in paths if not re.search(r'(?:^|/)(?:vendor|node_modules|third_party|archive|logs|dist|build)(?:/|$)',path,re.I)]
    data_paths=[path for path in production if (re.search(r'(?:schema|/db/|database|migration)',path,re.I) or path.endswith('.sql'))
                and PurePosixPath(path).suffix.lower() in {'.sql','.ts','.js','.py'} and not re.search(r'/tests?/|\.test\.',path)]
    data_paths.sort(key=lambda path:(not bool(re.search(r'^(?:backend|src|prisma|database|db)/|^[^/]+\.sql$',path)),
                                     not path.endswith('.sql'),not bool(re.search(r'(?:schema|startup|bootstrap)\.',path,re.I)),len(PurePosixPath(path).parts),path))
    table_candidates=[]
    for path in data_paths[:12] if profile['tables'] else []:
        for row in structures.sql_tables(read(path),path):table_candidates.append((path,row))
    terms=set(re.findall(r'[a-z][a-z0-9_]{2,}',query.lower()))
    table_candidates.sort(key=lambda item:(-sum(term in item[1]['name'].lower() for term in terms),
                                          not item[1]['complete_definition'],len(item[0]),item[1]['source_start']))
    seen_names=set()
    for path,row in table_candidates:
        if len(result['database_tables'])>=profile['tables']:break
        if row['name'] in seen_names:continue
        variants=[other for _,other in table_candidates if other['name']==row['name']]
        shapes={digest([other['fields'],other['primary_key'],other['unique_keys'],other['foreign_keys']]) for other in variants}
        row['conflicting_definitions']=len(shapes)>1
        if row['conflicting_definitions']:row['limitations'].append('发现同名表的不同声明，未自动认定哪个定义是当前部署结构')
        if add(row,path,'database_tables'):seen_names.add(row['name'])

    # Reserve a small route slice before component details consume the budget.
    # Multiple declarations may share one exact read range; a catalogue of
    # endpoint names alone never substitutes for those actual source lines.
    api_paths=[path for path in production if re.search(r'(?:routes?|controllers?|/http/)',path,re.I)
               and PurePosixPath(path).suffix in {'.ts','.js','.py'} and not re.search(r'/tests?/|test-support|\.test\.',path)]
    api_start=count
    for path in sorted(api_paths,key=lambda p:(-sum(term in p.lower() for term in terms),len(PurePosixPath(p).parts),p))[:12] if profile['routes'] else []:
        for row in structures.api_routes(read(path),path):
            if len(result['api_routes'])>=profile['routes'] or count-api_start>=2:break
            add(row,path,'api_routes',max(0,row['source_start']-100),min(len(read(path)),row['source_end']+150))
        if len(result['api_routes'])>=profile['routes'] or count-api_start>=2:break

    # Resolve imports only using real relative paths or an actually read
    # tsconfig paths mapping. Convention-only guesses do not become edges.
    configs={}
    def resolve(path,module):
        bases=[];config_ids=[]
        if module.startswith('.'):
            bases=[posixpath.normpath(posixpath.join(str(PurePosixPath(path).parent),module))]
        else:
            parent=PurePosixPath(path).parent
            for directory in [parent,*parent.parents]:
                config=(directory/'tsconfig.json').as_posix()
                if config not in path_set:continue
                if config not in configs:
                    try:configs[config]=json.loads(read(config))
                    except (ValueError,TypeError):configs[config]={}
                options=configs[config].get('compilerOptions',{}) if isinstance(configs[config],dict) else {}
                if not isinstance(options,dict):options={}
                mappings=options.get('paths',{})
                if not isinstance(mappings,dict):mappings={}
                for pattern,targets in mappings.items():
                    if not isinstance(pattern,str):continue
                    prefix=pattern.split('*')[0]
                    if '*' not in pattern or not module.startswith(prefix):continue
                    for target in targets if isinstance(targets,list) else []:
                        if not isinstance(target,str):continue
                        base_url=options.get('baseUrl','.')
                        if not isinstance(base_url,str):continue
                        bases.append(posixpath.normpath(posixpath.join(str(directory),base_url,target.replace('*',module[len(prefix):]))))
                if bases:
                    config_ids=register(config,0,len(read(config))) or []
                break
        matches={candidate for base in bases for candidate in
                 (base,base+'.tsx',base+'.ts',base+'.jsx',base+'.js',base+'/index.tsx',base+'/index.ts') if candidate in path_set}
        if len(matches)!=1 or not module.startswith('.') and not config_ids:return None,[]
        return next(iter(matches)),config_ids

    page_paths=[path for path in production if structures.route_from_file(path) is not None and not re.search(r'/tests?/|\.test\.',path)]
    def page_rank(path):
        action=0 if re.search(r'/(?:apply|create|new|submit|applications)/page\.',path) else 1 if re.search(r'/(?:credentials|records|courses|list)/page\.',path) else 2 if re.search(r'/(?:profile|dashboard|settings)/page\.',path) else 3
        return action,-sum(term in path.lower() for term in terms),len(PurePosixPath(path).parts),path
    page_paths.sort(key=page_rank)
    for path in page_paths if profile['pages'] else []:
        if len(result['frontend_pages'])>=profile['pages'] or count>=max_ranges:break
        text=read(path);row=structures.frontend_page(text,path)
        limit=''.join(text.splitlines(keepends=True)[:45])
        row['labels']=[label for label in row['labels'] if label in limit]
        page=add(row,path,'frontend_pages',0,len(limit))
        if not page:continue
        page['components']=[]
        imports=row['imports']
        for imported in imports[:8]:
            if imported['source_end']>len(limit):continue
            target,config_ids=resolve(path,imported['module'])
            if not target:continue
            component_text=read(target);header=''.join(component_text.splitlines(keepends=True)[:65])
            ids=register(target,0,len(header))
            if not ids:continue
            page['evidence_ids']=list(dict.fromkeys([*page['evidence_ids'],*config_ids,*ids]))
            page['components'].append({'name':PurePosixPath(target).stem,'path':target,
                                       'relation':'source_import','evidence_ids':list(dict.fromkeys([*config_ids,*ids]))})
            page['api_calls']=[];page['controls']=[]
            masked_component=structures.code_mask(component_text)
            lines=component_text.splitlines(keepends=True)
            def near(a,b):
                first=max(0,component_text.count('\n',0,a)-3)
                last=min(len(lines),component_text.count('\n',0,b)+5)
                return len(''.join(lines[:first])),len(''.join(lines[:last]))
            # Read interface text itself, rather than treating the route or
            # imported symbol as its business name. Resolve only literal keys
            # in a JSON namespace with the same explicit nesting.
            ui=re.search(r'<(?:PageLayout|h1|h2|CardTitle)\b',component_text)
            snippets=[header]
            if ui:
                a,b=near(ui.start(),ui.end());ui_ids=register(target,a,b)
                if ui_ids:
                    snippets.append(component_text[a:b])
                    page['evidence_ids']=list(dict.fromkeys([*page['evidence_ids'],*ui_ids]))
                    labels=structures.frontend_page(component_text[a:b],path)['labels']
                    page['labels']=list(dict.fromkeys([*page['labels'],*labels]))
            call_candidates=[{'symbol':call[1]+'.'+call[2],'start':call.start(),'end':call.end(),
                              'kind':'member_call'} for call in re.finditer(r'\b([A-Za-z_$][\w$]*(?:Api|api))\.([A-Za-z_$][\w$]*)\s*\(',masked_component)]
            # A page often delegates submission to an imported feature helper,
            # rather than invoking an HTTP client directly. Keep that actual
            # call visible without inventing the helper's internal behavior.
            for match in re.finditer(r'\bimport\s*\{([^}]+)\}\s*from\s*[\'"]([^\'"]+)[\'"]',header):
                for imported_name in match[1].split(','):
                    local=imported_name.strip().split(' as ')[-1].strip()
                    if not re.fullmatch(r'(?:submit|approve|reject|mint|verify|create)[A-Z]\w*',local):continue
                    for call in re.finditer(r'\b'+re.escape(local)+r'\s*\(',masked_component):
                        call_candidates.append({'symbol':local,'start':call.start(),'end':call.end(),'kind':'imported_function_call'})
            def call_rank(call):
                symbol=call['symbol']
                priority=0 if re.search(r'apply|submit|approve|reject|mint',symbol,re.I) else 1 if re.search(r'verify|create',symbol,re.I) else 2
                return priority,call['start']
            call_candidates.sort(key=call_rank)
            unique=[];symbols=set()
            for call in call_candidates:
                if call['symbol'] not in symbols:unique.append(call);symbols.add(call['symbol'])
            for call in unique[:2]:
                # Reserve the control and locale ranges that identify the page
                # in reader-facing language, before optional helper calls.
                if 'useTranslations' in header and ui and count+4>max_ranges:break
                a,b=near(call['start'],call['end']);call_ids=register(target,a,b)
                if call_ids:
                    page['evidence_ids']=list(dict.fromkeys([*page['evidence_ids'],*call_ids]))
                    page['api_calls'].append({'symbol':call['symbol'],'call_kind':call['kind'],'evidence_ids':call_ids,
                                              'scope':'静态调用点，不假定各调用的运行时先后顺序'})
            controls=list(re.finditer(r'<(Input|Select|Textarea|Button|input|select|textarea|button|form)\b[^>]*>',masked_component))
            control=min(controls,key=lambda m:(0 if re.search(r'submit|verify|apply|approve|reject',component_text[m.start():m.end()],re.I) else 1 if m[1].lower() in {'input','select','textarea','form'} else 2,m.start())) if controls else None
            if control:
                a,b=near(control.start(),control.end());control_ids=register(target,a,b)
                if control_ids:
                    page['evidence_ids']=list(dict.fromkeys([*page['evidence_ids'],*control_ids]))
                    page['controls'].append({'tag':control[1],'evidence_ids':control_ids})
                    snippets.append(component_text[a:b])
                    labels=structures.frontend_page(component_text[a:b],path)['labels']
                    page['labels']=list(dict.fromkeys([*page['labels'],*labels]))
            namespace=re.search(r'\bconst\s+t\s*=\s*useTranslations\([\'"]([^\'"]+)[\'"]\)',header)
            keys=list(dict.fromkeys(re.findall(r'\bt\([\'"]([^\'"]+)[\'"]\)', '\n'.join(snippets))))
            if namespace and keys:
                suffix='/'+namespace[1].replace('.','/')+'.json'
                candidates=[p for p in production if p.endswith(suffix) and re.search(r'/(?:zh|zh-CN|zh_CN)/',p)]
                translations=[]
                for translation_path in candidates[:3]:
                    try:
                        raw=json.loads(read(translation_path));value=raw
                        for part in namespace[1].split('.'):value=value[part]
                        if isinstance(value,dict):translations.append((translation_path,value))
                    except (ValueError,KeyError,TypeError):continue
                if len(translations)==1:
                    translation_path,value=translations[0]
                    # Register the namespace and individual literal value lines,
                    # not a whole large locale catalogue.
                    for key in [k for k in keys if re.search(r'title|submit|apply|verify|retry|reject|approve|label|tab',k,re.I)][:3]:
                        label=value.get(key)
                        if not isinstance(label,str) or len(label)>80 or '{' in label:continue
                        source=read(translation_path)
                        hit=re.search(r'"'+re.escape(key)+r'"\s*:\s*'+re.escape(json.dumps(label,ensure_ascii=False)),source)
                        if not hit:continue
                        # Register one compact namespace+selected-value span when
                        # it fits, otherwise keep the exact separated ranges.
                        ids=register(translation_path,0,hit.end()) if source.count('\n',0,hit.end())<60 and len(source[:hit.end()].encode('utf-8'))<4000 else register(translation_path,0,len(''.join(source.splitlines(keepends=True)[:4])))
                        label_ids=register(translation_path,source.rfind('\n',0,hit.start())+1,hit.end()) if ids else None
                        if label_ids:
                            page['evidence_ids']=list(dict.fromkeys([*page['evidence_ids'],*ids,*label_ids]))
                            page['labels']=list(dict.fromkeys([*page['labels'],label]))
            page['component_scope']='已读组件头部及明确登记的调用/控件片段；不表示完整阅读全部渲染与交互'
            break

    test_paths=[path for path in production if re.search(r'/tests?/|\.(?:test|spec)\.',path) and PurePosixPath(path).suffix in {'.ts','.js','.tsx','.py'}]
    for path in sorted(test_paths,key=lambda p:(-sum(term in p.lower() for term in terms),len(PurePosixPath(p).parts),p))[:8] if profile['tests'] else []:
        if count>=max_ranges:break
        for row in structures.test_cases(read(path),path):
            if len(result['test_cases'])>=profile['tests']:break
            add(row,path,'test_cases')
        if len(result['test_cases'])>=profile['tests']:break
    result['readings']=list(readings.values())
    result['required_evidence_ids']=list(dict.fromkeys(eid for lane in LANES
                                                        for row in result[lane] for eid in row['evidence_ids']))
    result['coverage'].update(read_bytes=used,read_ranges=count,locally_scanned_files=len(cache))
    result['candidate_files']=list(dict.fromkeys([*data_paths[:12],*page_paths[:12],*api_paths[:8],*test_paths[:8]]))
    from .chapter_visual_plans import build
    result['visual_plans']=build(section,result)
    from .paper_visuals import validate_spec
    valid=[]
    for plan in result['visual_plans']:
        try:plan['spec']=validate_spec(plan['spec']);valid.append(plan)
        except ValueError as error:result['gaps'].append({'kind':'visual_plan_unavailable','reason':str(error)})
    result['visual_plans']=valid
    if len(result['gaps'])>20:
        result['coverage']['additional_gap_records']=len(result['gaps'])-20
        result['gaps']=result['gaps'][:20]
    result['hash']=digest(result)
    return result,items


def available(materials,ids):
    value=deepcopy(materials);allowed=set(ids)
    for lane in LANES:
        value[lane]=[row for row in value.get(lane,[]) if set(row['evidence_ids'])<=allowed]
    known={row['id'] for lane in LANES for row in value[lane]}
    value['visual_plans']=[row for row in value['visual_plans'] if set(row['material_ids'])<=known and set(row['evidence_ids'])<=allowed]
    value['readings']=[row for row in value['readings'] if row['evidence_id'] in allowed]
    value['required_evidence_ids']=[eid for eid in value['required_evidence_ids'] if eid in allowed]
    value['coverage']['supplied_materials']=len(known)
    value.pop('hash',None);value['hash']=digest(value)
    return value


def with_execution_records(materials,section,evidence_rows):
    """Only typed, actually executed records may supply a results table."""
    value=deepcopy(materials)
    if section.get('kind')!='results':return value
    records=[]
    for row in evidence_rows:
        if row.get('kind') not in {'test_run','data_result'}:continue
        payload=row.get('payload',{});observed=payload.get('observed',{})
        if observed.get('actual_execution') is not True:continue
        command=observed.get('command','未提供命令')
        if isinstance(command,list):command=' '.join(str(part) for part in command)
        code=observed.get('exit_code')
        outcome='退出状态待核对' if type(code) is not int else 'exit_code='+str(code)
        scope=observed.get('scope','只适用于该次命令记录')
        if observed.get('fixture') is True:scope=str(scope)+'（隔离/夹具执行）'
        if observed.get('timed_out') is True or observed.get('timed_out_or_output_limited') is True:
            scope=str(scope)+'；记录含超时或输出受限标记'
        if not observed.get('snapshot_id'):scope=str(scope)+'；记录未标定代码快照'
        elif value.get('snapshot_id') and observed['snapshot_id']!=value['snapshot_id']:
            scope=str(scope)+'；记录来自其他代码快照，不能当作本次版本验证'
        records.append({'id':'material-'+digest([row['id'],observed])[:24],
                        'kind':'execution_record','name':payload.get('title') or row['id'],
                        'path':'已登记运行产物','command':str(command),'outcome':outcome,
                        'evidence_ids':[row['id']],'scope':scope,
                        'limitations':['进程退出码不自动证明每条测试用例通过，更不证明部署效果']})
    value['execution_records']=records[:8]
    value['required_evidence_ids']=list(dict.fromkeys([*value['required_evidence_ids'],*[row['evidence_ids'][0] for row in value['execution_records']]]))
    from .chapter_visual_plans import build
    from .paper_visuals import validate_spec
    valid=[]
    for plan in build(section,value):
        try:plan['spec']=validate_spec(plan['spec']);valid.append(plan)
        except ValueError as error:value['gaps'].append({'kind':'visual_plan_unavailable','reason':str(error)})
    value['visual_plans']=valid
    value.pop('hash',None);value['hash']=digest(value)
    return value
