"""Keep layout line breaks distinct from the end of a complete rule."""
import re
from functools import lru_cache
from . import db

ITEM=re.compile(r'^\s*(?:\d{1,3}\s*(?:[.．](?!\d)|[、)）·]|[，,]\s*(?=[\u3400-\u9fff]{1,12}[:：]))|[（(][\d一二三四五六七八九十]+[)）]|[一二三四五六七八九十]+[、．]|第[\d一二三四五六七八九十]+条)')
MARKER=re.compile(r'^\s*\[(?:本地OCR|备注|补充原生|嵌入DOCX)')
OPEN={'“':'”','「':'」','『':'』','（':'）','(':')','[':']','【':'】','{':'}'}
ENDS='。！？!?；;'
WORDS=('同时','标题','其他','详细','引用','英文','中文','参考文献','研究方法','内容','正文','论文','摘要','关键词','要求','包括','格式','规范','重要','方法','数据','使用','对象','根据','至少','不超过','不少于','不得','应该','必须','其中','因此','以及','目的','研究','工作','理论','分析','结果','语言','文献','收敛','随意','张冠李戴')


def _trim(text,start,end):
    while start<end and text[start].isspace():start+=1
    while end>start and text[end-1].isspace():end-=1
    return start,end


def _balanced(text):
    stack=[];ascii_quote=False
    for char in text:
        if char=='"':ascii_quote=not ascii_quote;continue
        if ascii_quote:continue
        if stack and char==stack[-1]:stack.pop()
        elif char in OPEN:stack.append(OPEN[char])
    return not stack and not ascii_quote


def split_word(previous,following):
    return any(previous.endswith(word[:i]) and following.startswith(word[i:]) for word in WORDS for i in range(1,len(word)))


def continues(previous,following):
    previous=previous.strip();following=following.strip()
    if not previous or not following or ITEM.match(following) or MARKER.match(following):return False
    if not _balanced(previous):return True
    if previous[-1] in '，,、':return True
    return split_word(previous,following)


def cut_before(text,start):
    left=text[:start];right=text[start:]
    if not left.strip():return False
    stripped=left.rstrip()
    if '\n' in left[len(stripped):]:return not _balanced(stripped) or split_word(stripped.splitlines()[-1],right.splitlines()[0])
    if stripped[-1] in '更并且' and re.match(r'不得|禁止|必须|需要|应当|至少|不少于',right):return False
    return bool(left and not left[-1].isspace() and left[-1] not in ENDS+'，,、：:）)】]”"')


def cut_after(text,end):
    left=text[:end];right=text[end:]
    if not right.strip():return False
    suffix=right.lstrip()
    if '\n' in right[:len(right)-len(suffix)]:return continues(left.splitlines()[-1],suffix.splitlines()[0])
    if suffix[0] in ENDS:return False
    return bool(left.rstrip() and left.rstrip()[-1] not in ENDS+'，,、：:')


@lru_cache(maxsize=256)
def units(text,flowing=False):
    """Return literal spans; numbered requirements retain all their continuation lines."""
    blocks=[];start=None;end=0;numbered=False;offset=0
    for line in text.splitlines(keepends=True):
        content=line.rstrip('\r\n');next_end=offset+len(content)
        if not content.strip() or MARKER.match(content):
            if start is not None:blocks.append((start,end,numbered))
            start=None;numbered=False
        elif ITEM.match(content):
            if start is not None:blocks.append((start,end,numbered))
            start=offset;end=next_end;numbered=True
        else:
            if start is None:start=offset
            end=next_end
        offset+=len(line)
    if start is not None:blocks.append((start,end,numbered))
    result=[]
    for start,end,numbered in blocks:
        if numbered:
            a,b=_trim(text,start,end)
            if a<b:result.append((a,b,text[a:b]))
            continue
        stack=[];quoted=False;at=start
        for pos in range(start,end):
            char=text[pos]
            if char=='"':quoted=not quoted
            elif not quoted:
                if stack and char==stack[-1]:stack.pop()
                elif char in OPEN:stack.append(OPEN[char])
            if char in ENDS and not stack and not quoted:
                a,b=_trim(text,at,pos+1)
                if a<b:result.append((a,b,text[a:b]))
                at=pos+1
            elif char=='\n' and not flowing and not stack and not quoted:
                previous=text[at:pos].splitlines()[-1] if text[at:pos].strip() else ''
                following=text[pos+1:end].splitlines()[0] if text[pos+1:end].strip() else ''
                if not continues(previous,following):
                    a,b=_trim(text,at,pos)
                    if a<b:result.append((a,b,text[a:b]))
                    at=pos+1
        a,b=_trim(text,at,end)
        if a<b:result.append((a,b,text[a:b]))
    return tuple(result)


