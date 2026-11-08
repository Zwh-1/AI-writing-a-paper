"""Conservative static structures, with offsets into the original source.

No code/SQL is executed. Literal DDL is a definition, not a live database
inspection. Unresolved expressions and conflicting definitions stay explicit.
"""
import re

IDENT=r'(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$]*)'
QUALIFIED=IDENT+r'(?:\s*\.\s*'+IDENT+r')?'


def name(value):
    return '.'.join(part.strip().strip('`"[]') for part in value.split('.'))


def literals(text):
    """Read JS/TS string literals, retaining each decoded character's offset."""
    rows=[];i=0
    while i<len(text):
        if text.startswith('//',i):
            at=text.find('\n',i);i=at if at>=0 else len(text);continue
        if text.startswith('/*',i):
            at=text.find('*/',i+2);i=at+2 if at>=0 else len(text);continue
        if text[i] not in '\'"`':i+=1;continue
        quote=text[i];start=i;i+=1;value=[];positions=[];unresolved_escape=False
        while i<len(text) and text[i]!=quote:
            offset=i;char=text[i]
            if char=='\\' and i+1<len(text):
                i+=1
                if text[i] in '\r\n':
                    if text[i]=='\r' and i+1<len(text) and text[i+1]=='\n':i+=1
                    i+=1;continue
                if text[i] in 'ux':unresolved_escape=True
                char={'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','v':'\v','0':'\0'}.get(text[i],text[i])
            value.append(char);positions.append(offset);i+=1
        if i<len(text):
            rows.append({'text':''.join(value),'positions':positions,'start':start,'end':i+1,'unresolved_escape':unresolved_escape})
            i+=1
    combined=[]
    for row in rows:
        if combined and re.fullmatch(r'\s*\+\s*',text[combined[-1]['end']:row['start']]):
            combined[-1]['text']+=row['text'];combined[-1]['positions']+=row['positions'];combined[-1]['end']=row['end']
            combined[-1]['unresolved_escape']=combined[-1]['unresolved_escape'] or row['unresolved_escape']
        else:combined.append(dict(row))
    return combined


def balanced(text,start,opening='(',closing=')'):
    depth=0;quote=None;i=start
    while i<len(text):
        char=text[i]
        if quote:
            if char=='\\':i+=2;continue
            if char==quote:
                if i+1<len(text) and text[i+1]==quote:i+=2;continue
                quote=None
        elif char in '\'"`':quote=char
        elif char==opening:depth+=1
        elif char==closing:
            depth-=1
            if not depth:return i
        i+=1
    return None


def code_mask(text):
    """Hide string/comment contents without moving source offsets."""
    value=list(text)
    for row in literals(text):value[row['start']:row['end']]=['\n' if ch=='\n' else ' ' for ch in text[row['start']:row['end']]]
    return re.sub(r'//[^\n]*|/\*[\s\S]*?\*/',lambda m:''.join('\n' if char=='\n' else ' ' for char in m.group()),''.join(value))


def _parts(text):
    result=[];start=0;depth=0;quote=None;i=0
    while i<len(text):
        char=text[i]
        if quote:
            if char=='\\':i+=2;continue
            if char==quote:
                if i+1<len(text) and text[i+1]==quote:i+=2;continue
                quote=None
        elif char in '\'"`':quote=char
        elif char=='(':depth+=1
        elif char==')':depth-=1
        elif char==',' and depth==0:result.append(text[start:i].strip());start=i+1
        i+=1
    result.append(text[start:].strip())
    return [part for part in result if part]


def _columns(value):
    return [name(part) for part in _parts(value)]


