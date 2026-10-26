"""Local, deterministic thesis diagrams. Input is data, never executable code."""
import io
import math
import heapq
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


@lru_cache(maxsize=96)
def _font(size):
    for name in ('C:/Windows/Fonts/simsun.ttc', 'C:/Windows/Fonts/msyh.ttc',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    raise ValueError('未找到绘图字体，请安装宋体或微软雅黑')


def _lines(text, font, width):
    result = []
    for paragraph in str(text).split('\n'):
        line = ''
        for char in paragraph:
            if line and font.getlength(line + char) > width:
                result.append(line)
                line = ''
            line += char
        result.append(line)
    return result


class Canvas:
    def __init__(self, width, height):
        # Both sides are at least 1080 px; extra supersampling keeps fine lines crisp.
        self.scale = max(2, 1080 / min(width, height))
        dimensions = (math.ceil(width * self.scale), math.ceil(height * self.scale))
        if dimensions[0] * dimensions[1] > 40_000_000:
            raise ValueError('图过大，请拆分为多张图')
        self.image = Image.new('RGB', dimensions, 'white')
        self.draw = ImageDraw.Draw(self.image)

    def coords(self, box):
        return tuple(round(v * self.scale) for v in box)

    def line(self, points, dash=False, width=1.5):
        points = [(x * self.scale, y * self.scale) for x, y in points]
        if not dash:
            self.draw.line(points, fill='#222222', width=max(1, round(width * self.scale)), joint='curve')
            return
        for a, b in zip(points, points[1:]):
            length = math.dist(a, b)
            if not length:
                continue
            for offset in range(0, math.ceil(length), round(12 * self.scale)):
                end = min(offset + 7 * self.scale, length)
                self.draw.line([(a[0] + (b[0]-a[0])*offset/length, a[1] + (b[1]-a[1])*offset/length),
                                (a[0] + (b[0]-a[0])*end/length, a[1] + (b[1]-a[1])*end/length)],
                               fill='#222222', width=max(1, round(width * self.scale)))

    def arrow(self, points, dash=False, arrow=True):
        self.line(points, dash)
        if arrow:
            a, b = points[-2:]
            angle = math.atan2(b[1]-a[1], b[0]-a[0])
            self.line([(b[0]-11*math.cos(angle-.42), b[1]-11*math.sin(angle-.42)), b,
                       (b[0]-11*math.cos(angle+.42), b[1]-11*math.sin(angle+.42))])

    def text(self, box, text, size=19, align='center', background=False):
        x0, y0, x1, y1 = box
        for trial in range(size, 10, -1):
            font = _font(round(trial * self.scale))
            lines = _lines(text, font, (x1-x0-16)*self.scale)
            line_height = trial * 1.45 * self.scale
            if len(lines) * line_height <= (y1-y0-8)*self.scale:
                break
        else:
            raise ValueError('文字过长，当前图中无法清晰排布，请缩短标签或拆图')
        y = y0*self.scale + ((y1-y0)*self.scale-len(lines)*line_height)/2
        for line in lines:
            width = font.getlength(line)
            x = (x0+x1)*self.scale/2-width/2 if align == 'center' else (x0+9)*self.scale
            if background:
                self.draw.rectangle((x-4, y, x+width+4, y+line_height), fill='white')
            self.draw.text((x, y), line, font=font, fill='#111111', anchor='lt')
            y += line_height

    def shape(self, box, shape='process'):
        bounds = self.coords(box)
        width = max(1, round(1.6*self.scale))
        if shape in ('ellipse', 'start', 'end', 'use_case'):
            self.draw.ellipse(bounds, fill='white', outline='#222222', width=width)
        elif shape == 'decision':
            x0, y0, x1, y1 = bounds
            points = [((x0+x1)/2, y0), (x1, (y0+y1)/2), ((x0+x1)/2, y1), (x0, (y0+y1)/2)]
            self.draw.polygon(points, fill='white')
            self.draw.line(points+[points[0]], fill='#222222', width=width, joint='curve')
        else:
            self.draw.rectangle(bounds, fill='white', outline='#222222', width=width)

    def result(self):
        output = io.BytesIO()
        self.image.save(output, format='PNG', dpi=(300, 300))
        return output.getvalue(), self.image.size


def _intersects(a,b):
    return a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]


