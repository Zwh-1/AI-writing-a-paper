"""Read the original context behind a candidate, independently of legacy slices."""
from fastapi import HTTPException
from . import db, security
from .document_observations import observations

VERSION = 'rule-source-context-v1'


def requires_original_comparison(source):
    """Reading more OCR cannot substitute for comparing it with the image."""
    if not source or not source.get('file_id'):
        return False
    loc = db.loads(source.get('locator'), {})
    if source.get('method') == 'assistant_visual_excerpt':
        return False
    if source.get('method') == 'windows_ocr':
        return True
    if source.get('method') == 'visual_transcription':
        return loc.get('transcription_scope') not in ('complete_image_text', 'selected_complete_passages')
    unit = db.one('SELECT origin,locator FROM knowledge_units WHERE id=?', (loc.get('unit_id'),))
    if not unit:
        return False
    if unit['origin'] in ('windows_ocr', 'unverified_image_transcript'):
        return True
    if unit['origin'] == 'visual_transcription':
        meta = db.loads(unit['locator'], {})
        return (meta.get('transcription_scope') or meta.get('observation_locator', {}).get('transcription_scope')) not in ('complete_image_text', 'selected_complete_passages')
    return False


def read(rule, source, remote=False):
    from .visual_rule_repair import asset_ids
    if not source or not source.get('file_id'):
        return None
    original = db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1', (source['file_id'], rule['project_id']))
    if not original:
        raise HTTPException(404, '候选原件不属于当前项目或已停用')
    if remote and not security.file_allows_remote(original['id'], rule['project_id']):
        raise HTTPException(403, '原件禁止外发')
    page = db.one('SELECT * FROM pages WHERE id=? AND file_id=?', (source.get('page_id'), original['id']))
    if not page:
        return None
    if remote and page['review_state'] == 'not_applicable':
        raise HTTPException(403, '候选所在原页已排除')
    assets = asset_ids(source)
    rows = db.rows('SELECT * FROM sources WHERE file_id=? AND page_id=? ORDER BY id', (original['id'], page['id']))
    raw = []
    checked = []
    mapping = db.setting('document_observation_supersessions', {})
    for row in rows:
        loc = db.loads(row['locator'], {})
        asset = loc.get('asset_sha256') or loc.get('evidence_asset_sha256')
        if asset not in assets:
            continue
        if row['method'] == 'windows_ocr':
            raw.append(row)
        elif row['method'] in ('assistant_visual_excerpt', 'visual_transcription') and row['id'] not in mapping:
            checked.append({'source_id': row['id'], 'text': row['text'], 'asset_sha256': asset,
                            'coverage': loc.get('transcription_scope', 'unassessed'), 'source_issues': loc.get('source_issues', [])})
    complete = observations(raw)
    native = []
    # Read the original native page rather than the combined page/OCR cache or
    # the 800/1000-character source windows used by older extraction versions.
    if assets and original['extension'] == '.pptx':
        from .parsers import pptx_pages
        for original_page in pptx_pages(security.safe_object(original['sha256']).read_bytes()):
            if original_page['number'] == page['page_number']:
                native = [{'text': original_page['text'], 'notes': original_page['notes'], 'origin': 'original_slide'}]
                break
    else:
        native = [{'text': row['text'], 'origin': row['method']} for row in rows if row['method'] in ('native', 'native_supplement')]
    views = [{'text': observation['text'], 'source_ids': observation['source_ids'], 'locator': observation['locator'],
              'issues': observation['issues'], 'cannot_support_claims': True} for observation in complete]
    loc = db.loads(source.get('locator'), {})
    old_unit = db.one('SELECT locator FROM knowledge_units WHERE id=?', (loc.get('unit_id'),))
    old_loc = db.loads(old_unit['locator'], {}) if old_unit else {}
    issues = []
    if raw and not any(observation['text'] == source['text'] for observation in complete):
        issues.append('legacy_source_is_not_complete_image_observation')
    if assets and not raw:
        issues.append('original_image_has_no_saved_ocr')
    if any(observation['issues'] for observation in complete):
        issues.append('observation_storage_requires_review')
    return {'version': VERSION, 'file_id': original['id'], 'file_hash': original['sha256'], 'page': page['page_number'],
            'page_id': page['id'], 'asset_sha256s': sorted(assets), 'native_context': native, 'raw_observations': views,
            'verified_passages': checked, 'source_issues': issues,
            'legacy_source_characters': len(source['text']), 'full_observation_characters': sum(len(o['text']) for o in complete),
            'legacy_unit_locator': old_loc, 'requested_scope_complete': True, 'candidate_verified': False,
            'requires_original_comparison': requires_original_comparison(source),
            'scope_note': '完整原页文字、全部保存OCR与校对摘录分别提供；恢复读取范围不代表候选已核验。'}


def model_context(rule, source):
    result = read(rule, source, remote=True)
    if not result:
        return source['text'], source.get('page_text') or source['text']
    text = source['text']
    if result['raw_observations'] and requires_original_comparison(source):
        text = '\n\n'.join(o['text'] for o in result['raw_observations'])
    context = '\n\n'.join(p['text'] + ('\n[原页备注]\n' + p['notes'] if p.get('notes') else '') for p in result['native_context'])
    if result['raw_observations'] and text == source['text']:
        context += '\n\n[完整保存OCR，尚未核对]\n' + '\n\n'.join(o['text'] for o in result['raw_observations'])
    if result['verified_passages']:
        context += '\n\n[独立校对摘录，不能替代完整观察]\n' + '\n\n'.join(p['text'] for p in result['verified_passages'])
    return text, context or source.get('page_text') or source['text']
