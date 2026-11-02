"""Keep superseded rules addressable without mixing them into the current catalog."""
from . import db


def visible(pid,rows):
    retired=set(db.setting('retired_rule_ids:'+pid,[]))
    return [row for row in rows if row['id'] not in retired]


def sql(alias='r'):
    if alias not in ('r','rules'):raise ValueError('Invalid rule alias')
    return f" NOT EXISTS(SELECT 1 FROM settings archived,json_each(archived.value) member WHERE archived.key='retired_rule_ids:'||{alias}.project_id AND member.value={alias}.id) "
