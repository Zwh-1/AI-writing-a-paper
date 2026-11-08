"""Literal claim locators for navigation and cross-chapter review, never evidence."""
import re

# Match labels immediately preceding reported values. Values remain unverified.
PATTERN=re.compile(r'([A-Za-z][A-Za-z _-]{1,50}|[\u4e00-\u9fff]{2,30})\s*(?:[:：=]|为|是|达到|约为|为约|was|is)?\s*([+-]?\d+(?:\.\d+)?)\s*(%|％|ms|s|秒|人|个|次|MB|GB)?',re.I)


def labels(text):
    result=[]
    for match in PATTERN.finditer(text):
        label=re.sub(r'\s+',' ',match[1].strip()).casefold()
        label=re.sub(r'(?:达到|约为|为约|为|是|was|is)$','',label).strip()
        # Common sentence prefixes do not identify different metrics.
        label=re.sub(r'^(?:本研究|本文|测试中|实验中|结果显示|the |our )','',label).strip()
        if len(label)>1:result.append({'label':label,'value':match[2],'unit':match[3] or ''})
    return result


def catalogue(section,revision,blocks):
    return [{'section_id':section['id'],'revision_id':revision['id'],'paragraph_index':i,
             'node_id':node.get('attrs',{}).get('nodeId'),'mentions':mentions,'evidence':False}
            for i,node,text in blocks if (mentions:=labels(text))]
