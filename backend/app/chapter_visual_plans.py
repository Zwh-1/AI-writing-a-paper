"""Deterministic diagram/table plans from actual, supplied chapter materials."""
from copy import deepcopy
import re
from .writing_agent.common import digest


def target_groups(section):
    kind=section.get('kind');goal=section.get('goal','')
    generic=not goal or goal.startswith(('按已确认规则','组织本节内容'))
    text=section.get('title','') if generic else goal
    mapping={'database':r'数据库|数据模型|表结构|字段','frontend':r'前端|页面|界面|交互',
             'api':r'接口|路由','testing':r'测试|实验|验证','execution':r'结果|执行|实测|测量','requirements':r'需求|场景|角色'}
    selected={key for key,pattern in mapping.items() if re.search(pattern,text)}
    defaults={'requirements':{'requirements'},'system_design':{'database','frontend','api'},
              'implementation':{'frontend','api'},'results':{'testing','execution'}}
    return selected & defaults.get(kind,set()) if selected or not generic else defaults.get(kind,set())


def _core_fields(table, limit=10):
    required=set(table['primary_key'])
    for key in table['unique_keys']:required.update(key)
    for foreign in table['foreign_keys']:required.update(foreign['columns'])
    fields=table['fields']
    # Keep whole compound keys even when they exceed the usual summary size.
    chosen=[field for field in fields if field['name'] in required]
    chosen.extend(field for field in fields if field['name'] not in required and
                  re.search(r'status|state|name|title|type|kind|role',field['name'],re.I))
    chosen.extend(field for field in fields if field['name'] not in required and
                  field not in chosen and not re.search(r'(?:created|updated|deleted)_?at$|timestamp$',field['name'],re.I))
    chosen.extend(field for field in fields if field not in chosen)
    selected={field['name'] for field in chosen[:max(limit,len(required))]}
    return [field for field in fields if field['name'] in selected]


def _constraint_note(table):
    notes=[]
    if len(table['primary_key'])>1:notes.append('联合主键（'+', '.join(table['primary_key'])+'）')
    notes.extend('联合唯一（'+', '.join(key)+'）' for key in table['unique_keys'] if len(key)>1)
    notes.extend('（'+', '.join(fk['columns'])+'）引用 '+fk['target_table']+'（'+', '.join(fk['target_columns'])+'）'
                 for fk in table['foreign_keys'])
    return '；'.join(notes)


