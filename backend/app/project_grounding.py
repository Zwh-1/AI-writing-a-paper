"""Read a balanced, snapshot-bound project evidence set before thesis writing.

File names and static edges are navigation only. Every usable observation is
an immutable evidence item carrying the exact bytes and line range read.
"""
from pathlib import PurePosixPath
import re
from copy import deepcopy
from . import db
from .writing_agent.common import digest

VERSION='project_grounding.v1'
CATEGORIES={
    'overview':r'(?:^|/)readme(?:\.[a-z-]+)?\.md$|(?:需求|项目说明|系统说明)',
    'configuration':r'(?:^|/)(?:package\.json|pyproject\.toml|cargo\.toml|hardhat\.config\.[jt]s)$',
    'entry':r'(?:^|/)(?:routes?|controllers?|pages?|app)(?:/|\.)|(?:^|/)(?:server|main)\.[a-z]+$',
    'business':r'(?:^|/)(?:services?|usecases?|domains?|modules?)/|service\.[jt]s$',
    'data':r'(?:^|/)(?:schema|models?|entities|migrations?|prisma|database)(?:/|\.)|\.(?:sql|prisma)$',
    'contracts':r'(?:^|/)contracts?/.*\.sol$',
    'permissions':r'(?:^|/)(?:auth|permissions?|security|guards?)(?:/|\.)|(?:auth|permission|guard|verifier)(?:service)?\.[jt]s$',
    'tests':r'(?:^|/)(?:tests?|test-support|__tests__|spec|mocks?|fixtures?)(?:/|\.)|\.(?:test|spec)\.',
}
QUESTIONS={
    'overview':'项目面向哪些对象和场景，解决什么问题？',
    'configuration':'项目实际声明了哪些技术和启动入口，配置与运行证据如何区分？',
    'entry':'用户从哪个入口发起业务，输入怎样进入后续处理？',
    'business':'关键业务经过哪些处理步骤，模块如何协作，失败时怎样处理？',
    'data':'哪些实体和状态参与业务，数据怎样创建、更新与关联？',
    'contracts':'应用合约承担哪些业务约束，链上与链下如何衔接？',
    'permissions':'谁可以执行哪些操作，权限、验证和异常分支在哪里落实？',
    'tests':'测试定义覆盖哪些行为，有无独立的执行记录？',
}
ORDER={
    'requirements':('overview','entry','business','permissions','data','configuration','contracts','tests'),
    'system_design':('overview','configuration','entry','business','data','contracts','permissions','tests'),
    'implementation':('overview','entry','business','contracts','data','permissions','configuration','tests'),
    'results':('overview','tests','business','permissions','configuration','contracts','entry','data'),
}
DEPENDENCY=re.compile(r'(?:^|/)(?:node_modules|vendor|third_party|third-party|external|dist|build|coverage|logs|archive|\.git)(?:/|$)',re.I)


def categories(path):
    result=[key for key,pattern in CATEGORIES.items() if re.search(pattern,path,re.I)]
    if PurePosixPath(path).suffix.lower() in {'.md','.txt','.rst'}:
        return [key for key in result if key=='overview']
    if PurePosixPath(path).suffix.lower() in {'.json','.yaml','.yml','.toml'}:
        return [key for key in result if key=='configuration']
    # A test named auth/model/service is evidence of test definitions, not a
    # substitute for the production permission, data or business modules.
    if 'tests' in result:return ['tests']
    return result


def _rank(path,query):
    words=set(re.findall(r'[a-z][a-z0-9_]{2,}',query.lower()))
    value=path.lower()
    app_source=bool(re.search(r'^(?:backend|frontend)/(?!scripts/|public/|deployments/)|^contracts/contracts/|^(?:src|app|server|client)/',value))
    shell_only=PurePosixPath(path).stem.lower() in {'index','types','interfaces','layout','errors'}
    return (0 if app_source or '/' not in path else 1,-sum(word in value for word in words),
            int(shell_only),len(PurePosixPath(path).parts),len(path),path)


