"""Preserve rule intent and route explicitly named composite chapter parts."""
from copy import deepcopy
from . import rule_engine


PARTS = {
    'methodology': ('相关技术', '关键技术', '技术基础', '研究方法', '理论基础'),
    'literature_review': ('研究现状', '文献综述', '相关研究'),
    'requirements': ('需求分析',),
    'system_design': ('系统设计', '总体设计', '架构设计'),
    'implementation': ('系统实现', '详细实现'),
    'testing': ('系统测试', '结果分析', '测试分析'),
    'conclusion': ('总结与展望',),
}


def section_parts(section):
    """Only the declared kind and literal title parts; never infer from prose."""
    result=[{'kind':rule_engine.section_kind(section['kind']), 'target':'本章适用内容'}]
    title=section.get('title','')
    for kind,labels in PARTS.items():
        label=next((label for label in labels if label in title),None)
        if label and kind not in {item['kind'] for item in result}:
            result.append({'kind':kind,'target':'本章“'+label+'”部分'})
    return result


def application(rule, matched_parts=(), include_statement=False):
    value={key:deepcopy(rule.get(key)) for key in
           ('id','scope','modality','category','source_id','checker','value')}
    if include_statement:value['statement']=rule['statement']
    value['conditions']=deepcopy(rule.get('conditions') or {})
    value['applies_to']=deepcopy(rule.get('applies_to') or list(matched_parts))
    return value


def for_context(rules):
    return [application(rule) for rule in rules]


def parse_reviews(value, applications, paragraphs):
    """A loaded ID is not compliance. Require located, explicit assessments."""
    expected={row['id']:row for row in applications};seen=set();reviews=[];findings=[]
    rows=value.get('rule_reviews')
    if not isinstance(rows,list):return [],[],False
    for row in rows:
        if not isinstance(row,dict) or row.get('rule_id') not in expected or row['rule_id'] in seen:
            raise ValueError('规则复核返回了未知或重复规则编号')
        seen.add(row['rule_id']);status=row.get('status');reason=row.get('reason')
        if status not in {'satisfied','violation','not_applicable','unknown'} or not isinstance(reason,str) or not reason.strip():
            raise ValueError('规则复核需要明确状态与依据')
        indices=row.get('paragraph_indices',[]);quote=row.get('quote','')
        if not isinstance(indices,list) or any(type(i) is not int or not 0<=i<len(paragraphs) for i in indices):
            raise ValueError('规则复核引用了未提供的段落')
        if status in {'satisfied','violation'} and (not indices or not isinstance(quote,str) or not quote.strip()
                or not any(quote in paragraphs[i].get('text','') for i in indices)):
            status='unknown'
        record={**row,'status':status,'method':'model_assisted_rule_application','human_review':'pending'}
        reviews.append(record)
        if status=='violation':
            index=next(i for i in indices if quote in paragraphs[i].get('text',''))
            findings.append({'kind':'rule_application','rule_id':row['rule_id'],'paragraph_index':index,
                             'message':reason,'excerpt':quote,'repair_query':reason,
                             'severity':'blocker' if expected[row['rule_id']].get('modality')=='mandatory' else 'warning',
                             'method':'model_assisted_rule_application'})
    complete=seen==set(expected) and all(row['status']!='unknown' for row in reviews)
    return reviews,findings,complete