def build(section,materials):
    kind=section.get('kind');plans=[]
    def add(group,spec,rows,task,terms):
        if not rows:return
        ids=list(dict.fromkeys(eid for row in rows for eid in row['evidence_ids']))
        plans.append({'id':'visualplan-'+digest([materials['snapshot_id'],kind,group,spec])[:24],
                      'group':group,'spec':spec,'material_ids':[row['id'] for row in rows],
                      'evidence_ids':ids,'paragraph_task':task,'terms':list(dict.fromkeys(terms)),
                      'policy':'本项是候选，按本节问题选用；解释具体关系和设计理由，不逐格复读。来源和阅读范围保留在记录中，不复制进图题。'})
    tables=materials['database_tables'];pages=materials['frontend_pages'];routes=materials['api_routes'];tests=materials['test_cases']
    if kind=='system_design':
        for table in tables:
            if table['conflicting_definitions'] or not table['complete_definition']:continue
            fields=_core_fields(table)
            if len(table['name'])>70 or len(fields)>20:continue
            if any(len(field['data_type'])>900 for field in fields):continue
            compound_pk=len(table['primary_key'])>1
            def constraints(field):
                return ' / '.join({'PK':'联合主键成员' if compound_pk else '主键',
                                   'FK':'外键','UNIQUE':'唯一','NOT NULL':'非空',
                                   'AUTO_INCREMENT':'自增'}.get(c,c) for c in field['constraints']) or '—'
            headers=['字段','数据类型','约束','默认值']
            if any(field.get('comment') for field in fields):headers.append('说明')
            rows=[]
            for field in fields:
                row=[field['name'],field['data_type'],constraints(field),field.get('default') or '—']
                if len(headers)==5:row.append(field.get('comment') or '—')
                rows.append(row)
            note=_constraint_note(table)
            if len(fields)<len(table['fields']):note=('仅列关键字段，完整定义见建表源码。'+note)
            spec={'kind':'three_line_table','title':table['name']+'表关键字段设计',
                  'headers':headers,'rows':rows,'note':note}
            add('database',spec,[table],
                '解释'+table['name']+'关键字段承担的业务信息、标识与约束选择；联合约束按字段组分析，不把成员当作单列唯一。',
                [table['name'],*[field['name'] for field in fields[:4]]])
        known={table['name']:table for table in tables if table['complete_definition'] and not table['conflicting_definitions']}
        relations=[]
        for child in known.values():
            for foreign in child['foreign_keys']:
                parent=known.get(foreign['target_table'])
                if not parent or len(foreign['columns'])!=len(foreign['target_columns']):continue
                referenced=set(foreign['target_columns'])
                # A non-unique referenced index does not establish a single parent.
                if not any(key and set(key)<=referenced for key in [parent['primary_key'],*parent['unique_keys']]):continue
                relations.append((child,parent,foreign))
        if relations:
            selected=list({row['name']:row for child,parent,_ in relations for row in (child,parent)}.values())[:6]
            node_ids={row['name']:'t'+str(i) for i,row in enumerate(selected)}
            relations=[r for r in relations if r[0]['name'] in node_ids and r[1]['name'] in node_ids]
            entities=[];shown={}
            for table in selected:
                needed=set(table['primary_key'])
                for child,parent,foreign in relations:
                    if child['name']==table['name']:needed.update(foreign['columns'])
                    if parent['name']==table['name']:needed.update(foreign['target_columns'])
                core=[field for field in table['fields'] if field['name'] in needed]
                core.extend(field for field in _core_fields(table,4) if field not in core)
                shown[table['name']]={field['name'] for field in core}
                fields=[]
                for field in core:
                    key='PK/FK' if {'PK','FK'}<=set(field['constraints']) else 'PK' if 'PK' in field['constraints'] else 'FK' if 'FK' in field['constraints'] else ''
                    data_type=field['data_type']
                    if len(data_type)>30:data_type=re.match(r'\w+',data_type)[0]
                    fields.append({'name':field['name'],'data_type':data_type,'key':key})
                entities.append({'id':node_ids[table['name']],'label':table['name'],'fields':fields})
            edges=[]
            for child,parent,foreign in relations:
                columns=set(foreign['columns'])
                if not columns<=shown[child['name']] or not set(foreign['target_columns'])<=shown[parent['name']]:continue
                unique=any(key and set(key)<=columns for key in [child['primary_key'],*child['unique_keys']])
                required=all(any(field['name']==column and ('NOT NULL' in field['constraints'] or 'PK' in field['constraints']) for field in child['fields']) for column in columns)
                label=', '.join(foreign['columns'])+' → '+', '.join(foreign['target_columns'])
                edges.append({'source':node_ids[child['name']],'target':node_ids[parent['name']],
                              'label':label if len(label)<=60 else '外键引用',
                              'source_cardinality':'0..1' if unique else '0..N','target_cardinality':'1' if required else '0..1'})
            if edges:
                add('database',{'kind':'er','title':'核心数据表的外键关系','entities':entities,'edges':edges},selected,
                    '解释外键两端的字段对应和可空/唯一条件；父记录可以没有子记录，唯一外键也不保证每个父记录恰有一个子记录。图为物理引用关系，不能当作完整概念模型。',
                    [row['name'] for row in selected])
    if pages and kind in {'requirements','system_design'}:
        # Source-backed UI text describes an interface; a path alone is not a requirement.
        meaningful=[page for page in pages if page.get('labels')]
        if meaningful:
            group='requirements' if kind=='requirements' else 'frontend'
            if kind=='requirements':
                title='功能入口与操作要素';headers=['功能入口','界面操作要素']
                rows=[[page['labels'][0],'、'.join(page['labels'][1:5]) or page['labels'][0]] for page in meaningful]
                task='结合入口及操作文案说明使用场景；角色权限、系统响应和异常需要对应处理逻辑，不能从页面路径猜测。'
            else:
                title='核心页面的界面组成';headers=['页面','界面要素','控件类型']
                rows=[[page['labels'][0],'、'.join(page['labels'][1:5]) or '—',
                       '、'.join(dict.fromkeys(c['tag'] for c in page.get('controls',[]))) or '—'] for page in meaningful]
                task='解释页面如何组织用户输入与操作，围绕实际界面要素分析设计，不列函数调用清单。'
            add(group,{'kind':'three_line_table','title':title,'headers':headers,'rows':rows,'note':''},meaningful,task,
                [label for page in meaningful for label in page['labels'][:5]])
    if pages and kind=='implementation':
        # One bounded graph per feature keeps unrelated roles and long symbols apart.
        for page in pages[:2]:
            if not page.get('components') or not page.get('api_calls'):continue
            component=page['components'][0]
            label=page.get('labels',[page['route']])[0] if page.get('labels') else page['route']
            nodes=[{'id':'page','label':label,'shape':'process'},
                   {'id':'component','label':component['name'],'shape':'process'}]
            edges=[{'source':'page','target':'component','label':'页面实现'}]
            for index,call in enumerate(page['api_calls'][:2]):
                key='call'+str(index);nodes.append({'id':key,'label':call['symbol'],'shape':'process'})
                edges.append({'source':'component','target':key,'label':'调用'})
            add('frontend',{'kind':'structure','title':(label+'功能调用关系') if page.get('labels') else component['name']+'的调用关系','nodes':nodes,'edges':edges},[page],
                '解释该功能的实现模块与具体调用职责；这是静态依赖关系，运行先后、校验及异常处理须另据处理函数。',
                [page['route'],component['name'],*[call['symbol'] for call in page['api_calls']]])
    if routes and kind=='implementation':
        add('api',{'kind':'three_line_table','title':'核心接口与请求方式','headers':['请求方式','路由片段'],
                   'rows':[[row['method'],row['route']] for row in routes],
                   'note':'路由片段以服务端声明为准，完整地址还包含挂载前缀。'},routes,
            '解释这些接口分别服务什么业务动作及边界，处理逻辑没有给出时不能补造校验、事务或返回结果。',[row['route'] for row in routes])
    if tests and kind=='results':
        mode={'normal':'未提供执行记录','skip':'已跳过（skip）','todo':'待实现（todo）','only':'仅选定执行（only）；未提供执行记录'}
        add('testing',{'kind':'three_line_table','title':'测试设计与执行状态','headers':['用例定义','断言方式','执行状态'],
                       'rows':[[row['name'],'、'.join(row['assertions']) or '未声明断言',mode.get(row.get('declared_mode','normal'),'未提供执行记录')] for row in tests],
                       'note':'用例定义用于说明测试设计，执行结论须以对应日志为依据。'},tests,
            '解释用例关注的具体条件与边界；若仅有名称和断言方法，明确不能据此补造输入、预期或判定通过。',
            [row['name'] for row in tests])
    runs=materials.get('execution_records',[])
    if runs and kind=='results':
        add('execution',{'kind':'three_line_table','title':'测试执行记录','headers':['记录','执行命令','退出状态','适用范围'],
                       'rows':[[row['name'],row['command'],row['outcome'],str(row['scope'])] for row in runs],
                       'note':'退出码不等于逐例通过率；失败、超时和执行范围按日志保留。'},runs,
            '对照命令、退出状态和范围解释结果及失败原因，不把退出码改写为全部用例通过。',[row['name'] for row in runs])
    allowed=target_groups(section)
    return [plan for plan in plans if plan['group'] in allowed][:12]