def _route(a,b,obstacles,width,height,ports=('bottom','top'),offsets=(0,0),shapes=('process','process'),used=()):
    """Route through empty row/column gutters, including long and cyclic links."""
    def portal(box,side,offset,shape):
        x,y=(box[0]+box[2])/2,(box[1]+box[3])/2
        if shape=='actor':y-=15
        radius=(box[3]-box[1])/2 if side in ('left','right') else (box[2]-box[0])/2
        offset=max(-radius+20,min(radius-20,offset))
        point={'left':(box[0],y+offset),'right':(box[2],y+offset),'top':(x+offset,box[1]),'bottom':(x+offset,box[3])}[side]
        dx,dy={'left':(-22,0),'right':(22,0),'top':(0,-22),'bottom':(0,22)}[side]
        escape=(point[0]+dx,point[1]+dy)
        if shape in ('use_case','ellipse','start','end','decision'):
            factor=(1-abs(offset)/radius) if shape=='decision' else math.sqrt(max(0,1-(offset/radius)**2))
            if side in ('left','right'):point=(x+(-1 if side=='left' else 1)*(box[2]-box[0])/2*factor,y+offset)
            else:point=(x+offset,y+(-1 if side=='top' else 1)*(box[3]-box[1])/2*factor)
        elif shape=='actor':point=(110,y)
        return point,escape
    start,escape=portal(a,ports[0],offsets[0],shapes[0]);end,arrival=portal(b,ports[1],offsets[1],shapes[1])
    blocked=[(box[0]-16,box[1]-16,box[2]+16,box[3]+16) for box in obstacles]
    xs=sorted({25,width-25,escape[0],arrival[0],*[v for box in obstacles for gap in (22,44,66) for v in (box[0]-gap,box[2]+gap)]})
    ys=sorted({85,height-25,escape[1],arrival[1],*[v for box in obstacles for gap in (22,44,66) for v in (box[1]-gap,box[3]+gap)]})
    xs=[x for x in xs if 15<=x<=width-15];ys=[y for y in ys if 75<=y<=height-15]
    @lru_cache(maxsize=None)
    def free(x,y):
        return not any(box[0]<x<box[2] and box[1]<y<box[3] for box in blocked)
    @lru_cache(maxsize=None)
    def segment(p,q):
        if p[0]==q[0]:
            lo,hi=sorted((p[1],q[1]))
            return not any(box[0]<p[0]<box[2] and lo<box[3] and hi>box[1] for box in blocked)
        lo,hi=sorted((p[0],q[0]))
        return not any(box[1]<p[1]<box[3] and lo<box[2] and hi>box[0] for box in blocked)
    used_vertical={};used_horizontal={}
    for path in used:
        for p,q in zip(path,path[1:]):
            if p[0]==q[0]:used_vertical.setdefault(p[0],[]).append(tuple(sorted((p[1],q[1]))))
            elif p[1]==q[1]:used_horizontal.setdefault(p[1],[]).append(tuple(sorted((p[0],q[0]))))
    def shared_cost(p,q):
        ranges=used_vertical.get(p[0],[]) if p[0]==q[0] else used_horizontal.get(p[1],[])
        lo,hi=sorted((p[1],q[1]) if p[0]==q[0] else (p[0],q[0]))
        return sum(max(0,min(hi,end)-max(lo,start))*8 for start,end in ranges)
    begin=(xs.index(escape[0]),ys.index(escape[1]),0)
    target=(xs.index(arrival[0]),ys.index(arrival[1]))
    pending=[(0,begin)];costs={begin:0};previous={}
    final=None
    while pending:
        cost,state=heapq.heappop(pending)
        if cost!=costs[state]:continue
        i,j,direction=state
        if (i,j)==target:final=state;break
        for ni,nj,nd in ((i-1,j,1),(i+1,j,1),(i,j-1,2),(i,j+1,2)):
            if not 0<=ni<len(xs) or not 0<=nj<len(ys):continue
            p,q=(xs[i],ys[j]),(xs[ni],ys[nj])
            if not free(*q) or not segment(p,q):continue
            next_state=(ni,nj,nd)
            score=cost+math.dist(p,q)+(25 if direction and nd!=direction else 0)+shared_cost(p,q)
            if score<costs.get(next_state,float('inf')):
                costs[next_state]=score;previous[next_state]=state;heapq.heappush(pending,(score,next_state))
    if final is None:raise ValueError('节点过于密集，连线无法避让；请拆图或减少节点')
    path=[]
    while final!=begin:
        path.append((xs[final[0]],ys[final[1]]));final=previous[final]
    path=[start,escape]+list(reversed(path))+[end]
    compact=[]
    for point in path:
        if compact and point==compact[-1]:continue
        while len(compact)>1 and ((compact[-2][0]==compact[-1][0]==point[0] and (compact[-1][1]-compact[-2][1])*(point[1]-compact[-1][1])>=0) or (compact[-2][1]==compact[-1][1]==point[1] and (compact[-1][0]-compact[-2][0])*(point[0]-compact[-1][0])>=0)):
            compact.pop()
        compact.append(point)
    return compact


def render_diagram(spec):
    from .additional_visuals import LABELS,render
    if spec['kind'] in LABELS:return render(spec)
    from .paper_visual_layout import layout_diagram,draw_diagram
    return draw_diagram(layout_diagram(spec))
