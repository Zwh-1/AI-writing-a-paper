"""Versioned extraction candidates. No inferred boundary grants semantic support."""
import io
import re
from . import db, security

ENGINE = 'native-structure-v5-complete-views'
STRATEGIES = {'paragraphs', 'page'}


def spans(text, strategy='paragraphs'):
    if strategy == 'page':
        return [(0, len(text))] if text.strip() else []
    # Whitespace and offsets belong to the extraction version, not the original PDF bytes.
    bounds = [0] + [m.end() for m in re.finditer(r'(?:\r?\n)[ \t]*(?:\r?\n)', text)] + [len(text)]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if text[a:b].strip()]


def pdf_pages(original):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(security.safe_object(original['sha256'])))
    try:
        for number in range(len(pdf)):
            page = pdf[number]
            tp = page.get_textpage()
            try:
                text = tp.get_text_bounded()
                blocks = []
                for a, b in spans(text):
                    quote = text[a:b].strip()
                    box = None
                    search = tp.search(quote)
                    try:
                        match = search.get_next()
                        if match:
                            boxes = [tp.get_charbox(i) for i in range(match[0], match[0] + match[1])]
                            if boxes:
                                box = [min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes)]
                    finally:
                        search.close()
                    blocks.append({'start': a, 'end': b, 'bbox': box})
                yield {'number': number + 1, 'text': text, 'blocks': blocks, 'origin': 'pdfium_text_order',
                       'page_size': list(page.get_size()), 'coordinate_system': 'pdf_points_bottom_left', 'physical_page': True}
            finally:
                tp.close()
                page.close()
    finally:
        pdf.close()


def docx_pages(original):
    from docx import Document
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    doc = Document(io.BytesIO(security.safe_object(original['sha256']).read_bytes()))
    text = ''; blocks = []; headings = []
    for index, element in enumerate(doc.element.body):
        kind = 'paragraph'; level = None
        if element.tag.endswith('}p'):
            para = Paragraph(element, doc); value = para.text
            match = re.search(r'(?:Heading|标题)\s*(\d+)', para.style.name if para.style else '', re.I)
            if match:
                level = int(match[1]); kind = 'heading'; headings = headings[:level - 1] + [value]
        elif element.tag.endswith('}tbl'):
            table = Table(element, doc); value = '\n'.join(' | '.join(c.text for c in row.cells) for row in table.rows); kind = 'table'
        else:
            continue
        if not value.strip():
            continue
        start = len(text); text += value + '\n\n'
        blocks.append({'start': start, 'end': len(text), 'kind': kind, 'paragraph': index + 1, 'heading_path': list(headings), 'level': level})
    yield {'number': 1, 'text': text, 'blocks': blocks, 'origin': 'ooxml_structure', 'physical_page': False}


def extract(original, engine=ENGINE):
    pages = db.rows('SELECT * FROM pages WHERE file_id=? ORDER BY page_number', (original['id'],))
    if engine == 'legacy':
        return [{'number': p['page_number'], 'text': p['text'], 'blocks': [], 'origin': 'legacy_extraction', 'physical_page': original['extension'] == '.pdf'} for p in pages]
    if engine != ENGINE:
        raise ValueError('未知解析引擎；本机结构解析不下载模型或自动调用外部服务')
    if original['extension'] == '.pdf':
        extracted = list(pdf_pages(original))
    elif original['extension'] == '.docx':
        extracted = list(docx_pages(original))
    else:
        from .parsers import parse_pages
        native=list(parse_pages(security.safe_object(original['sha256']).read_bytes(),original['extension']))
        extracted = [{'number': p['number'], 'text': p['text']+('\n[备注]\n'+p.get('notes','') if p.get('notes') else ''), 'blocks': [],
                      'origin': 'pptx_slide' if original['extension']=='.pptx' else 'native_extraction','unit_kind':'slide_topic' if original['extension']=='.pptx' else None,
                      'title':p.get('title',''),'document_title':original['name'],'physical_page': False} for p in native]
    # OCR/manual observations remain separate from native extraction, never silently blended.
    from .document_observations import observations
    superseded=db.setting('document_observation_supersessions',{})
    for page in pages:
        sources=db.rows("SELECT * FROM sources WHERE page_id=? AND method NOT IN ('native','native_supplement','document_rule_context') ORDER BY id", (page['id'],))
        for observation in observations(sources,superseded,preserve_unverified=True):
            if observation['text'].strip():
                extracted.append({'number':page['page_number'],'text':observation['text'],'blocks':[],
                                  'origin':observation['origin'],'source_id':observation['source_ids'][0],
                                  'observation_locator':observation['locator'],'observation_issues':observation['issues'],
                                  'unit_kind':'image_observation' if observation['origin'] in ('windows_ocr','assistant_visual_excerpt','unverified_image_transcript') else None,
                                  'physical_page':original['extension']=='.pdf','transcript_verified':False})
    return extracted