def _outside_strings(text):
    return re.sub(r"'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"",lambda m:' '*len(m.group()),text)


def sql_tables(text,path):
    if path.lower().endswith('.sql'):
        # Comments retain their physical length, so source offsets do not move.
        clean=list(text);i=0;quote=None
        while i<len(text):
            if quote:
                if text[i]=='\\':i+=2;continue
                if text[i]==quote:
                    if i+1<len(text) and text[i+1]==quote:i+=2;continue
                    quote=None
                i+=1;continue
            if text[i] in '\'"`':quote=text[i];i+=1;continue
            end=None
            if text.startswith('--',i):
                end=text.find('\n',i);end=len(text) if end<0 else end
            elif text.startswith('/*',i):
                end=text.find('*/',i+2);end=len(text) if end<0 else end+2
            if end is not None:
                clean[i:end]=['\n' if ch=='\n' else ' ' for ch in text[i:end]];i=end
            else:i+=1
        clean=''.join(clean)
        sources=[{'text':clean,'positions':list(range(len(text)))}]
    else:sources=literals(text)
    result=[]
    for source in sources:
        if source.get('unresolved_escape'):continue
        sql=source['text']
        for match in re.finditer(r'\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?('+QUALIFIED+r')\s*\(',sql,re.I):
            end=balanced(sql,match.end()-1)
            if end is None:continue
            body=sql[match.end():end];fields=[];primary=[];foreign=[];unique=[];unparsed=['动态插值'] if '${' in body else []
            for part in _parts(body):
                constraint=r'^(?:CONSTRAINT\s+'+IDENT+r'\s+)?'
                pk=re.search(constraint+r'PRIMARY\s+KEY\s*\(([^)]+)\)',part,re.I)
                if pk:primary.extend(_columns(pk[1]));continue
                fk=re.search(constraint+r'FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+('+QUALIFIED+r')\s*\(([^)]+)\)',part,re.I)
                if fk:
                    foreign.append({'columns':_columns(fk[1]),'target_table':name(fk[2]),'target_columns':_columns(fk[3])});continue
                uq=re.search(constraint+r'UNIQUE(?:\s+(?:KEY|INDEX))?(?:\s+'+IDENT+r')?\s*\(([^)]+)\)',part,re.I)
                if uq:unique.append(_columns(uq[1]));continue
                if re.match(r'(?:KEY|INDEX|FULLTEXT)\b',part,re.I):continue
                if re.match(r'(?:CONSTRAINT|CHECK|EXCLUDE)\b',part,re.I):unparsed.append(part);continue
                field=re.match(r'('+IDENT+r')\s+([\s\S]+)',part)
                if not field:unparsed.append(part);continue
                rest=field[2];outside=_outside_strings(rest)
                cutoff=re.search(r'\s+(?:NOT\s+NULL|NULL|PRIMARY\s+KEY|UNIQUE|REFERENCES|DEFAULT|COMMENT|CHECK|GENERATED|COLLATE|CONSTRAINT|AUTO_INCREMENT)\b',outside,re.I)
                data_type=rest[:cutoff.start()].strip() if cutoff else rest.strip()
                # Types/identifiers containing interpolation are not invented.
                if '${' in data_type or '${' in field[1]:unparsed.append(part);continue
                constraints=[]
                declaration=re.split(r'\bCOMMENT\b',outside,flags=re.I)[0]
                if re.search(r'\bNOT\s+NULL\b',declaration,re.I):constraints.append('NOT NULL')
                if re.search(r'\bAUTO_INCREMENT\b',declaration,re.I):constraints.append('AUTO_INCREMENT')
                if re.search(r'\bPRIMARY\s+KEY\b',declaration,re.I):primary.append(name(field[1]))
                if re.search(r'\bUNIQUE\b',declaration,re.I):unique.append([name(field[1])])
                reference=re.search(r'\bREFERENCES\b',declaration,re.I)
                inline=re.match(r'REFERENCES\s+('+QUALIFIED+r')\s*\(([^)]+)\)',rest[reference.start():],re.I) if reference else None
                if inline:foreign.append({'columns':[name(field[1])],'target_table':name(inline[1]),'target_columns':_columns(inline[2])})
                comment_at=re.search(r'\bCOMMENT\b',outside,re.I)
                comment=re.match(r"'((?:''|\\.|[^'\\])*)'",rest[comment_at.end():].lstrip()) if comment_at else None
                default_at=re.search(r'\bDEFAULT\b',declaration,re.I)
                default=''
                if default_at:
                    tail=rest[default_at.end():].lstrip()
                    literal=re.match(r"'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|[^\s,()]+(?:\([^)]*\))?",tail)
                    if literal:default=literal[0]
                    elif tail.startswith('('):
                        stop=balanced(tail,0)
                        if stop is not None:default=tail[:stop+1]
                fields.append({'name':name(field[1]),'data_type':data_type,'constraints':constraints,
                               'default':default,'comment':comment[1].replace("''", "'") if comment else ''})
            known={field['name'] for field in fields}
            for field in fields:
                if field['name'] in primary:field['constraints'].append('PK')
                if [field['name']] in unique:field['constraints'].append('UNIQUE')
                if any(field['name'] in row['columns'] for row in foreign):field['constraints'].append('FK')
            if not fields:continue
            start_cp=source['positions'][match.start()];end_cp=source['positions'][end]+1
            result.append({'kind':'database_table','name':name(match[1]),'fields':fields,
                'primary_key':list(dict.fromkeys(primary)),'unique_keys':unique,'foreign_keys':foreign,
                'complete_definition':not unparsed and set(primary)<=known and all(set(key)<=known for key in unique) and all(set(row['columns'])<=known and len(row['columns'])==len(row['target_columns']) for row in foreign),
                'limitations':['DDL声明不证明迁移已执行或当前部署库结构一致']+(['存在无法静态解析的字段或约束'] if unparsed else []),
                'source_start':start_cp,'source_end':end_cp})
    return result


def route_from_file(path):
    match=re.search(r'(?:^|/)(?:src/)?app/(.*?)/?page\.[jt]sx?$',path)
    if not match:return None
    parts=[part for part in match[1].split('/') if part and not part.startswith('(') and not part.startswith('@')]
    return '/'+('/'.join(parts))


def frontend_page(text,path):
    route=route_from_file(path)
    if route is None:return None
    imports=[]
    for match in re.finditer(r'\bimport\s+([\s\S]*?)\s+from\s*[\'"]([^\'"]+)[\'"]',text):
        clause=match[1].strip()
        if len(clause)<300:imports.append({'clause':clause,'module':match[2],'source_start':match.start(),'source_end':match.end()})
    labels=[m[1].strip() for m in re.finditer(r'<(?:h[1-3]|label|button|CardTitle)\b[^>]*>\s*([^<{}\n]{1,100})\s*</',text)]
    return {'kind':'frontend_page','name':route,'route':route,'path':path,'imports':imports,
            'labels':list(dict.fromkeys(labels)),
            'limitations':['路由按已读页面文件约定定位，不证明界面已运行；路由组名不自动证明权限']}


def api_routes(text,path):
    rows=[]
    for literal in literals(text):
        if literal.get('unresolved_escape'):continue
        prefix=text[max(0,literal['start']-100):literal['start']]
        match=re.search(r'\b([A-Za-z_$][\w$]*)\.(get|post|put|patch|delete|options|head)\s*\(\s*$',prefix,re.I)
        if match and literal['text'].startswith('/'):
            rows.append({'kind':'api_route','name':match[2].upper()+' '+literal['text'],
                         'method':match[2].upper(),'route':literal['text'],
                         'source_start':max(0,literal['start']-len(match[0])),'source_end':literal['end'],
                         'limitations':['仅为静态路由片段，尚未合并路由挂载前缀，也不证明请求执行成功']})
    return rows


def test_cases(text,path):
    rows=[]
    for literal in literals(text):
        if literal.get('unresolved_escape'):continue
        prefix=text[max(0,literal['start']-80):literal['start']]
        match=re.search(r'\b(?:test|it)(?:\.(only|skip|todo))?\s*\(\s*$',prefix)
        if not match:continue
        start=max(0,literal['start']-len(match[0]));opening=text.find('(',start,literal['start'])
        end=balanced(text,opening) if opening>=0 else None
        end=end+1 if end is not None else literal['end']
        body=code_mask(text[literal['end']:end])
        assertions=list(dict.fromkeys(re.findall(r'\.(to[A-Z]\w*|equal|deepEqual|strictEqual|throws|rejects)\s*\(',body)))
        rows.append({'kind':'test_case','name':literal['text'],'assertions':assertions,
                     'declared_mode':match[1] or 'normal',
                     'source_start':start,'source_end':end,
                     'execution_status':'definition_only_no_execution_judgment',
                     'limitations':['此条目只描述测试定义，运行结果须另据明确执行记录核对']})
    return rows
