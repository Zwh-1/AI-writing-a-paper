"""Executable, scoped paper rules; all automatic inference remains a candidate."""
import re,hashlib,math
from datetime import datetime
from fastapi import HTTPException
from . import db,documents
DEFAULT_OPERATIONS=('generate','revise','polish','expand','compress','translate')
OPERATIONS=set(DEFAULT_OPERATIONS)
FIELDS={'paper_type','section_kind','operation','language'}
CHECKERS={'manual','word_min','word_max','word_range','reference_min','reference_recent','reference_foreign_ratio','required_sections','required_terms','forbidden_terms','heading_min','figure_min','table_min','format_value','rendered_page_min'}
FORMAT_KEYS={'font','latin_font','font_size','line_spacing','first_line_chars','margin_top','margin_bottom','margin_left','margin_right','heading_font','reference_superscript'}
def execution_key(checker,value):
    if checker=='manual':return ''
    if checker in ('word_min','word_max','word_range'):return 'word_count'
    if checker=='format_value':return 'format:'+value['key']
    return checker

def validate_condition(expr,depth=0):
    if not expr:return
    if not isinstance(expr,dict) or depth>8:raise HTTPException(422,'规则条件结构无效或嵌套过深')
    if 'all' in expr or 'any' in expr:
        key='all' if 'all' in expr else 'any'
        if not isinstance(expr[key],list) or not 1<=len(expr[key])<=30:raise HTTPException(422,'规则条件组无效')
        for part in expr[key]:validate_condition(part,depth+1)
    elif expr.get('field') not in FIELDS or expr.get('op') not in ('eq','ne','in','not_in'):raise HTTPException(422,'不支持的规则条件字段或比较方式')
    elif expr['op'] in ('in','not_in') and not isinstance(expr.get('value'),list):raise HTTPException(422,'集合条件需要数组')
def condition_matches(expr,context):
    if not expr:return True
    validate_condition(expr)
    if 'all' in expr:return all(condition_matches(x,context) for x in expr['all'])
    if 'any' in expr:return any(condition_matches(x,context) for x in expr['any'])
    actual=context.get(expr['field']);target=expr.get('value');op=expr['op']
    return actual==target if op=='eq' else actual!=target if op=='ne' else actual in target if op=='in' else actual not in target

