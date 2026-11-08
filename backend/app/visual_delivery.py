"""Physical figure specification and vector output from deterministic renderers."""
from contextvars import ContextVar
from contextlib import contextmanager
import io
import math
from xml.sax.saxutils import escape
from fastapi import HTTPException

ACTIVE=ContextVar('visual_vector_canvas',default=None)
DEFAULT={'width_mm':150.0,'minimum_text_pt':7.0,'target_dpi':300,'unit':'','x_label':'','y_label':'','legend':''}


def specification(value=None):
    value={} if value is None else value
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise HTTPException(422,'图表交付规格字段无效')
    result={**DEFAULT,**value}
    for key,lo,hi in (('width_mm',30,500),('minimum_text_pt',4,24),('target_dpi',72,1200)):
        x=result[key]
        if not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) or not lo<=x<=hi:raise HTTPException(422,'图表交付参数超范围：'+key)
    for key in ('unit','x_label','y_label','legend'):
        if not isinstance(result[key],str) or len(result[key])>500:raise HTTPException(422,'图表标签无效')
    return result


def quality(metadata,width_mm=None):
    spec=specification(metadata.get('delivery_spec'))
    def positive(value):return type(value) in (int,float) and math.isfinite(value) and value>0
    width=spec['width_mm'] if width_mm is None else width_mm
    if not positive(width):raise HTTPException(422,'图表实际排版宽度须为有限正数')
    pixels=metadata.get('width')
    dpi=pixels/(width/25.4) if positive(pixels) else None
    font=metadata.get('minimum_logical_font');logical=metadata.get('logical_width')
    pt=font*width*72/25.4/logical if positive(font) and positive(logical) else None
    if dpi is not None and not positive(dpi):dpi=None
    if pt is not None and not positive(pt):pt=None
    return {'placed_width_mm':width,'effective_dpi':round(dpi,2) if dpi else None,
            'resolution_status':'pass' if dpi and dpi>=spec['target_dpi'] else 'fail' if dpi else 'unknown',
            'minimum_text_pt':round(pt,2) if pt else None,
            'text_status':'pass' if pt and pt>=spec['minimum_text_pt'] else 'fail' if pt else 'unknown',
            'required':spec,'visual_verification':'not_run','semantic_verification':'not_assessed'}