def folded(text):return ''.join(c for c in text if not c.isspace() and c not in '\u200b\ufeff')


def spans(text,statement):
    indexes=[i for i,c in enumerate(text) if not c.isspace() and c not in '\u200b\ufeff']
    needle=folded(statement)
    if not needle:return []
    normalized=''.join(text[i] for i in indexes);result=[];at=0
    while (at:=normalized.find(needle,at))>=0:
        result.append((indexes[at],indexes[at+len(needle)-1]+1));at+=len(needle)
    return result


def assess(statement,source_text='',page_text='',flowing=False):
    """Find provable omissions, not semantic similarity or speculative completions."""
    matches=[]
    for origin,text in (('source',source_text),('page',page_text)):
        if not text:continue
        for start,end in spans(text,statement):
            unit=next(((a,b,value) for a,b,value in units(text,flowing) if a<=start and end<=b),None)
            if unit:matches.append((origin,start,end,*unit))
    if not matches:
        return {'status':'not_literal','needs_attention':not _balanced(statement),'issue':'条款引号或括号不完整，需保留完整原文依据' if not _balanced(statement) else None,'context':source_text,'replacement':None}
    candidates=[]
    for origin,start,end,a,b,value in matches:
        cut_start=cut_before(value,start-a)
        cut_end=cut_after(value,end-a) or bool(value[end-a:].strip() and not _balanced(statement))
        dangling=bool(re.search(r'[，,、：:]\s*$',statement)) or not _balanced(statement)
        issue='条款在换行或引号、括号内部被拆开，遗漏了同一要求的后续内容' if cut_end or cut_start else '原文自身结束于未闭合的引号、括号或续接标点' if dangling else None
        replacement=value if (cut_start or cut_end) and _balanced(value) and value.rstrip().endswith(tuple(ENDS+'”"）)')) and folded(value)!=folded(statement) else None
        candidates.append({'status':'fragment' if issue else 'complete','needs_attention':bool(issue),'issue':issue,'context':value,'replacement':replacement,'origin':origin,'context_start':a,'context_end':b})
    repairs={folded(c['replacement']):c for c in candidates if c['replacement']}
    source_repairs=[c for c in candidates if c['origin']=='source' and c['replacement']]
    if len({folded(c['replacement']) for c in source_repairs})==1:return source_repairs[0]
    if len(repairs)==1:return next(iter(repairs.values()))
    complete=[c for c in candidates if not c['needs_attention']]
    if complete and not repairs:return complete[0]
    result=candidates[0]
    if len(repairs)>1:result={**result,'status':'fragment','needs_attention':True,'replacement':None,'issue':'相同片段对应不同上下文，未猜测补全内容'}
    return result


def source_for_rule(rule):
    if not rule.get('source_id'):return None
    return db.one('SELECT s.id,s.file_id,s.page_id,s.text,s.normalized,s.kind,s.method,s.verified,p.text AS page_text,f.sha256 AS file_sha256,f.name AS source_file,p.page_number FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.id=?',(rule['source_id'],))


def inspect_rule(rule,source=None):
    source=source or source_for_rule(rule)
    if not source:return {'status':'manual','needs_attention':False,'issue':None,'context':'','replacement':None}
    flowing=source.get('method')=='windows_ocr' or str(source.get('source_file','')).lower().endswith('.pdf')
    return assess(rule['statement'],source['text'],source.get('page_text') or '',flowing)


def summary(info):
    return {k:info.get(k) for k in ('status','needs_attention','issue')}|{'replacement_available':bool(info.get('replacement'))}