def candidates(page, strategy):
    text = page['text']; native_blocks = page.get('blocks', [])
    ranges = [(b['start'], b['end'], b) for b in native_blocks] if strategy == 'paragraphs' and native_blocks else [(a, b, {}) for a, b in spans(text, strategy)]
    if page.get('unit_kind')=='slide_topic':ranges=[(0,len(text),{'kind':'slide_topic','heading_path':[page.get('document_title',''),page.get('title','')]})]
    if page.get('unit_kind')=='image_observation':ranges=[(0,len(text),{'kind':'image_observation'})]
    for a, b, block in ranges:
        value = text[a:b]
        if not value.strip():
            continue
        issues = list(page.get('observation_issues',[]))
        if page['origin'] in ('pdfium_text_order','pptx_slide'):
            issues.append('reading_order_needs_review')
        if page['origin']=='pptx_slide':issues.append('teaching_scope_needs_review')
        if '\ufffd' in value:
            issues.append('replacement_characters')
        kind = block.get('kind', 'table' if ' | ' in value or len(re.findall(r'\S[ \t]{3,}\S', value)) >= 3 else 'paragraph')
        if page['origin'] not in ('ooxml_structure',) and strategy == 'paragraphs':
            issues.append('boundary_inferred')
        if not value.rstrip().endswith(('。', '！', '？', '.', '!', '?', '”', '」', '）', ')', ':', '：')) and kind != 'heading':
            issues.append('possible_continuation')
        if kind == 'table':
            issues.append('table_header_and_notes_need_review')
        if page['origin'] not in ('ooxml_structure', 'native_extraction', 'pdfium_text_order', 'legacy_extraction','pptx_slide'):
            issues.append('transcript_needs_original_comparison')
        if page['origin']=='assistant_visual_excerpt':
            issues=[issue for issue in issues if issue!='transcript_needs_original_comparison']
            if (page.get('observation_locator') or {}).get('observation_locator',{}).get('transcription_scope')!='complete_image_text':issues.append('selected_visual_excerpts_not_complete_image')
        locator = {'page': page['number'], 'char_start': a, 'char_end': b, 'physical_page': page.get('physical_page', False),
                   'coordinate_system': page.get('coordinate_system'), 'bbox': block.get('bbox'), 'paragraph': block.get('paragraph'),
                   'source_id': page.get('source_id'), 'extraction_origin': page['origin'], 'page_size': page.get('page_size')}
        if page['origin']=='pptx_slide':locator.update(slide_number=page['number'],slide_title=page.get('title',''),boundary_basis='whole_original_slide_not_semantic_closure')
        if page.get('observation_locator'):locator.update(page['observation_locator'])
        if locator.get('context_stream'):locator['context_stream']+=':page:'+str(page['number'])
        if page['origin']=='assistant_visual_excerpt':
            observed=(page.get('observation_locator') or {}).get('observation_locator',{})
            if observed.get('source_issues'):
                locator['source_issues']=observed['source_issues'];issues.extend(observed['source_issues'])
            locator.update(transcription_scope=observed.get('transcription_scope'),read_scope='inspected_visual_excerpts',
                evidence_asset_sha256=observed.get('evidence_asset_sha256'),entire_original_image_transcribed=observed.get('transcription_scope')=='complete_image_text')
        yield {'text': value, 'kind': kind, 'locator': locator, 'origin': page['origin'], 'issues': issues, 'heading_path': block.get('heading_path', [])}