def collect(pid,snapshot_id,section,query,existing=(),max_bytes=48000,max_observations=24):
    from .writing_agent import repository,evidence
    snapshot=repository.get_snapshot(pid,snapshot_id)
    binding=db.one('SELECT root_path_local FROM repository_bindings WHERE id=? AND project_id=?',(snapshot['binding_id'],pid))
    root_hint=(binding['root_path_local'] if binding else '').replace('\\','/').lower()
    diagnostic_source='/.runtime/' in root_hint and 'frozen' in root_hint
    files=db.rows('SELECT relative_path FROM repository_snapshot_files WHERE snapshot_id=? ORDER BY relative_path',(snapshot_id,))
    paths=[row['relative_path'] for row in files]
    own=[path for path in paths if not DEPENDENCY.search(path) and PurePosixPath(path).name not in
         {'AGENTS.md','AI_AGENT_RULES.md','CODEX.md','CLAUDE.md','GEMINI.md'}]
    # The actual project overview informs navigation relevance, never supplies
    # unobserved implementation facts. It is also registered below as an exact
    # reading range. Domain terms beat generic names such as migration/config.
    overview=next((path for path in own if path.lower()=='readme.md'),None)
    if overview:
        heading=repository.read(pid,snapshot_id,overview,1,35,remote=True)['text']
        query+=' '+' '.join(re.findall(r'\b[A-Za-z][A-Za-z0-9_]{2,}\b',heading)[:24])
        for term,aliases in {'学生':'student','凭证':'credential sbt token','档案':'profile record',
                             '发行':'issuer issuance','核验':'verifier verification','审批':'approval review',
                             '订单':'order','课程':'course','任务':'task','服务':'service'}.items():
            if term in heading:query+=' '+aliases
    order=ORDER.get(section.get('kind'),tuple(CATEGORIES))
    selected=[];by_category={}
    for category in order:
        ranked=sorted([path for path in own if category in categories(path)],key=lambda p:_rank(p,query))
        if category=='overview':ranked.sort(key=lambda path:(path.lower()!='readme.md',_rank(path,'')[0],len(PurePosixPath(path).parts),len(path),path))
        if category=='configuration':ranked.sort(key=lambda path:('/' in path,_rank(path,query)))
        if category=='business':ranked.sort(key=lambda path:(_rank(path,query)[0],
            not bool(re.search(r'(?:service|workflow|usecase)\.[a-z]+$',path,re.I)),_rank(path,query)))
        if category=='contracts':ranked.sort(key=lambda path:('/core/' not in path.lower(),_rank(path,query)))
        by_category[category]=ranked
    # Two passes avoid letting one subsystem consume the whole read budget.
    for position in range(2):
        for category in order:
            available=by_category[category]
            if position==0:
                candidate=available[0] if available else None
            else:
                first=available[0] if available else None
                def component(path):
                    parts=PurePosixPath(path).parts
                    if category=='business':
                        for marker in ('domain','domains','modules','services'):
                            if marker in parts and parts.index(marker)+1<len(parts)-1:return '/'.join(parts[:parts.index(marker)+2])
                        return '/'.join(parts[:-1])
                    return parts[0] if '/' in path else ''
                diverse=category in {'entry','overview','configuration','business','tests'}
                candidate=next((path for path in available[1:] if component(path)!=component(first)
                                and (_rank(path,query)[0]==_rank(first,query)[0] or category in {'overview','tests'})),None) if first and diverse else None
                if candidate is None:candidate=available[1] if len(available)>1 else None
            if candidate and candidate not in selected:selected.append(candidate)
    if not selected:selected=sorted(own,key=lambda p:_rank(p,query))[:6]
    # Follow actual resolved import paths for selected application entries.
    follow=[path for path in selected if categories(path)!=['tests']]
    if follow:
        marks=','.join('?' for _ in follow)
        dependencies=db.rows('SELECT DISTINCT target.relative_path FROM code_edges e '
            'JOIN code_units source ON source.id=e.from_unit_id JOIN code_units target ON target.id=e.to_unit_id '
            'WHERE e.snapshot_id=? AND e.relation=\'imports\' AND source.relative_path IN ('+marks+') '
            'ORDER BY target.relative_path',[snapshot_id,*follow])
        selected.extend(row['relative_path'] for row in dependencies if row['relative_path'] in own
                        and row['relative_path'] not in selected)
    chapter=None;items=[];readings=[];gaps=[];used=0;seen=set()
    if section.get('kind') in {'requirements','system_design','implementation','results'}:
        from .chapter_materials import collect as collect_structures
        chapter,items=collect_structures(pid,snapshot_id,section,own,query,existing,
                                        max_bytes=max_bytes//2,max_ranges=max(0,min(14,max_observations-6)))
        used=chapter['coverage']['read_bytes']
        for row in chapter['readings']:
            labels=set(categories(row['path']))
            for lane,label in (('database_tables','data'),('frontend_pages','entry'),('api_routes','entry'),('test_cases','tests')):
                if any(row['evidence_id'] in item['evidence_ids'] for item in chapter[lane]):labels.add(label)
            readings.append({**row,'categories':sorted(labels)})
            seen.add((row['path'],row['line_start'],row['line_end']))
    existing_by_range={}
    for item in existing:
        if item['origin_type']=='code' and item['origin_id']==snapshot_id:
            locator=item.get('locator') or db.loads(item.get('locator_json'),{})
            existing_by_range[(locator.get('path'),locator.get('line_start'),locator.get('line_end'))]=item
            observed=item.get('payload',{})
            if observed.get('path') and observed.get('line_start') and observed.get('line_end'):
                existing_by_range[(observed['path'],observed['line_start'],observed['line_end'])]=item
    for path in selected:
        file_unit=db.one("SELECT line_end FROM code_units WHERE snapshot_id=? AND relative_path=? AND kind='file' LIMIT 1",(snapshot_id,path))
        end=file_unit['line_end'] if file_unit else 90
        # A file head gives imports and setup; a complete named unit below it
        # provides behavior. Neither range implies the entire file was read.
        head_end=20 if 'configuration' in categories(path) else 35 if 'overview' in categories(path) else 70
        ranges=[(1,min(end,head_end))]
        units=db.rows("SELECT line_start,line_end,symbol FROM code_units WHERE snapshot_id=? AND relative_path=? AND kind IN ('function','contract','class','variable') ORDER BY line_start LIMIT 80",(snapshot_id,path))
        units.sort(key=lambda u:(-len(set(re.findall(r'[a-z][a-z0-9_]{2,}',query.lower())) &
                                         set(re.findall(r'[a-z][a-z0-9_]{2,}',u['symbol'].lower()))),u['line_start']))
        unit=next((u for u in units if u['line_end']>70 and u['line_end']-u['line_start']<=110),None)
        if unit:ranges.append((unit['line_start'],unit['line_end']))
        for start,stop in ranges:
            if len(items)>=max_observations:break
            key=(path,start,stop)
            if key in seen:continue
            seen.add(key)
            raw=repository.read(pid,snapshot_id,path,start,stop,remote=True)
            size=len(raw['text'].encode('utf-8'))
            if size>6000:
                gaps.append({'path':path,'line_start':start,'line_end':stop,'reason':'完整行范围超过单次阅读容量，需定向补读'});continue
            if not raw['text'].strip():continue
            if used+size>max_bytes:
                gaps.append({'path':path,'reason':'项目取证预算未覆盖此范围，不能视作已读'});continue
            locator={'path':path,'line_start':start,'line_end':stop}
            item=existing_by_range.get(key)
            if item is None:
                prior=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='code' AND origin_id=? AND locator_json=? AND validity='current'",(pid,snapshot_id,db.dumps(locator)))
                item=evidence.get(pid,prior['id'],remote=True) if prior else evidence.create(pid,{
                    'kind':'test_definition' if 'tests' in categories(path) else 'code_observation',
                    'origin_type':'code','origin_id':snapshot_id,'locator':locator,
                    'conditions':['仅为当前冻结快照中的实际行范围；配置、设计、实现、测试定义均不等于已运行或已部署。']})
            items.append(item);used+=size
            readings.append({'path':path,'categories':categories(path),'evidence_id':item['id'],
                             'line_start':start,'line_end':stop,'file_sha256':raw['file_sha256']})
        if len(items)>=max_observations:break
    questions=[];required=list(chapter['required_evidence_ids']) if chapter else []
    for category in order:
        ids=[row['evidence_id'] for row in readings if category in row['categories']]
        if ids:required.append(ids[0])
        questions.append({'id':category,'question':QUESTIONS[category],'evidence_ids':list(dict.fromkeys(ids)),
                          'status':'read_for_analysis' if ids else 'not_read_or_not_present',
                          'support':'not_assessed'})
    dossier={'schema':VERSION,'snapshot_id':snapshot_id,'readings':readings,'questions':questions,
             'source_binding_kind':'diagnostic_frozen_copy' if diagnostic_source else 'bound_repository',
             'candidate_files':list(dict.fromkeys([*selected,*[path for category in order for path in by_category[category][:8]]]))[:100],
             'required_evidence_ids':list(dict.fromkeys(required)),
             'gaps':gaps,'coverage':{'repository_files':len(paths),'read_files':len({r['path'] for r in readings}),
                                  'read_ranges':len(readings),'read_bytes':used,'complete_repository_read':False},
             'policy':'这是实际阅读登记和调查问题，不是已证实的项目说明。文件名、目录与静态依赖只作导航；必须依据所附原文解释角色、流程、状态与约束，未覆盖问题保留缺口。'}
    if chapter and any(chapter[lane] for lane in ('database_tables','frontend_pages','api_routes','test_cases')):
        dossier['chapter_materials']=chapter
        dossier['candidate_files']=list(dict.fromkeys([*chapter['candidate_files'],*dossier['candidate_files']]))[:100]
        dossier['gaps'].extend(chapter['gaps'])
    dossier['hash']=digest(dossier)
    return dossier,items


def available(dossier,ids):
    """A packed request may never cite an omitted observation as supplied."""
    value=deepcopy(dossier);allowed=set(ids)
    value['readings']=[row for row in value['readings'] if row['evidence_id'] in allowed]
    for row in value['questions']:
        row['evidence_ids']=[eid for eid in row['evidence_ids'] if eid in allowed]
        if not row['evidence_ids']:row['status']='not_supplied'
    value['required_evidence_ids']=[eid for eid in value['required_evidence_ids'] if eid in allowed]
    value['coverage']['supplied_ranges']=len(value['readings'])
    value.pop('hash',None);value['hash']=digest(value)
    return value
