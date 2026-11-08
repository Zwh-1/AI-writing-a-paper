"""Shared visual generation rules for chapter writers and standalone diagrams."""
from .config import ROOT

VERSION = 'academic-visuals.v1'


def guidance(kind=None):
    text=(ROOT / 'prompts' / (VERSION + '.txt')).read_text(encoding='utf-8').strip()
    chapter={'requirements':'第三章','system_design':'第四章','implementation':'第五章','results':'第六章',
             'er':'第四章','flowchart':'第五章','sequence':'第五章','use_case':'第三章'}.get(kind)
    return '\n'.join(line for line in text.splitlines() if not line.startswith(('第三章','第四章','第五章','第六章')) or not chapter or line.startswith(chapter))


def summary():
    return ('图表规则 '+VERSION+'：先定本节问题，候选计划按需选用，不凑数量或跨章复制。表格突出关键字段与联合约束，来源范围留在记录。'
            'ER按明确引用及可空/唯一条件画基数；静态调用不能当运行时序。保留skip、todo、失败、未执行状态，不编造测试结果。'
            '图题简洁，图后用独立段落解释具体元素、关系和设计理由；编号与交叉引用交给程序。已有图表优先引用，长英文按词或标识边界换行。')
