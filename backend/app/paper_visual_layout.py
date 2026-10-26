"""Measured diagram layout with distinct attachment points and inspectable geometry."""
import math
from .paper_visual_render import Canvas,_font,_lines,_route,_intersects


def text_size(text,size,width):
    lines=_lines(text,_font(size),width)
    return min(width,max((_font(size).getlength(line) for line in lines),default=0)),len(lines)*size*1.45


def _label_size(text):
    width,height=text_size(text,17,230)
    return max(48,width+22),max(36,height+14)


def _flow_nodes(spec):
    nodes=spec['nodes'];edges=spec['edges'];by_id={n['id']:n for n in nodes}
    outgoing={n['id']:[] for n in nodes};incoming={n['id']:[] for n in nodes}
    for i,edge in enumerate(edges):outgoing[edge['source']].append((i,edge['target']));incoming[edge['target']].append(edge['source'])
    colors={};back=set()
    def visit(key):
        colors[key]=1
        for index,target in outgoing[key]:
            if colors.get(target)==1:back.add(index)
            elif not colors.get(target):visit(target)
        colors[key]=2
    roots=sorted(by_id,key=lambda key:(by_id[key].get('shape')!='start',bool(incoming[key]),key))
    for key in roots:
        if not colors.get(key):visit(key)
    rank={key:0 for key in by_id};degree={key:0 for key in by_id}
    for i,e in enumerate(edges):
        if i not in back:degree[e['target']]+=1
    queue=[key for key in roots if not degree[key]]
    for key in queue:
        for i,target in outgoing[key]:
            if i in back:continue
            rank[target]=max(rank[target],rank[key]+1);degree[target]-=1
            if degree[target]==0:queue.append(target)
    levels=[[n for n in nodes if rank[n['id']]==r] for r in range(max(rank.values())+1)]
    dimensions={}
    for n in nodes:
        decision=n.get('shape')=='decision'
        location={'user':'用户侧','on_chain':'链上','off_chain':'链下','external':'外部接口'}.get(n.get('location'))
        _,text_height=text_size(n['label']+('\n【'+location+'】' if location else ''),20,205 if decision else 214)
        dimensions[n['id']]=(300 if decision else 250,max(112 if decision else 84,text_height*(2 if decision else 1)+32))
    gap=max([100]+[_label_size(e.get('label',''))[1]+60 for e in edges if e.get('label')])
    row_heights=[max(dimensions[n['id']][1] for n in level) for level in levels]
    width=max(800,max(sum(dimensions[n['id']][0] for n in level)+125*(len(level)-1) for level in levels)+200)
    height=max(540,135+sum(row_heights)+gap*(len(levels)-1)+70)
    placed=[]
    y=115
    for row,level in enumerate(levels):
        total=sum(dimensions[n['id']][0] for n in level)+125*(len(level)-1)
        x=(width-total)/2
        for n in level:
            w,h=dimensions[n['id']]
            placed.append({**n,'shape':n.get('shape','process'),'box':(x,y+(row_heights[row]-h)/2,x+w,y+(row_heights[row]+h)/2),'rank':row})
            x+=w+125
        y+=row_heights[row]+gap
    return width,height,placed


