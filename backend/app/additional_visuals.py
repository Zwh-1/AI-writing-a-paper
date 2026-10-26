"""Additional thesis design diagrams. All examples are illustrative, not project facts."""
import math

LABELS={'architecture':'架构图','module':'功能模块图','class_diagram':'类图','sequence':'时序图',
        'data_flow':'数据流图','contract_interaction':'合约交互图','circuit':'电路设计图','structure':'结构示意图'}
GUIDANCE={
 'structure':'说明对象的组成、连接、分类或拓扑关系，保持实际结构，不强加业务流程或层次。',
 'architecture':'明确架构模式、层次、各层组件及层间交互。只画实际存在的层；非区块链项目不得添加区块链层。',
 'module':'显示功能模块的分解与层级，名称须与需求和实现章节一致。不要把例文模块移入本项目。',
 'class_diagram':'使用 UML 类框，分别列类名、属性、操作，并正确区分关联、依赖、继承、聚合和组合。类名应来自实际设计或源码。',
 'sequence':'按时间自上而下列参与者、生命线和消息，说明请求、服务处理与返回；只在项目实际使用区块链时添加链上参与者。',
 'data_flow':'显示外部实体、处理过程、数据存储与有名称的数据流。每条数据流应能说明起点、终点和处理步骤。',
 'contract_interaction':'清楚显示钱包、后端、预言机和合约中的实际参与方，标出调用方向和链上、链下边界，不虚构合约。',
 'circuit':'只根据给定设计显示输入、运算或约束模块、连线与输出；设计示意不能冒充实际电路运行或实验结果。',
}
TEMPLATES={
 'structure':{'kind':'structure','title':'组成结构设计示例','nodes':[{'id':'core','label':'核心组件'},{'id':'store','label':'存储组件'},{'id':'external','label':'外部组件'}],'edges':[{'source':'core','target':'store','label':'数据连接'},{'source':'core','target':'external','label':'接口连接'}]},
 'architecture':{'kind':'architecture','title':'分层架构设计示例','layers':[
  {'id':'presentation','label':'表现层','components':[{'id':'web','label':'用户界面'}]},
  {'id':'business','label':'业务层','components':[{'id':'service','label':'业务服务'}]},
  {'id':'data','label':'数据层','components':[{'id':'database','label':'数据库'}]}],
  'edges':[{'source':'web','target':'service','label':'业务请求'},{'source':'service','target':'database','label':'数据读写'}]},
 'module':{'kind':'module','title':'功能模块设计示例','nodes':[{'id':'system','label':'业务系统'},{'id':'account','label':'账户模块'},{'id':'records','label':'记录模块'},{'id':'profile','label':'资料维护'},{'id':'query','label':'记录查询'}],
  'edges':[{'source':'system','target':'account'},{'source':'system','target':'records'},{'source':'account','target':'profile'},{'source':'records','target':'query'}]},
 'class_diagram':{'kind':'class_diagram','title':'业务类设计示例','classes':[
  {'id':'controller','label':'RecordController','attributes':['- service: RecordService'],'operations':['+ query(id): Record']},
  {'id':'service','label':'RecordService','attributes':['- repository: Repository'],'operations':['+ find(id): Record']}],
  'edges':[{'source':'controller','target':'service','relation':'dependency','label':'调用'}]},
 'sequence':{'kind':'sequence','title':'业务请求时序示例','participants':[{'id':'user','label':'用户','kind':'actor'},{'id':'web','label':'前端','kind':'component'},{'id':'api','label':'后端服务','kind':'component'}],
  'messages':[{'source':'user','target':'web','label':'提交请求','kind':'sync'},{'source':'web','target':'api','label':'发送业务请求','kind':'sync'},{'source':'api','target':'web','label':'返回处理结果','kind':'return'},{'source':'web','target':'user','label':'展示结果','kind':'return'}]},
 'data_flow':{'kind':'data_flow','title':'数据流设计示例','nodes':[{'id':'user','label':'用户','shape':'entity'},{'id':'validate','label':'校验处理','shape':'process'},{'id':'store','label':'业务记录','shape':'storage'}],
  'edges':[{'source':'user','target':'validate','label':'输入数据','processing':'检查必填项与数据格式'},{'source':'validate','target':'store','label':'有效记录','processing':'通过校验后写入存储'}]},
 'contract_interaction':{'kind':'contract_interaction','title':'合约交互设计示例','nodes':[{'id':'wallet','label':'用户钱包','location':'user'},{'id':'server','label':'后端服务','location':'off_chain'},{'id':'contract','label':'业务合约','location':'on_chain'}],
  'edges':[{'source':'wallet','target':'server','label':'提交业务请求'},{'source':'server','target':'contract','label':'调用合约方法'},{'source':'contract','target':'server','label':'返回调用结果'}]},
 'circuit':{'kind':'circuit','title':'约束电路设计示例','nodes':[{'id':'input','label':'输入数据'},{'id':'hash','label':'哈希运算'},{'id':'compare','label':'约束检查'},{'id':'output','label':'输出约束结果'}],
  'edges':[{'source':'input','target':'hash','label':'原像'},{'source':'hash','target':'compare','label':'计算结果'},{'source':'compare','target':'output','label':'满足 / 不满足'}]},
}


