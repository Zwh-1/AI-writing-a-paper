"""Dual-record immutable read ranges; permissions are checked on every resolve."""
from .writing_quality_protocols import digest, RANGES


def freeze(project_id, observation):
    start, end = observation.get('char_start'), observation.get('char_end')
    text = observation.get('text')
    if type(start) is not int or type(end) is not int or start < 0 or end <= start or not isinstance(text, str) or len(text) != end-start:
        raise ValueError('Invalid exact read range')
    value = {'schema': RANGES, 'project_id': project_id,
        **{key: observation.get(key) for key in ('unit_id', 'file_id', 'parse_id', 'unit_hash', 'reading_id')},
        'char_start': start, 'char_end': end, 'text_hash': digest(text)}
    if not all(value[key] for key in ('unit_id', 'file_id', 'parse_id', 'unit_hash')):
        raise ValueError('Read range lacks immutable provenance')
    return {**value, 'hash': digest(value)}


def resolve(project_id, handle, *, remote=True):
    from . import knowledge_units as units
    if (not isinstance(handle, dict) or handle.get('schema') != RANGES or handle.get('project_id') != project_id
            or digest({key: value for key, value in handle.items() if key != 'hash'}) != handle.get('hash')):
        raise ValueError('Read range handle is foreign or altered')
    unit = units.get(project_id, handle['unit_id'], remote=remote)
    if not unit['current'] or any(unit[key] != handle[key] for key in ('file_id', 'parse_id')) or unit['content_hash'] != handle['unit_hash']:
        raise ValueError('Read range original version changed')
    start, end = handle['char_start'], handle['char_end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(unit['text']):
        raise ValueError('Read range bounds changed')
    text = unit['text'][start:end]
    if digest(text) != handle['text_hash']:raise ValueError('Read range text changed')
    return {'text': text, 'unit_id': unit['id'], 'file_id': unit['file_id'],
            'parse_id': unit['parse_id'], 'unit_hash': unit['content_hash'],
            'char_start': start, 'char_end': end, 'locator': unit['locator']}