def for_card(card,plans,index,total):
    selected=[plan for at,plan in enumerate(plans) if at%max(1,total)==index]
    return {**card,'visual_plan_ids':[row['id'] for row in selected],
            'project_material_ids':list(dict.fromkeys(mid for row in selected for mid in row['material_ids'])),
            'visual_argument_tasks':[row['paragraph_task'] for row in selected]}


def attach_cards(cards,plans):
    matches={'database':r'数据|实体|字段|模型','frontend':r'前端|界面|页面|模块|代码结构',
             'api':r'接口|流程|执行顺序','requirements':r'功能需求|场景|流程|需求',
             'testing':r'设置|用例|测试|实验','execution':r'执行过程|指标|结果|讨论'}
    assigned={index:[] for index in range(len(cards))}
    for at,plan in enumerate(plans):
        target=next((index for index,card in enumerate(cards) if re.search(matches[plan['group']],card.get('question',''))),at%max(1,len(cards)))
        if cards:assigned[target].append(plan)
    return [for_card(card,assigned[index],0,1) for index,card in enumerate(cards)]


def candidate_problem(candidate,context,previous=()):
    materials=context.get('chapter_materials')
    if not materials:
        return '本次未提供项目材料或图表计划，不能关联project_material_ids或visual_plan_ids。' if any(
            row.get('project_material_ids') or row.get('visual_plan_ids') for row in candidate.get('paragraphs',[])) else ''
    known={row['id']:row for lane in ('database_tables','frontend_pages','api_routes','test_cases','execution_records') for row in materials.get(lane,[])}
    plans={row['id']:row for row in materials['visual_plans']}
    used={pid for row in previous for pid in row.get('visual_plan_ids',[])}
    for paragraph in candidate.get('paragraphs',[]):
        material_ids=paragraph.get('project_material_ids',[]);plan_ids=paragraph.get('visual_plan_ids',[])
        if plan_ids and paragraph.get('preserve_node_id'):return '受保护或保留段落不能重写为新图表说明，请使用新的解释段落。'
        if len(plan_ids)>1:return '每个首次关联的图表需要独立解释段落，不能把多个visual_plan_ids塞入同一段。'
        if set(material_ids)-set(known) or set(plan_ids)-set(plans):return '段落关联了本次未提供的项目材料或图表计划，不能杜撰编号。'
        evidence=set(paragraph.get('evidence_ids',[]))
        for span in paragraph.get('claim_spans',[]):evidence.update(span.get('evidence_ids',[]))
        if any(not set(known[mid]['evidence_ids'])<=evidence for mid in material_ids):
            return '段落关联的项目材料没有回填实际读取的全部evidence_ids；材料编号不能代替原始依据。'
        for pid in plan_ids:
            if pid in used:return '同一图表计划重复生成；应承接已有图表，仅在首次解释段落关联visual_plan_ids。'
            if not set(plans[pid]['evidence_ids'])<=evidence:return '图表解释段落未绑定该计划实际读取的全部依据，请按计划回填evidence_ids，不编造来源。'
            used.add(pid)
    return ''