def _case_nodes(spec):
    actors=[n for n in spec['nodes'] if n.get('shape')=='actor'];cases=[n for n in spec['nodes'] if n.get('shape')!='actor']
    actor_ids={a['id'] for a in actors}
    direct={e['target'] if e['source'] in actor_ids else e['source'] for e in spec['edges'] if e.get('relation','association')=='association'}
    primary=[n for n in cases if n['id'] in direct];secondary=[n for n in cases if n['id'] not in direct]
    slots={}
    if primary and secondary and max(len(primary),len(secondary))<=6:
        rows=max(len(primary),len(secondary));columns=2
        for i,n in enumerate(primary):slots[n['id']]=(0,i)
        used=set()
        for n in secondary:
            related=[slots[e['source']][1] for e in spec['edges'] if e['target']==n['id'] and e['source'] in slots]
            related += [slots[e['target']][1] for e in spec['edges'] if e['source']==n['id'] and e['target'] in slots]
            desired=related[0] if related else len(used)
            row=min((r for r in range(rows) if r not in used),key=lambda r:(abs(r-desired),r))
            used.add(row);slots[n['id']]=(1,row)
    else:
        columns=min(3,max(1,math.ceil(len(cases)/5)));rows=math.ceil(len(cases)/columns)
        for i,n in enumerate(primary+secondary):slots[n['id']]=(i//rows,i%rows)
    sizes={n['id']:(250,max(100,text_size(n['label'],20,206)[1]*1.55+24)) for n in cases}
    row_heights=[max((sizes[n['id']][1] for n in cases if slots[n['id']][1]==r),default=100) for r in range(rows)]
    width=300+columns*410
    height=max(630,150+sum(row_heights)+100*(rows-1)+80,len(actors)*195+170)
    placed=[]
    extra=(height-(150+sum(row_heights)+100*(rows-1)+80))/max(1,rows)
    for n in cases:
        col,row=slots[n['id']];w,h=sizes[n['id']]
        x=290+col*410;y=135+sum(row_heights[:row])+row*(100+extra)+(row_heights[row]-h)/2
        placed.append({**n,'shape':'use_case','box':(x,y,x+w,y+h)})
    for i,n in enumerate(actors):
        y=135+(i+.5)*(height-300)/len(actors)
        placed.append({**n,'shape':'actor','box':(35,y-70,185,y+100)})
    return width,height,placed


def _er_nodes(spec):
    entities=spec['entities'];columns=min(2,len(entities));details={}
    for n in entities:
        head=max(54,text_size(n['label'],21,300)[1]+22)
        fields=[];top=head
        for field in n['fields']:
            label=' '.join(x for x in (field.get('key',''),field['name'],field.get('data_type','')) if x)
            height=max(48,text_size(label,22,304)[1]+18)
            fields.append({'text':label,'top':top,'height':height});top+=height
        details[n['id']]={'height':top,'header':head,'field_layout':fields}
    row_heights=[max(details[n['id']]['height'] for n in entities[i:i+columns]) for i in range(0,len(entities),columns)]
    gap=max([150]+[_label_size(e.get('label',''))[1]+65 for e in spec['edges'] if e.get('label')])
    width=max(720,170+columns*500);height=max(540,170+sum(row_heights)+gap*(len(row_heights)-1)+70)
    placed=[]
    for i,n in enumerate(entities):
        col,row=i%columns,i//columns;x=80+col*500;y=145+sum(row_heights[:row])+row*gap
        placed.append({**n,**details[n['id']],'shape':'entity','box':(x,y,x+330,y+details[n['id']]['height']),'rank':row})
    return width,height,placed


def _ports(kind,a,b):
    ax,ay=(a['box'][0]+a['box'][2])/2,(a['box'][1]+a['box'][3])/2
    bx,by=(b['box'][0]+b['box'][2])/2,(b['box'][1]+b['box'][3])/2
    if a['id']==b['id']:return 'right','top'
    if a['shape']=='actor':return 'right','left'
    if b['shape']=='actor':return 'left','right'
    if kind=='flowchart':
        if a['rank']>=b['rank']:return 'right','right'
        if a['shape']=='decision' and abs(ax-bx)>100:return ('left' if bx<ax else 'right'),'top'
        return 'bottom','top'
    if kind=='er' and a['rank']!=b['rank']:return ('bottom','top') if ay<by else ('top','bottom')
    if abs(ax-bx)>100:return ('right','left') if ax<bx else ('left','right')
    return ('bottom','top') if ay<by else ('top','bottom')


def _hits_segment(bounds,a,b):
    if a[0]==b[0]:return bounds[0]<a[0]<bounds[2] and min(a[1],b[1])<bounds[3] and max(a[1],b[1])>bounds[1]
    return bounds[1]<a[1]<bounds[3] and min(a[0],b[0])<bounds[2] and max(a[0],b[0])>bounds[0]


def _place_label(text,points,nodes,occupied,width,height,other_paths=(),size=17,compact=False):
    if not text:return None
    w,h=_label_size(text) if not compact else (max(38,_font(size).getlength(text)+18),size*1.45+10)
    for a,b in sorted(zip(points,points[1:]),key=lambda pair:-math.dist(*pair)):
        for fraction in (.5,.3,.7,.15,.85):
            x=a[0]+(b[0]-a[0])*fraction;y=a[1]+(b[1]-a[1])*fraction
            options=[(x-w/2,y-h-7,x+w/2,y-7),(x-w/2,y+7,x+w/2,y+h+7)] if a[1]==b[1] else [(x+9,y-h/2,x+9+w,y+h/2),(x-w-9,y-h/2,x-9,y+h/2)]
            for box in options:
                if box[0]<12 or box[1]<85 or box[2]>width-12 or box[3]>height-12:continue
                if any(_intersects(box,bounds) for bounds in [*nodes,*occupied]):continue
                if any(_hits_segment(box,p,q) for path in other_paths for p,q in zip(path,path[1:])):continue
                occupied.append(box);return box
    raise ValueError('关系文字过于密集，当前图中无法清晰排布，请减少同一区域的关系或拆图')


def _card_label(text,point,side,nodes,occupied,width,height):
    w=max(38,_font(16).getlength(text)+18);h=34;x,y=point
    if side in ('left','right'):
        left=x+10 if side=='right' else x-10-w
        options=[(left,y-h-7,left+w,y-7),(left,y+7,left+w,y+h+7)]
    else:
        top=y+10 if side=='bottom' else y-h-10
        options=[(x+9,top,x+9+w,top+h),(x-w-9,top,x-9,top+h)]
    for box in options:
        if box[0]>=10 and box[1]>=85 and box[2]<=width-10 and box[3]<=height-10 and not any(_intersects(box,b) for b in [*nodes,*occupied]):
            occupied.append(box);return box
    raise ValueError('关系两端的基数文字过于密集，请拆分关系')


def layout_diagram(spec):
    width,height,nodes={'flowchart':_flow_nodes,'use_case':_case_nodes,'er':_er_nodes}[spec['kind']](spec)
    by_id={n['id']:n for n in nodes};boxes=[n['box'] for n in nodes]
    edges=[];groups={}
    for index,edge in enumerate(spec['edges']):
        source,target=by_id[edge['source']],by_id[edge['target']]
        ports=_ports(spec['kind'],source,target)
        item={**edge,'ports':ports,'index':index};edges.append(item)
        for key,side in ((edge['source'],ports[0]),(edge['target'],ports[1])):groups.setdefault((key,side),[]).append(index)
    paths=[]
    for edge in edges:
        a,b=by_id[edge['source']],by_id[edge['target']];offsets=[]
        for node,side in ((a,edge['ports'][0]),(b,edge['ports'][1])):
            group=groups[(node['id'],side)];available=((node['box'][3]-node['box'][1]) if side in ('left','right') else (node['box'][2]-node['box'][0]))-48
            spacing=min(30,available/max(1,len(group)-1))
            offsets.append(0 if node['shape']=='actor' else (group.index(edge['index'])-(len(group)-1)/2)*spacing)
        edge['points']=_route(a['box'],b['box'],boxes,width,height,edge['ports'],tuple(offsets),(a['shape'],b['shape']),paths)
        paths.append(edge['points'])
    occupied=[]
    for edge in edges:
        points=edge['points'];edge['cardinalities']=[]
        if spec['kind']=='er':
            for start,side,text in ((points[0],edge['ports'][0],edge.get('source_cardinality','1')),(points[-1],edge['ports'][1],edge.get('target_cardinality','N'))):
                box=_card_label(text,start,side,boxes,occupied,width,height)
                edge['cardinalities'].append({'text':text,'box':box})
    for edge in edges:
        relation=edge.get('relation','association')
        text='<<'+relation+'>>' if spec['kind']=='use_case' and relation!='association' else edge.get('label','')
        edge['label_text']=text
        edge['label_box']=_place_label(text,edge['points'],boxes,occupied,width,height,
                                      [p for i,p in enumerate(paths) if i!=edge['index']])
    return {'kind':spec['kind'],'title':spec['title'],'width':width,'height':height,'nodes':nodes,'edges':edges}


def draw_diagram(scene):
    canvas=Canvas(scene['width'],scene['height'])
    if scene['kind']=='use_case':canvas.shape((220,26,scene['width']-26,scene['height']-25))
    canvas.text((240 if scene['kind']=='use_case' else 45,30,scene['width']-45,84),scene['title'],24)
    for edge in scene['edges']:
        relation=edge.get('relation','association')
        canvas.arrow(edge['points'],dash=scene['kind']=='use_case' and relation!='association',
                     arrow=scene['kind']=='flowchart' or scene['kind']=='use_case' and relation!='association')
    for node in scene['nodes']:
        box=node['box'];x0,y0,x1,y1=box
        if node['shape']=='actor':
            x,y=110,(y0+y1)/2-15
            canvas.draw.ellipse(canvas.coords((x-12,y-58,x+12,y-34)),outline='#222222',width=round(1.7*canvas.scale))
            canvas.line([(x,y-34),(x,y+25)]);canvas.line([(x-27,y-8),(x+27,y-8)])
            canvas.line([(x-25,y+58),(x,y+25),(x+25,y+58)])
            canvas.text((15,y+62,205,y+111),node['label'],18)
        elif node['shape']=='entity':
            canvas.shape(box);canvas.line([(x0,y0+node['header']),(x1,y0+node['header'])])
            canvas.text((x0+8,y0+5,x1-8,y0+node['header']-4),node['label'],21)
            for field in node['field_layout']:
                canvas.text((x0+5,y0+field['top']+3,x1-5,y0+field['top']+field['height']-3),field['text'],22,align='left')
        else:
            canvas.shape(box,node['shape'])
            if node['shape']=='decision':text_box=(x0+40,y0+(y1-y0)*.25,x1-40,y1-(y1-y0)*.25)
            elif node['shape']=='use_case':text_box=(x0+20,y0+(y1-y0)*.16,x1-20,y1-(y1-y0)*.16)
            else:text_box=(x0+10,y0+5,x1-10,y1-5)
            location={'user':'用户侧','on_chain':'链上','off_chain':'链下','external':'外部接口'}.get(node.get('location'))
            canvas.text(text_box,node['label']+('\n【'+location+'】' if location else ''),20)
    for edge in scene['edges']:
        for label in edge['cardinalities']:canvas.text(label['box'],label['text'],16,background=True)
        if edge['label_box']:canvas.text(edge['label_box'],edge['label_text'],17,background=True)
    return canvas.result()