class VectorDraw:
    def __init__(self,canvas):self.canvas=canvas
    def _box(self,bounds):return [float(x)/self.canvas.scale for x in bounds]
    def rectangle(self,bounds,fill=None,outline=None,width=1,**kw):
        x0,y0,x1,y1=self._box(bounds)
        self.canvas.elements.append(f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" fill="{fill or "none"}" stroke="{outline or "none"}" stroke-width="{width/self.canvas.scale}"/>')
    def ellipse(self,bounds,fill=None,outline=None,width=1,**kw):
        x0,y0,x1,y1=self._box(bounds)
        self.canvas.elements.append(f'<ellipse cx="{(x0+x1)/2}" cy="{(y0+y1)/2}" rx="{(x1-x0)/2}" ry="{(y1-y0)/2}" fill="{fill or "none"}" stroke="{outline or "none"}" stroke-width="{width/self.canvas.scale}"/>')
    def polygon(self,points,fill=None,outline=None,width=1,**kw):
        self.line(points,fill=fill,polygon=True)
        if outline:self.line([*points,points[0]],fill=outline,width=width)
    def line(self,points,fill='#222222',width=1,polygon=False,**kw):
        coords=' '.join(f'{x/self.canvas.scale},{y/self.canvas.scale}' for x,y in points)
        tag='polygon' if polygon else 'polyline'
        self.canvas.elements.append(f'<{tag} points="{coords}" fill="{fill if polygon else "none"}" stroke="{fill if not polygon else "none"}" stroke-width="{width/self.canvas.scale}" stroke-linejoin="round"/>')


class VectorCanvas:
    def __init__(self,width,height):
        self.width=width;self.height=height;self.scale=1;self.elements=[];self.draw=VectorDraw(self);self.minimum_font=1000
    def coords(self,box):return box
    def line(self,points,dash=False,width=1.6):
        coords=' '.join(f'{x},{y}' for x,y in points)
        self.elements.append(f'<polyline points="{coords}" fill="none" stroke="#222222" stroke-width="{width}"'+(' stroke-dasharray="7 5"' if dash else '')+'/>')
    def arrow(self,points,dash=False,arrow=True):
        self.line(points,dash)
        if arrow:
            a,b=points[-2:];angle=math.atan2(b[1]-a[1],b[0]-a[0])
            self.line([(b[0]-11*math.cos(angle-.42),b[1]-11*math.sin(angle-.42)),b,(b[0]-11*math.cos(angle+.42),b[1]-11*math.sin(angle+.42))])
    def shape(self,box,shape='process'):
        x0,y0,x1,y1=box
        if shape in ('ellipse','start','end','use_case'):self.draw.ellipse(box,fill='white',outline='#222222',width=1.6)
        elif shape=='decision':
            points=[((x0+x1)/2,y0),(x1,(y0+y1)/2),((x0+x1)/2,y1),(x0,(y0+y1)/2)]
            self.draw.polygon(points,fill='white');self.line(points+[points[0]])
        else:self.draw.rectangle(box,fill='white',outline='#222222',width=1.6)
    def text(self,box,text,size=19,align='center',background=False):
        from .paper_visual_render import _font,_lines
        x0,y0,x1,y1=box
        for trial in range(size,10,-1):
            font=_font(trial);lines=_lines(text,font,x1-x0-16);height=trial*1.45
            if len(lines)*height<=y1-y0-8:break
        else:raise ValueError('文字过长，当前图中无法清晰排布，请缩短标签或拆图')
        self.minimum_font=min(self.minimum_font,trial)
        y=y0+((y1-y0)-len(lines)*height)/2
        for line in lines:
            width=font.getlength(line);x=(x0+x1)/2-width/2 if align=='center' else x0+9
            if background:self.draw.rectangle((x-4,y,x+width+4,y+height),fill='white')
            # Explicit baseline preserves the measured logical font size.
            self.elements.append(f'<text x="{x}" y="{y+trial}" font-family="SimSun,Microsoft YaHei,sans-serif" font-size="{trial}" fill="#111111">{escape(line)}</text>')
            y+=height
    def result(self):
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="'+str(self.width)+'" height="'+str(self.height)+'" viewBox="0 0 '+str(self.width)+' '+str(self.height)+'"><rect width="100%" height="100%" fill="white"/>'+''.join(self.elements)+'</svg>').encode(),(self.width,self.height)


def render_vector(spec,format='svg',delivery=None):
    from .paper_visual_render import render_diagram
    token=ACTIVE.set(True)
    try:data,size=render_diagram(spec)
    finally:ACTIVE.reset(token)
    if format=='svg':return data
    if format!='pdf':raise ValueError('矢量格式无效')
    return svg_pdf(data,specification(delivery)['width_mm'])


def svg_pdf(data,width_mm):
    """Render only the deterministic SVG vocabulary produced above."""
    from lxml import etree
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.colors import toColor
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    root=etree.fromstring(data);_,_,width,height=map(float,root.get('viewBox').split())
    scale=width_mm*72/25.4/width;stream=io.BytesIO()
    canvas=Canvas(stream,pagesize=(width*scale,height*scale))
    if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    canvas.translate(0,height*scale);canvas.scale(scale,-scale)
    for node in root:
        tag=etree.QName(node).localname
        canvas.saveState()
        fill=node.get('fill','none');stroke=node.get('stroke','none')
        if fill!='none':canvas.setFillColor(toColor(fill))
        if stroke!='none':canvas.setStrokeColor(toColor(stroke))
        canvas.setLineWidth(float(node.get('stroke-width',1)))
        if node.get('stroke-dasharray'):canvas.setDash([float(x) for x in node.get('stroke-dasharray').split()])
        flags={'fill':int(fill!='none'),'stroke':int(stroke!='none')}
        if tag=='rect':
            w=width if node.get('width')=='100%' else float(node.get('width',0))
            h=height if node.get('height')=='100%' else float(node.get('height',0))
            canvas.rect(float(node.get('x',0)),float(node.get('y',0)),w,h,**flags)
        elif tag=='ellipse':
            cx,cy,rx,ry=(float(node.get(k)) for k in ('cx','cy','rx','ry'))
            canvas.ellipse(cx-rx,cy-ry,cx+rx,cy+ry,**flags)
        elif tag in ('polygon','polyline'):
            points=[tuple(map(float,p.split(','))) for p in node.get('points').split()]
            path=canvas.beginPath();path.moveTo(*points[0])
            for point in points[1:]:path.lineTo(*point)
            if tag=='polygon':path.close()
            canvas.drawPath(path,**flags)
        elif tag=='text':
            canvas.translate(float(node.get('x')),float(node.get('y')));canvas.scale(1,-1)
            canvas.setFont('STSong-Light',float(node.get('font-size',12)));canvas.drawString(0,0,node.text or '')
        else:raise ValueError('结构图包含不支持的矢量元素')
        canvas.restoreState()
    canvas.showPage();canvas.save()
    return stream.getvalue()


def vector_metadata(svg):
    import re
    from lxml import etree
    root=etree.fromstring(svg)
    sizes=[float(x.get('font-size')) for x in root.iter('{http://www.w3.org/2000/svg}text')]
    return {'logical_width':float(root.get('viewBox').split()[2]),'minimum_logical_font':min(sizes) if sizes else None,
            'font_family':'SimSun,Microsoft YaHei,sans-serif','vector_format':'svg'}
