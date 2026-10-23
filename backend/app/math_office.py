"""LaTeX -> MathML -> native Office Math. Unsupported structures fail explicitly."""
from lxml import etree as E
from .ooxml_preserve import M
N='{'+M+'}'
def node(name,**attrs):return E.Element(N+name,{N+k:str(v) for k,v in attrs.items()})
def wrap(name,children):
    out=node(name)
    for child in children:out.append(child)
    return out

def convert(latex):
    from latex2mathml.converter import convert as to_mathml
    if not isinstance(latex,str) or not 1<=len(latex)<=10000:raise ValueError('公式长度须为1至10000字符')
    tree=E.fromstring(to_mathml(latex).encode(),E.XMLParser(resolve_entities=False,no_network=True))
    def walk(element):
        kind=E.QName(element).localname;children=list(element)
        if kind in ('math','mrow','mstyle','mpadded','semantics'):
            result=[]
            for child in children:
                if E.QName(child).localname in ('annotation','annotation-xml'):continue
                parts=walk(child)
                if result and result[-1].tag==N+'nary' and len(result[-1].find(N+'e'))==0 and parts and not (parts[0].tag==N+'r' and ''.join(parts[0].itertext()) in ('+','-','=','≤','≥',',',';')):
                    for part in parts:result[-1].find(N+'e').append(part)
                else:result.extend(parts)
            return result
        if kind in ('mi','mn','mo','mtext','ms','mspace'):
            r=node('r');t=node('t');t.text=''.join(element.itertext()) if kind!='mspace' else '\u2009';t.set('{http://www.w3.org/XML/1998/namespace}space','preserve');r.append(t);return [r]
        if kind=='mfrac':return [wrap('f',[wrap('num',walk(children[0])),wrap('den',walk(children[1]))])]
        if kind in ('msup','msub','msubsup') and ''.join(children[0].itertext()) in ('∑','∏','∫','∮','⋃','⋂'):
            n=node('nary');pr=node('naryPr');pr.append(node('chr',val=''.join(children[0].itertext())));pr.append(node('limLoc',val='undOvr'));n.append(pr);n.append(wrap('sub',walk(children[1]) if kind!='msup' else []));n.append(wrap('sup',walk(children[-1]) if kind!='msub' else []));n.append(node('e'));return [n]
        if kind in ('msup','msub','msubsup'):
            names={'msup':['e','sup'],'msub':['e','sub'],'msubsup':['e','sub','sup']}[kind];return [wrap({'msup':'sSup','msub':'sSub','msubsup':'sSubSup'}[kind],[wrap(name,walk(child)) for name,child in zip(names,children)])]
        if kind in ('msqrt','mroot'):
            rad=node('rad')
            if kind=='msqrt':pr=node('radPr');pr.append(node('degHide',val='1'));rad.append(pr);rad.append(node('deg'));rad.append(wrap('e',[p for child in children for p in walk(child)]))
            else:rad.append(wrap('deg',walk(children[1])));rad.append(wrap('e',walk(children[0])))
            return [rad]
        if kind in ('munder','mover','munderover'):
            base=''.join(children[0].itertext())
            if base in ('∑','∏','∫','∮','⋃','⋂'):
                n=node('nary');pr=node('naryPr');pr.append(node('chr',val=base));pr.append(node('limLoc',val='undOvr'));n.append(pr);n.append(wrap('sub',walk(children[1]) if kind!='mover' else []));n.append(wrap('sup',walk(children[-1]) if kind!='munder' else []));n.append(node('e'));return [n]
            if kind=='mover':
                acc=node('acc');pr=node('accPr');pr.append(node('chr',val=''.join(children[1].itertext())));acc.append(pr);acc.append(wrap('e',walk(children[0])));return [acc]
            return [wrap('limLow' if kind=='munder' else 'sSubSup',[wrap('e',walk(children[0])),wrap('lim' if kind=='munder' else 'sub',walk(children[1]))]+([wrap('sup',walk(children[2]))] if kind=='munderover' else []))]
        if kind=='mtable':
            matrix=node('m')
            for row in children:
                mr=node('mr')
                for cell in row:mr.append(wrap('e',walk(cell)))
                matrix.append(mr)
            return [matrix]
        if kind in ('mtr','mtd'):return [part for child in children for part in walk(child)]
        if kind=='mfenced':
            delimiter=node('d');pr=node('dPr');pr.append(node('begChr',val=element.get('open','(')));pr.append(node('endChr',val=element.get('close',')')));delimiter.append(pr)
            for child in children:delimiter.append(wrap('e',walk(child)))
            return [delimiter]
        raise ValueError('公式结构暂不支持：'+kind+'；未降级为普通文本')
    root=wrap('oMath',walk(tree));return E.tostring(root,encoding='unicode')