def proposals(candidate,context,paragraph_node_ids=None):
    materials=context.get('chapter_materials') or {};plans={row['id']:row for row in materials.get('visual_plans',[])}
    problem=candidate_problem(candidate,context)
    if problem:raise ValueError(problem)
    result=[]
    for index,paragraph in enumerate(candidate['paragraphs']):
        for pid in paragraph.get('visual_plan_ids',[]):
            plan=plans[pid]
            value={'after_paragraph':index,'spec':deepcopy(plan['spec']),'evidence_ids':plan['evidence_ids'],
                           'explanation':paragraph['text'],'explanation_in_paragraph':True,'place_before_explanation':True,
                           'visual_plan_id':pid,'project_material_ids':plan['material_ids'],'source_material_group':plan['group']}
            if paragraph_node_ids is not None:
                if not paragraph_node_ids[index]:raise ValueError('图表计划没有对应的可编辑解释段落')
                value['after_node_id']=paragraph_node_ids[index]
            result.append(value)
    return result


def findings(section,candidate,context):
    materials=context.get('chapter_materials')
    if not materials or context.get('operation')!='generate' or context.get('scope')=='selected_paragraphs':return []
    plans={row['id']:row for row in materials['visual_plans']};used=set()
    for paragraph in candidate.get('paragraphs',[]):used.update(paragraph.get('visual_plan_ids',[]))
    # Equivalent direct proposals are accepted; the supplied source-bound
    # structure remains the comparison, not a generic illustration template.
    for proposal in candidate.get('visuals',[]):
        for pid,plan in plans.items():
            if proposal.get('spec')==plan['spec'] and set(plan['evidence_ids'])<=set(proposal.get('evidence_ids',[])):used.add(pid)
    if re.search(r'纯文字|仅(?:输出|生成)文字|不(?:插入|生成|使用)图表',context.get('instruction','')):return []
    request_text=' '.join(str(value or '') for value in (context.get('instruction'),section.get('goal'),section.get('title')))
    requested={kind for kind,pattern in {'er':r'(?i:ER)\s*图|实体关系图','use_case':r'用例图',
               'flowchart':r'流程图','sequence':r'时序图','architecture':r'架构图'}.items() if re.search(pattern,request_text)}
    supplied_kinds={plans[pid]['spec']['kind'] for pid in used if pid in plans}
    supplied_kinds.update(row.get('spec',{}).get('kind') for row in candidate.get('visuals',[]) if row.get('evidence_ids'))
    missing=requested-supplied_kinds
    if missing:
        return [{'kind':'chapter_material_unused','severity':'warning','paragraph_index':0,
                 'message':'本节明确要求的图种尚未提供：'+kind+'；需按实际依据生成，材料不足则补读并说明缺口。',
                 'repair_query':'根据本节明确图种要求补充真实元素及关系，不用其他图表替代。',
                 'method':'explicit_visual_requirement','semantic_support':'not_granted'} for kind in sorted(missing)]
    if used & set(plans):return []
    if any(row.get('project_material_ids') or any(term and term in row.get('text','') for plan in plans.values() for term in plan['terms']) for row in candidate.get('paragraphs',[])):return []
    required=target_groups(section) & {plan['group'] for plan in plans.values()}
    covered={plans[pid]['group'] for pid in used if pid in plans}
    result=[]
    for group in sorted(required-covered):
        plan=next(plan for plan in plans.values() if plan['group']==group)
        points={point for card in context.get('argument_plan',{}).get('paragraph_cards',[])
                if plan['id'] in card.get('visual_plan_ids',[]) for point in card.get('point_ids',[])}
        at=next((index for index,row in enumerate(candidate.get('paragraphs',[]))
                 if not row.get('preserve_node_id') and points & set(row.get('brief_points',[]))),0)
        result.append({'kind':'chapter_material_unused','severity':'warning','paragraph_index':at,
                       'message':'本节尚未结合具体项目材料展开说明，请补充与本节问题有关的字段、操作或测试条件；可选用候选图表：'+plan['spec']['title'],
                       'repair_query':plan['paragraph_task'],'visual_plan_id':plan['id'],
                       'method':'project_material_visual_coverage','semantic_support':'not_granted'})
    return result
