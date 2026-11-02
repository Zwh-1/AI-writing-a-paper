"""Explicit remote-source contract. OCR recognition data stays in local provenance."""
import hashlib
from . import db

LOCATOR_FIELDS=('kind','page','slide','page_number','slide_number','char_start','char_end','paragraph','part','read_scope','transcription_scope','extraction_origin')


def compact_locator(value):
    value=db.loads(value,{}) if isinstance(value,str) else value
    if not isinstance(value,dict):return {}
    return {key:item for key in LOCATOR_FIELDS if isinstance((item:=value.get(key)),(int,float,str)) and not isinstance(item,bool) and (not isinstance(item,str) or len(item)<=120)}

def compact_reference_metadata(metadata):
    """Keep every bibliographic value, including conflicting raw/structured values."""
    return {key:value for key,value in metadata.items() if key not in ('archive_member','archive_sha256','catalog_number')}


def source_hash(source):
    return hashlib.sha256(db.dumps([source['id'],source['text'],source.get('method'),source.get('file_sha256')]).encode()).hexdigest()


def outgoing(source):
    result={'source_id':source['id'],'text':source['text'],'locator':compact_locator(source.get('locator'))}
    if source.get('part_start') is not None:
        result['part_start']=source['part_start'];result['part_end']=source['part_end']
    return result


def content(sources):return db.dumps({'sources':[outgoing(source) for source in sources]})


def token_upper_bound(system,user):
    # No tokenizer is assumed for relay model aliases. Reserve conservatively for
    # UTF-8 text and protocol overhead; record the method instead of claiming exact tokens.
    return len((system+'\n'+user).encode('utf-8'))+512