def validate(raw,text,identifier):
    kind=raw['kind'];result={'kind':kind,'title':text(raw.get('title'),'图题',100)}
    seen=set()
    def node(raw_node):
        if not isinstance(raw_node,dict):raise ValueError('图节点须为对象')
        key=identifier(raw_node.get('id'))
        if key in seen:raise ValueError('节点 id 重复：'+key)
        seen.add(key)
        return {'id':key,'label':text(raw_node.get('label'),'节点名称',80)}
    if kind=='architecture':
        layers=raw.get('layers')
        if not isinstance(layers,list) or not 1<=len(layers)<=7:raise ValueError('架构图须有1–7层')
        result['layers']=[]
        for layer in layers:
            if not isinstance(layer,dict):raise ValueError('层次须为对象')
            item={'id':identifier(layer.get('id')),'label':text(layer.get('label'),'层次名称',60)}
            values=layer.get('components')
            if not isinstance(values,list) or not 1<=len(values)<=6:raise ValueError('每层须有1–6个组件')
            item['components']=[node(n) for n in values];result['layers'].append(item)
        layer_ids=[x['id'] for x in result['layers']]
        if len(layer_ids)!=len(set(layer_ids)):raise ValueError('架构层 id 重复')
    elif kind=='class_diagram':
        values=raw.get('classes')
        if not isinstance(values,list) or not 1<=len(values)<=12:raise ValueError('类图须有1–12个类')
        result['classes']=[]
        for value in values:
            item=node(value)
            for key in ('attributes','operations'):
                members=value.get(key,[])
                if not isinstance(members,list) or len(members)>12:raise ValueError('每个类最多12个属性和12个操作')
                item[key]=[text(x,'类成员',100) for x in members]
            result['classes'].append(item)
    elif kind=='sequence':
        values=raw.get('participants')
        if not isinstance(values,list) or not 2<=len(values)<=8:raise ValueError('时序图须有2–8个参与者')
        result['participants']=[]
        for value in values:
            item=node(value);role=value.get('kind','component')
            if role not in ('actor','component','contract','database'):raise ValueError('时序参与者类型无效')
            item['kind']=role;result['participants'].append(item)
    else:
        values=raw.get('nodes')
        if not isinstance(values,list) or not 1<=len(values)<=20:raise ValueError('图须有1–20个节点')
        result['nodes']=[]
        for value in values:
            item=node(value);shape=value.get('shape','process')
            if shape not in ('process','decision','entity','storage','start','end'):raise ValueError('不支持的节点形状')
            item['shape']=shape
            if value.get('location') is not None:
                if value['location'] not in ('user','off_chain','on_chain','external'):raise ValueError('节点位置标注无效')
                item['location']=value['location']
            result['nodes'].append(item)
    key='messages' if kind=='sequence' else 'edges';values=raw.get(key,[])
    if not isinstance(values,list) or len(values)>40 or any(not isinstance(v,dict) for v in values):raise ValueError('连线或消息须为不超过40项的数组')
    result[key]=[];duplicates=set()
    for value in values:
        source,target=value.get('source'),value.get('target')
        if not isinstance(source,str) or not isinstance(target,str) or source not in seen or target not in seen:raise ValueError('连线须指向现有节点')
        item={'source':source,'target':target,'label':text(value.get('label',''),'关系或消息文字',100,kind not in ('sequence','data_flow'))}
        if kind=='sequence':
            style=value.get('kind','sync')
            if style not in ('sync','async','return'):raise ValueError('时序消息使用 sync、async 或 return')
            item['kind']=style
        elif kind=='class_diagram':
            style=value.get('relation','association')
            if style not in ('association','dependency','inheritance','aggregation','composition'):raise ValueError('类关系类型无效')
            item['relation']=style
        if kind=='data_flow':item['processing']=text(value.get('processing',''),'处理步骤',500,True)
        identity=(source,target,item.get('relation'),item.get('kind'),item['label'])
        if identity in duplicates and kind!='sequence':raise ValueError('存在完全重复的连线')
        duplicates.add(identity);result[key].append(item)
    if kind in ('sequence','data_flow','architecture','module') and not values:raise ValueError(LABELS[kind]+'必须说明组件之间的关系或交互')
    if kind=='module':
        graph={key:[] for key in seen};active=set();done=set()
        for edge in result['edges']:graph[edge['source']].append(edge['target'])
        def visit(key):
            if key in active:raise ValueError('功能模块层级不能形成循环')
            if key in done:return
            active.add(key)
            for child in graph[key]:visit(child)
            active.remove(key);done.add(key)
        for key in graph:visit(key)
    if kind=='class_diagram':
        graph={key:[] for key in seen};active=set();done=set()
        for edge in result['edges']:
            if edge['relation']=='inheritance':graph[edge['source']].append(edge['target'])
        def visit_class(key):
            if key in active:raise ValueError('类继承关系不能形成循环或继承自身')
            if key in done:return
            active.add(key)
            for parent in graph[key]:visit_class(parent)
            active.remove(key);done.add(key)
        for key in graph:visit_class(key)
    return result