def validate_execution(body):
    checker=body.get('checker','manual');value=body.get('value')
    if checker not in CHECKERS:raise HTTPException(422,'不支持的检查器')
    operations=body.get('operations',list(DEFAULT_OPERATIONS))
    if not isinstance(operations,list) or not operations or any(x not in OPERATIONS for x in operations):raise HTTPException(422,'写作操作范围无效')
    validate_condition(body.get('conditions',{}))
    if checker in ('word_min','word_max','reference_min','heading_min','figure_min','table_min','rendered_page_min'):
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<0:raise HTTPException(422,'此检查器需要非负数值')
    def finite_number(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    if checker=='word_range' and (not isinstance(value,dict) or not finite_number(value.get('min')) or not finite_number(value.get('max')) or not 0<=value['min']<=value['max']):raise HTTPException(422,'篇幅范围应为 {min,max} 且下限不大于上限')
    if checker in ('required_sections','required_terms','forbidden_terms') and (not isinstance(value,list) or not value or any(not isinstance(x,str) or not x.strip() for x in value)):raise HTTPException(422,'此检查器需要非空文字数组')
    if checker=='reference_foreign_ratio' and (not finite_number(value) or not 0<=value<=1):raise HTTPException(422,'外文比例应在0至1之间')
    if checker=='reference_recent' and (not isinstance(value,dict) or type(value.get('years')) is not int or not 1<=value['years']<=100 or not finite_number(value.get('minimum')) or not 0<=value['minimum']<=1 or type(value.get('anchor_year')) is not int or not 1900<=value['anchor_year']<=2200):raise HTTPException(422,'近年文献应配置years、minimum和明确的anchor_year')
    if checker=='format_value' and (not isinstance(value,dict) or value.get('key') not in FORMAT_KEYS or 'expected' not in value):raise HTTPException(422,'格式检查应指定key与expected')
    if checker=='format_value':
        key=value['key'];expected=value['expected']
        if key in ('font','latin_font','heading_font'):
            if not isinstance(expected,str) or not 1<=len(expected)<=100:raise HTTPException(422,'字体名应为非空文字')
        elif key=='reference_superscript':
            if not isinstance(expected,bool):raise HTTPException(422,'上标设置应为true或false')
        else:
            bounds=(3,100) if key=='font_size' else (.5,5) if key=='line_spacing' else (0,20) if key=='first_line_chars' else (0,80)
            if not isinstance(expected,(int,float)) or isinstance(expected,bool) or not math.isfinite(expected) or not bounds[0]<=expected<=bounds[1]:raise HTTPException(422,'排版目标数值超出支持范围')
    return checker,value,operations

def infer(statement,scope='global'):
    text=statement.replace(' ','');result={'checker':'manual','value':None,'rule_key':'','scope':scope}
    def number(raw):
        if raw.isdigit():return int(raw)
        vals={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
        if raw in vals:return vals[raw]
        if '十' in raw:
            a,b=raw.split('十',1);return vals.get(a,1)*10+vals.get(b,0)
        return None
    if '摘要' in text:result['scope']='abstract_en' if '英文摘要' in text else 'abstract'
    m=re.search(r'(?:不少于|至少)([\d一二三四五六七八九十]+)篇',text)
    if '参考文献' in text and m:
        n=number(m[1])
        if n is not None:result.update(checker='reference_min',value=n,rule_key='reference_min');return result
    m=re.search(r'(\d+)[—–~～至到-](\d+)字',text)
    if m:result.update(checker='word_range',value={'min':int(m[1]),'max':int(m[2])},rule_key='word_count');return result
    m=re.search(r'(?:不少于|至少|不超过|不得超过|控制在)(\d+)字',text)
    if m:result.update(checker='word_min' if '不少于' in m[0] or '至少' in m[0] else 'word_max',value=int(m[1]),rule_key='word_count');return result
    m=re.search(r'(?:不少于|至少)(\d+)个二级标题',text)
    if m:result.update(checker='heading_min',value=int(m[1]),rule_key='heading_min');return result
    format_value=None
    for label,key in [('上边距','margin_top'),('下边距','margin_bottom'),('左边距','margin_left'),('右边距','margin_right')]:
        m=re.search(label+r'[:：为是]?(\d+(?:\.\d+)?)(毫米|厘米|mm|cm)',text,re.I)
        if m:format_value={'key':key,'expected':float(m[1])*(10 if m[2].lower() in ('cm','厘米') else 1)}
    if '正文' in text:
        for label,size in [('小四',12),('四号',14),('五号',10.5)]:
            if label in text:
                format_value={'key':'font_size','expected':size};break
        m=re.search(r'行距[:：为]?(\d+(?:\.\d+)?)倍',text)
        if m:format_value={'key':'line_spacing','expected':float(m[1])}
        for font in ('宋体','仿宋','楷体','黑体'):
            if font in text and '字号' not in text:format_value={'key':'font','expected':font}
    if format_value:result.update(checker='format_value',value=format_value,rule_key='format:'+format_value['key'])
    return result

def project_input_fingerprint(pid,section_ids=None):
    sections=db.rows("SELECT id,current_revision_id,title,kind,goal,parent_id FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,))
    facts=db.rows('SELECT id,revision FROM facts WHERE project_id=? ORDER BY id',(pid,));refs=db.rows('SELECT id,revision FROM bibliography WHERE project_id=? ORDER BY id',(pid,))
    p=db.one('SELECT rule_package_id,brief,target_words FROM projects WHERE id=?',(pid,));value={'review_scope':section_ids,'project':p,'sections':sections,'facts':facts,'references':refs,'format':db.setting('format:'+pid,{}),'files':db.rows('SELECT id,sha256,active,egress,role FROM files WHERE project_id=? ORDER BY id',(pid,)),'sources':db.rows('SELECT s.id,s.kind,s.verified FROM sources s JOIN files f ON f.id=s.file_id WHERE f.project_id=? ORDER BY s.id',(pid,))}
    return hashlib.sha256(db.dumps(value).encode()).hexdigest()

def evaluate(rule,pid,sections,refs):
    from .exports import format_config
    value=db.loads(rule.get('value'));checker=rule.get('checker','manual');scope=rule.get('scope','global');target=[s for s in sections if scope in ('global',s['kind'],s['id'])]
    result=[]
    def add(message,state='pass',sid=None,rid=None,evidence=''):
        result.append({'rule_id':rule['id'],'section_id':sid,'revision_id':rid,'result':state,'message':message,'method':'human' if checker in ('manual','rendered_page_min') else 'deterministic','severity':'blocker' if rule['modality']=='mandatory' and state=='fail' else 'warning' if state!='pass' else 'info','evidence':evidence})
    if checker=='required_sections':
        missing=[v for v in value if not any(s['kind']==v or v in s['title'] for s in sections)];add('必需章节：'+('缺少 '+', '.join(missing) if missing else '结构齐备'),'fail' if missing else 'pass');return result
    if checker=='format_value':
        key=value['key'];actual=format_config(pid).get(key);expected=value['expected'];equal=abs(float(actual)-float(expected))<.001 if isinstance(expected,(float,int)) and isinstance(actual,(float,int)) else actual==expected
        add(f'排版参数 {key}：当前 {actual}，要求 {expected}','pass' if equal else 'fail',evidence='仅核对配置；实际DOCX属性与分页需在导出中检查');return result
    used={r['reference_id'] for s in sections if s.get('current_revision_id') for r in db.rows('SELECT reference_id FROM citations WHERE revision_id=?',(s['current_revision_id'],))};unique={}
    for ref in refs:
        if ref['id'] not in used:continue
        m=db.loads(ref['metadata'],{});key=str(m.get('doi') or (str(m.get('title','')).casefold()+str(m.get('year',''))));unique[key or ref['id']]=m
    if checker=='reference_min':add(f'正文已引用的去重文献数量：{len(unique)}/{value}（登记书目{len(refs)}条）','pass' if len(unique)>=value else 'fail');return result
    if checker in ('reference_recent','reference_foreign_ratio'):
        if not unique:add('尚未引用文献，无法满足比例要求','fail');return result
        yes=unknown=0
        for m in unique.values():
            if checker=='reference_recent':
                year=str(m.get('year',''));parsed=re.search(r'\b(19|20|21)\d{2}\b',year)
                if not parsed:unknown+=1
                elif value['anchor_year']-value['years']+1<=int(parsed[0])<=value['anchor_year']:yes+=1
            else:
                language=str(m.get('language','')).lower()
                if not language:unknown+=1
                elif language not in ('zh','zh-cn','zh-tw','chinese','中文'):yes+=1
        minimum=value['minimum'] if isinstance(value,dict) else value
        minimum_ratio=yes/len(unique);maximum_ratio=(yes+unknown)/len(unique)
        state='pass' if minimum_ratio>=minimum else 'fail' if maximum_ratio<minimum else 'unknown'
        add(f'文献比例：已知满足 {yes}/{len(unique)}，字段缺失 {unknown}，要求至少 {minimum:.0%}',state,evidence='近年按配置anchor_year及包含边界计算；未知年份/语种不推测');return result
    if not target:add('当前没有适用章节','not_applicable');return result
    for section in target:
        revision=documents.current(section);sid=section['id'];rid=revision['id'] if revision else None
        if checker in ('manual','rendered_page_min'):
            fingerprint=project_input_fingerprint(pid,[sid]);package=db.one('SELECT rule_package_id FROM projects WHERE id=?',(pid,))
            record=db.one('SELECT * FROM manual_rule_reviews WHERE project_id=? AND rule_id=? AND package_id=? AND input_fingerprint=? ORDER BY created_at DESC LIMIT 1',(pid,rule['id'],package['rule_package_id'],fingerprint))
            add(rule['statement'][:240]+('（人工记录：'+record['note']+'）' if record else ''),record['result'] if record else 'unknown',sid,rid,'人工按当前输入快照核对；不能证明其他版本符合');continue
        if not revision:add('章节无正文，无法核对：'+rule['statement'][:100],'fail',sid);continue
        text=revision['text'];doc=db.loads(revision['document'],{});count=documents.counts(text)['total'];state='pass';message=''
        if checker in ('word_min','word_max','word_range'):
            lo=value['min'] if checker=='word_range' else value if checker=='word_min' else 0;hi=value['max'] if checker=='word_range' else value if checker=='word_max' else float('inf');state='pass' if lo<=count<=hi else 'fail';message=f'篇幅 {count}，要求 {lo}–{hi if hi!=float("inf") else "不限"}'
        elif checker in ('required_terms','forbidden_terms'):
            found=[v for v in value if (v not in text if checker=='required_terms' else v in text)];state='fail' if found else 'pass';message=('缺少必需词项：' if checker=='required_terms' else '出现禁止词项：')+'、'.join(found) if found else '词项规则通过'
        elif checker in ('heading_min','figure_min','table_min'):
            typ={'heading_min':'heading','figure_min':'figure','table_min':'table'}[checker];n=sum(x['type']==typ for x in documents.nodes(doc));n+=sum(x.get('parent_id')==sid for x in sections) if checker=='heading_min' else 0;state='pass' if n>=value else 'fail';message=f'{typ} 数量 {n}，至少 {value}'
        else:state='unknown';message='检查器暂不支持：'+checker
        add(message,state,sid,rid)
    return result
