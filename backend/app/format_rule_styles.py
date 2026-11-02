"""Compile explicit layout statements by object, never by a global font guess."""
import re

SIZES={'初号':42,'小初':36,'一号':26,'小一':24,'二号':22,'小二':18,'三号':16,'小三':15,'四号':14,'小四':12,'五号':10.5,'小五':9,'六号':7.5}


def properties(text):
    compact=re.sub(r'\s+','',text);result={}
    font=re.search(r'宋体|黑体|楷体|仿宋',compact)
    if font:result['font']=font[0]
    if re.search(r'TimesNewRoman',compact,re.I):result['latin_font']='Times New Roman'
    size=re.search('|'.join(sorted(SIZES,key=len,reverse=True)),compact)
    if size:result['font_size']=SIZES[size[0]]
    spacing=re.search(r'固定(?:行距)?[：:]?(\d+(?:\.\d+)?)(?:pt|磅)',compact,re.I)
    if spacing:result['line_spacing_points']=float(spacing[1])
    if '加粗' in compact:result['bold']=True
    if '居中' in compact:result['alignment']='center'
    elif '两端对齐' in compact:result['alignment']='justify'
    elif '左对齐' in compact:result['alignment']='left'
    indent=re.search(r'(?:首行|段首)(?:缩进)?(\d+(?:\.\d+)?)字符',compact)
    if indent:result['first_line_chars']=float(indent[1])
    elif '取消首行缩进' in compact:result['first_line_chars']=0
    else:
        indent=re.search(r'缩进(\d+(?:\.\d+)?)字符',compact)
        if indent:result['left_indent_chars']=float(indent[1])
    before_after=re.search(r'段前[、和]?段后[：:]?各(\d+(?:\.\d+)?)行',compact)
    if before_after:result.update(space_before_lines=float(before_after[1]),space_after_lines=float(before_after[1]))
    return result


def compile_rule(rule):
    text=rule.get('statement','');note=rule.get('conditions',{}).get('scope_note','');compact=re.sub(r'\s+','',text)
    result={}
    def add(component,fragment,extra=None):
        values=properties(fragment)
        if extra:values.update(extra)
        if values:result.setdefault(component,{}).update(values)
    def line(pattern):
        match=re.search(pattern+r'[^\n]*',text)
        return match[0] if match else ''
    # Page/header and numbering requirements have no authority over body fonts.
    if '页码' in text and '罗马' in text:result.setdefault('document',{})['frontmatter_numbering']='lowerRoman' if '小写' in text else 'upperRoman'
    if '页码' in text and '阿拉伯' in text:result.setdefault('document',{})['body_numbering']='decimal'
    if '奇数页页眉为论文题目' in compact and '偶数页页眉为章节标题' in compact:result.setdefault('document',{}).update(chapter_headers=True,header_font='宋体',header_font_size=12)
    if '页眉' in text and '边距均为2厘米' in compact:result.setdefault('document',{}).update(header_distance=20,footer_distance=20)
    if '分节符' in text and '页眉' in text:result.setdefault('document',{})['use_section_breaks']=True
    if '摘要不上目录' in compact:result.setdefault('toc',{})['exclude_abstract']=True
    if '统一是阿拉伯数字' in compact and '标题' in text:result.setdefault('document',{})['heading_numbering']='arabic_space'
    if '新章节另起一页' in compact:result.setdefault('heading_1',{})['page_break_before']=True
    if '图片' in text and '图名' in text and '仅针对图片' in note:add('caption',text)
    if '仅针对封面题目' in note or '仅针对题目字段' in note:add('title',text)
    if '封面学院' in note or '封面字段' in note:add('cover_fields',text)
    if '目录格式要求' in text:
        add('toc_title',text.split('2.')[0])
        fixed=properties(text).get('line_spacing_points')
        for level,label in ((1,'一级目录'),(2,'二级目录'),(3,'三级目录')):
            add('toc_'+str(level),line(label),{'line_spacing_points':fixed} if fixed else None)
        add('toc_special',line('“参考文献”'),{'line_spacing_points':fixed} if fixed else None)
    if '仅针对目录中的' in note:add('toc_special',text)
    for level,marker in ((1,r'(?:每章标题|一级标题)'),(2,'二级标题'),(3,'三级标题')):
        if '目录' in note and '标题、正文、目录' not in note:continue
        fragment=line(marker)
        if fragment and ('大纲级别' in fragment or '号' in fragment):add('heading_'+str(level),fragment)
    body=re.search(r'(?:论文正文内容|正文内容)[\s\S]*?(?=\n\s*33\.|$)',text)
    if body:
        if rule.get('scope')=='abstract' or '仅针对中文摘要正文' in note:add('abstract_body',body[0])
        elif rule.get('scope')=='abstract_en':add('abstract_en_body',body[0])
        elif rule.get('scope','global')=='global':add('body',body[0])
    for labels,key in ((('上下','上','下','左','右','左右'),'margins'),):
        if '页面' in text and '页边距' in text:
            for match in re.finditer(r'(上下|左右|上|下|左|右)(?:页?边距)?(?:设置为?|为|是|[:：])?(\d+(?:\.\d+)?)(cm|厘米|mm|毫米)',compact,re.I):
                amount=float(match[2])*(10 if match[3].lower() in ('cm','厘米') else 1)
                for direction in match[1]:result.setdefault('body',{})[{'上':'margin_top','下':'margin_bottom','左':'margin_left','右':'margin_right'}[direction]]=amount
    if '右上标方括号' in compact:result.setdefault('body',{})['reference_superscript']=True
    if rule.get('scope')=='abstract' or '中文摘要' in text:
        fragment=line('中文摘要内容')
        if fragment:add('abstract_body',fragment)
        if '摘要标题' in note:add('abstract_heading',text)
        if '关键词' in note and '标签' in note:add('abstract_keywords',text,{'label_brackets':True} if '加方括号' in text else None)
    if rule.get('scope')=='abstract_en' or '英文摘要' in text:
        fragment=line('英文摘要内容')
        if fragment:add('abstract_en_body',fragment,{'font':'Times New Roman'})
        header=text.split('19.')[0]
        if 'Abstract' in header and ('小三' in header or '居中' in header):add('abstract_en_heading',header,{'font':'Times New Roman','page_break_before':True})
        keyword=line(r'(?:20\.|Keywords)')
        if keyword and '关键词' in text:add('abstract_en_keywords',keyword,{'font':'Times New Roman'})
    if rule.get('scope') in ('reference','references') and '仅针对参考文献条目' in note:add('references_body',text)
    return result


def compile_styles(rules):
    values={};conflicts=[];origins={};priorities={}
    for rule in rules:
        if rule.get('modality') not in ('mandatory','recommended'):continue
        priority=2 if rule['modality']=='mandatory' else 1
        for component,fields in compile_rule(rule).items():
            for key,value in fields.items():
                slot=(component,key);previous=priorities.get(slot,0)
                if priority<previous:continue
                if priority==previous and key in values.get(component,{}) and values[component][key]!=value:
                    conflicts.append({'component':component,'key':key,'rule_ids':[origins[slot],rule['id']]});continue
                priorities[slot]=priority;origins[slot]=rule['id'];values.setdefault(component,{})[key]=value
    return values,conflicts,{component:{key:origins[(component,key)] for key in fields} for component,fields in values.items()}