def flow_table(spec):
    if spec['kind']!='data_flow':raise ValueError('请选择数据流图')
    labels={n['id']:n['label'] for n in spec['nodes']}
    return {'kind':'three_line_table','title':'数据流与处理步骤说明','headers':['数据流名称','起点','终点','主要处理步骤'],
            'rows':[[e['label'],labels[e['source']],labels[e['target']],e.get('processing') or '【待补处理步骤】'] for e in spec['edges']],
            'note':'根据当前数据流结构整理，请核对与实际设计是否一致。'}


def render(spec):
    from .paper_visual_render import Canvas,_route
    from .paper_visual_layout import text_size,_place_label,_flow_nodes
    kind=spec['kind'];frames=[];positions=[]
    if kind=='sequence':
        participants=spec['participants'];width=max(800,120+len(participants)*220)
        heights=[max(85,text_size(m['label'],18,220)[1]+40) for m in spec['messages']]
        height=max(540,250+sum(heights));canvas=Canvas(width,height)
        canvas.text((40,20,width-40,80),spec['title'],24)
        xs={n['id']:100+i*(width-200)/max(1,len(participants)-1) for i,n in enumerate(participants)}
        for n in participants:
            x=xs[n['id']];canvas.shape((x-85,105,x+85,162));canvas.text((x-80,110,x+80,157),n['label'],19)
            canvas.line([(x,162),(x,height-35)],dash=True)
        y=210
        for i,message in enumerate(spec['messages']):
            a,b=xs[message['source']],xs[message['target']];h=heights[i]
            points=[(a,y+h-22),(b,y+h-22)] if a!=b else [(a,y+15),(a+70,y+15),(a+70,y+h-22),(a,y+h-22)]
            canvas.arrow(points,dash=message['kind']=='return')
            if message['kind']=='sync':
                x,end_y=points[-1];sign=1 if a<b else -1
                canvas.draw.polygon([canvas.coords((x,end_y)),canvas.coords((x-sign*10,end_y-5)),canvas.coords((x-sign*10,end_y+5))],fill='#222222')
            left=min(a,b);right=max(a,b)
            if right-left<180:left=max(20,a-90);right=min(width-20,a+150)
            canvas.text((left+8,y,right-8,y+h-30),message['label'],18,background=True);y+=h
        return canvas.result()
    if kind=='architecture':
        rows=[]
        for layer in spec['layers']:
            count=len(layer['components']);rows.append((math.ceil(count/3),max(145,65+math.ceil(count/3)*110)))
        width=1180;height=max(540,145+sum(h for _,h in rows)+100*(len(rows)-1))
        y=125
        for layer,(_,h) in zip(spec['layers'],rows):
            frames.append(((50,y,width-50,y+h),layer['label']))
            for i,node in enumerate(layer['components']):
                x=235+(i%3)*290;top=y+40+(i//3)*110
                positions.append({**node,'box':(x,top,x+240,top+70)})
            y+=h+100
    elif kind=='class_diagram':
        columns=min(2,len(spec['classes']));row_heights=[];info={}
        for node in spec['classes']:
            groups=[node['attributes'] or [' '],node['operations'] or [' ']]
            heights=[max(54,sum(text_size(line,22,300)[1]+8 for line in group)+12) for group in groups]
            info[node['id']]={'groups':groups,'heights':heights,'height':58+sum(heights)}
        for i in range(0,len(spec['classes']),columns):row_heights.append(max(info[n['id']]['height'] for n in spec['classes'][i:i+columns]))
        width=max(760,columns*510+150);height=max(540,160+sum(row_heights)+160*(len(row_heights)-1))
        for i,node in enumerate(spec['classes']):
            row,col=divmod(i,columns);x=80+col*510;y=130+sum(row_heights[:row])+row*160
            positions.append({**node,**info[node['id']],'box':(x,y,x+330,y+info[node['id']]['height'])})
    else:
        converted={'nodes':[{**n,'data_shape':n.get('shape','process'),'shape':n.get('shape','process') if n.get('shape') in ('decision','start','end') else 'process'} for n in spec['nodes']],'edges':spec['edges']}
        width,height,positions=_flow_nodes(converted)
    by_id={n['id']:n for n in positions};obstacles=[n['box'] for n in positions];paths=[];routes=[]
    for edge in spec['edges']:
        a,b=by_id[edge['source']]['box'],by_id[edge['target']]['box']
        ay,by=(a[1]+a[3])/2,(b[1]+b[3])/2
        ports=('right','top') if edge['source']==edge['target'] else ('bottom','top') if b[1]>a[3] else ('top','bottom') if a[1]>b[3] else ('right','left') if a[0]<b[0] else ('left','right')
        points=_route(a,b,obstacles,width,height,ports,used=paths);paths.append(points);routes.append((edge,points))
    canvas=Canvas(width,height);canvas.text((40,20,width-40,80),spec['title'],24)
    for box,label in frames:
        canvas.shape(box);canvas.text((box[0]+8,box[1]+8,220,box[3]-8),label,20)
    for edge,points in routes:
        relation=edge.get('relation','association')
        arrow=kind not in ('module','structure') and not (kind=='class_diagram' and relation in ('association','aggregation','composition'))
        canvas.arrow(points,dash=relation=='dependency',arrow=arrow and relation!='inheritance')
        if kind=='class_diagram' and relation=='inheritance':
            a,b=points[-2:];angle=math.atan2(b[1]-a[1],b[0]-a[0]);p=[b,(b[0]-16*math.cos(angle-.45),b[1]-16*math.sin(angle-.45)),(b[0]-16*math.cos(angle+.45),b[1]-16*math.sin(angle+.45))]
            canvas.draw.polygon([canvas.coords(v) for v in p],fill='white',outline='#222222',width=2)
        if kind=='class_diagram' and relation in ('aggregation','composition'):
            a,b=points[:2];angle=math.atan2(b[1]-a[1],b[0]-a[0]);dx,dy=math.cos(angle),math.sin(angle)
            p=[a,(a[0]+9*dx-5*dy,a[1]+9*dy+5*dx),(a[0]+18*dx,a[1]+18*dy),(a[0]+9*dx+5*dy,a[1]+9*dy-5*dx)]
            canvas.draw.polygon([canvas.coords(v) for v in p],fill='#222222' if relation=='composition' else 'white',outline='#222222',width=2)
    locations={'user':'用户侧','on_chain':'链上','off_chain':'链下','external':'外部接口'}
    for node in positions:
        box=node['box']
        if kind=='data_flow' and node.get('data_shape')=='storage':
            canvas.line([(box[0],box[1]),(box[2],box[1])]);canvas.line([(box[0],box[3]),(box[2],box[3])])
        else:canvas.shape(box,'ellipse' if kind=='data_flow' and node.get('data_shape')=='process' else node.get('shape','process'))
        if kind=='class_diagram':
            y=box[1];canvas.text((box[0]+5,y+3,box[2]-5,y+55),node['label'],20);y+=58
            for group,h in zip(node['groups'],node['heights']):
                canvas.line([(box[0],y),(box[2],y)]);canvas.text((box[0]+5,y+3,box[2]-5,y+h-3),'\n'.join(group),22,align='left');y+=h
        else:
            label=node['label']+('\n【'+locations[node['location']]+'】' if node.get('location') else '')
            canvas.text((box[0]+10,box[1]+4,box[2]-10,box[3]-4),label,19)
    occupied=[]
    for edge,points in routes:
        if edge.get('label'):
            box=_place_label(edge['label'],points,obstacles,occupied,width,height)
            canvas.text(box,edge['label'],17,background=True)
    return canvas.result()
