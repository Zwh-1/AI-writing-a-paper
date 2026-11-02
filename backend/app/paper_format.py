"""Resolve executable published formatting rules for the editor and exports."""
from . import db, rules, security, rule_engine
from .writing_agent.common import digest

DEFAULT = {'font':'宋体','latin_font':'Times New Roman','font_size':12,'line_spacing':1.5,'line_spacing_points':None,'alignment':'justify',
           'first_line_chars':2,'margin_top':25,'margin_bottom':25,'margin_left':30,'margin_right':25,
           'heading_font':'黑体','reference_superscript':True}


def resolve(pid, package=None, settings=None, section=None, project=None):
    p = project or security.require_project(pid)
    if package is None and project is None:
        package = db.one('SELECT * FROM rule_packages WHERE id=? AND project_id=?', (p['rule_package_id'], pid))
    configured = db.setting('format:'+pid,{}) if settings is None else settings
    result = {**DEFAULT, **(configured or {})}
    candidates = db.loads(package['rules'],[]) if package else []
    if section:
        operation='translate' if section['kind']=='abstract_en' else 'generate'
        context={'paper_type':p['paper_type'],'section_kind':rule_engine.section_kind(section['kind']),'language':'en' if section['kind']=='abstract_en' else 'zh','operation':operation}
        candidates=[r for r in candidates if (r['scope']=='global' or r['scope']==section['id'] or rule_engine.section_kind(r['scope'])==rule_engine.section_kind(section['kind'])) and operation in db.loads(r.get('operations'),[]) and rule_engine.condition_matches(r.get('conditions',{}),context)]
    values = {}; origins = {}; conflicts = []
    for rule in candidates:
        if rule.get('checker') != 'format_value' or rule.get('modality') != 'mandatory':
            continue
        if section is None and rule.get('scope','global') != 'global':
            continue
        if section is None and not rule_engine.condition_matches(rule.get('conditions',{}), {'paper_type':p['paper_type'],'section_kind':'body','language':'zh','operation':'generate'}):
            continue
        value = db.loads(rule.get('value'),{}) if isinstance(rule.get('value'),str) else rule.get('value',{})
        key = value.get('key')
        if key not in rule_engine.FORMAT_KEYS or 'expected' not in value:
            continue
        values.setdefault(key,[]).append(value['expected']); origins.setdefault(key,[]).append(rule['id'])
    for key, choices in values.items():
        unique = {db.dumps(x) for x in choices}
        if len(unique) == 1:
            result[key] = choices[0]
        else:
            conflicts.append({'key':key,'values':choices,'rule_ids':origins[key]})
    from .format_rule_styles import compile_styles,compile_rule
    available_rules=db.loads(package['rules'],[]) if package else []
    all_rules=[]
    for rule in available_rules:
        kinds=[section['kind']] if section else ['abstract','abstract_en','body','system_design','literature','reference','references']
        if any(rule_engine.condition_matches(rule.get('conditions',{}),{'paper_type':p['paper_type'],'section_kind':rule_engine.section_kind(kind),'operation':'translate' if kind=='abstract_en' else 'generate','language':'en' if kind=='abstract_en' else 'zh'}) for kind in kinds):all_rules.append(rule)
    components,component_conflicts,component_origins=compile_styles(all_rules)
    for key,value in components.get('body',{}).items():
        if key not in (configured or {}) and key not in values:result[key]=value
    if section:
        name='abstract_body' if section['kind']=='abstract' else 'abstract_en_body' if section['kind']=='abstract_en' else 'references_body' if section['kind'] in ('reference','references') else 'body'
        for key,value in components.get(name,{}).items():
            if key not in (configured or {}) and key not in values:result[key]=value
    conflicts.extend(component_conflicts)
    manual=[{'rule_id':r['id'],'statement':r['statement'],'scope':r['scope']} for r in all_rules if r.get('category')=='format' and r.get('checker')=='manual' and not compile_rule(r)]
    return {'config':result,'conflicts':conflicts,'rule_ids':origins,'components':components,'component_sources':component_origins,'manual_rules':manual,
            'version':digest({'config':result,'conflicts':conflicts,'components':components,'package':p['rule_package_id']})}
