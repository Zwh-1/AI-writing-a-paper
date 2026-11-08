"""Typed bibliography rendering. Formatting never asserts source authenticity."""
import re
from copy import deepcopy
from urllib.parse import urlparse

VERSIONS = ('legacy', 'gbt7714_2015', 'gbt7714_2025')
TYPES = {'article':'J','journal':'J','book':'M','thesis':'D','dissertation':'D',
         'conference':'C','proceedings':'C','standard':'S','webpage':'EB','web':'EB'}


def legacy(meta):
    if meta.get('citation_raw'):return re.sub(r'\s+', ' ', str(meta['citation_raw'])).strip()
    authors=meta.get('authors','')
    if isinstance(authors,list):authors='，'.join(str(a) for a in authors)
    return f"{authors or '【作者待补】'}. {meta.get('title') or '【题名待补】'}{(' ['+meta['type']+']') if meta.get('type') else ''}. {meta.get('journal') or meta.get('publisher') or ''}, {meta.get('year') or '【年份待补】'}{(', '+str(meta['volume'])) if meta.get('volume') else ''}{(':'+str(meta['pages'])) if meta.get('pages') else ''}.{(' DOI:'+meta['doi']) if meta.get('doi') else ''}"


def normalize(meta):
    if not isinstance(meta,dict):raise ValueError('书目元数据须为对象')
    original=deepcopy(meta)
    corrections=meta.get('corrections',{})
    if not isinstance(corrections,dict):raise ValueError('书目修订须为对象')
    data={**meta,**corrections}
    def text(key,*aliases):
        value=next((data[k] for k in (key,*aliases) if data.get(k) not in (None,'')), '')
        if not isinstance(value,(str,int,float)) or isinstance(value,bool):
            raise ValueError('书目字段类型无效：'+key)
        return re.sub(r'\s+',' ',str(value)).strip()
    keys=('title','journal','publisher','year','volume','issue','pages','edition','place','url',
          'access_date','publication_date','doi','container_title','editors','standard_number','citation_raw')
    aliases={'place':('publisher_place','location'),'issue':('number',),'url':('URL',),'access_date':('accessed','accessed_date'),
             'publication_date':('date','published'),'container_title':('booktitle',),'standard_number':('number',)}
    result={k:text(k,*aliases.get(k,())) for k in keys}
    kind=text('type','document_type') or ('J' if result['journal'] else '')
    kind=TYPES.get(kind.lower(),kind.upper()).strip('[]')
    result['base_type']=kind.split('/')[0]
    result['type']=kind
    authors=data.get('authors',data.get('author',''))
    if isinstance(authors,str):authors=[authors] if authors.strip() else []
    if not isinstance(authors,list) or len(authors)>200:raise ValueError('作者须为文字或列表')
    result['authors']=[]
    for author in authors:
        if isinstance(author,dict):author=author.get('name') or ' '.join(str(author.get(k,'')) for k in ('family','given'))
        if not isinstance(author,str):raise ValueError('作者姓名须为文字')
        if author.strip():result['authors'].append(re.sub(r'\s+',' ',author).strip())
    result['doi']=re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',result['doi'],flags=re.I)
    result['year']=result['year'] or result['publication_date'][:4]
    result['original']=original
    result['raw_reviewed']=data.get('citation_reviewed') is True
    return result


def validate(data):
    issues=[]
    def add(field,message,severity='error'):issues.append({'field':field,'message':message,'severity':severity})
    if data['citation_raw']:
        if not data['raw_reviewed']:add('citation_raw','保留人工格式原串，尚未确认格式核对','warning')
        return issues
    kind=data['base_type']
    if kind not in ('J','M','D','C','S','EB'):add('type','请选择已支持的文献类型')
    for field in ('title','year'):
        if not data[field]:add(field,'缺少'+{'title':'题名','year':'年份'}[field])
    if not data['authors']:add('authors','缺少责任者','warning' if kind=='EB' else 'error')
    if data['year'] and not re.fullmatch(r'\d{4}',data['year']):add('year','年份须为四位数字')
    required={'J':('journal',),'M':('publisher','place'),'D':('publisher','place'),
              'C':('container_title',) if data['container_title'] else ('publisher',),'S':('standard_number',),
              'EB':('url','access_date')}.get(kind,())
    for field in required:
        if not data[field]:add(field,'缺少本类型的著录字段：'+field)
    if kind=='J' and not data['pages']:add('pages','缺少页码或文章编号','warning')
    for key in ('access_date','publication_date'):
        if data[key]:
            try:
                from datetime import date
                date.fromisoformat(data[key])
            except ValueError:add(key,'日期须为有效的 YYYY-MM-DD')
    if data['url'] and urlparse(data['url']).scheme not in ('http','https'):add('url','访问路径须为 http 或 https')
    if data['doi'] and not re.fullmatch(r'10\.\d{4,9}/\S+',data['doi'],re.I):add('doi','DOI 格式待核对','warning')
    return issues


def render(meta,version='gbt7714_2025'):
    if version not in VERSIONS:raise ValueError('不支持的参考文献标准版本')
    data=normalize(meta);issues=validate(data)
    if version=='legacy':text=legacy({**meta,**meta.get('corrections',{})})
    elif data['citation_raw']:text=data['citation_raw']
    else:
        authors=data['authors'][:3]
        if len(data['authors'])>3:authors.append('等' if re.search(r'[\u4e00-\u9fff]',''.join(authors)) else 'et al.')
        author=', '.join(authors) or '【责任者待补】'
        kind=data['base_type'];electronic=('/OL' in data['type'] or kind=='EB')
        tag=data['type'] or '【类型待补】'
        if kind=='EB' and '/' not in tag:tag+='/OL'
        title=data['title'] or '【题名待补】'
        if kind=='S' and data['standard_number'] and data['standard_number'] not in title:title+=': '+data['standard_number']
        head=author+'. '+title+'['+tag+'].'
        year=data['year'] or '【年份待补】'
        if kind=='J':
            details=(data['journal'] or '【刊名待补】')+', '+year
            if data['volume']:details+=', '+data['volume']
            if data['issue']:details+='('+data['issue']+')'
            if data['pages']:details+=': '+data['pages']
        elif kind=='EB':details=data['publication_date'] or year
        else:
            location=': '.join(x for x in (data['place'],data['publisher']) if x)
            details=(location+', ' if location else '')+year
            if data['edition'] and kind=='M':head+=' '+data['edition']+'.'
            if kind=='C' and data['container_title']:
                head=head.rstrip('.')+'//'+((data['editors']+'. ') if data['editors'] else '')+data['container_title']+'.'
            if data['pages']:details+=': '+data['pages']
        if data['access_date'] and (electronic or data['url']):details+='['+data['access_date']+']'
        text=head+' '+details+'.'
        if data['url']:text+=' '+data['url'].rstrip('.')+'.'
        if data['doi']:text+=' DOI:'+data['doi']+'.'
    return {'text':text,'version':version,'normalized':data,'issues':issues,
            'format_status':'needs_correction' if any(x['severity']=='error' for x in issues) else 'needs_review' if issues else 'checked',
            'source_authenticity':'not_assessed'}
