"""Local reference packages: catalog metadata, immutable originals and import receipts."""
import csv
import hashlib
import io
import re
import stat
import unicodedata
import zipfile
from pathlib import PurePosixPath

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader

from . import db, documents, parsers, security, tasks
from .config import MAX_FILE, MAX_UNPACKED

router = APIRouter(prefix='/projects/{pid}/references')


def title_key(value):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', unicodedata.normalize('NFKC', str(value)).casefold())


def doi_key(value):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/|^doi:\s*', '', str(value or '').strip(), flags=re.I).rstrip('.,;。').casefold()


def citation_metadata(citation, year='', language=''):
    citation = re.sub(r'^\s*\[\d+\]\s*', '', str(citation)).strip()
    match = re.match(r'^(.+?)\.\s*(.+?)\[([A-Z](?:/[A-Z]+)?)\](.*)$', citation, re.S)
    if not match:
        raise ValueError('无法识别完整著录中的作者、题名和文献类型')
    author_text, title, kind, tail = match.groups()
    authors = [part.strip() for part in re.split(r'[,，]', author_text) if part.strip()]
    authors = [part + '.' if part.casefold() == 'et al' else part for part in authors]
    doi = re.search(r'10\.\d{4,9}/[^\s]+', tail, re.I)
    tail = re.split(r'\bDOI\s*:', tail, flags=re.I)[0].strip(' ./')
    year = str(year or (re.search(r'\b(?:19|20)\d{2}\b', tail) or [''])[0])
    before, _, after = tail.partition(year) if year else (tail, '', '')
    container = before.rstrip(' ,，.。')
    volume_pages = after.strip(' ,，.。')
    volume, _, pages = volume_pages.partition(':')
    return {
        'title': title.strip(), 'authors': authors, 'year': year,
        'type': kind, 'journal': container if kind != 'D' else '',
        'publisher': container if kind == 'D' else '',
        'volume': volume.strip(), 'pages': pages.strip(' .。'),
        'doi': doi.group().rstrip('.,;。') if doi else '',
        'language': 'zh' if language in ('中文', 'zh') else 'en' if language in ('外文', '英文', 'en') else '',
        'scope': 'full_text', 'citation_raw': citation, 'metadata_source': '包内文献索引',
    }


def inspect_archive(data, filename='文献包.zip'):
    if not data or len(data) > MAX_FILE:
        raise ValueError('文献压缩包为空或超过120MB')
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError('文件不是有效ZIP压缩包') from error
    with archive:
        def read_member(entry):
            try:
                return archive.read(entry)
            except (zipfile.BadZipFile, RuntimeError, OSError) as error:
                raise ValueError('无法读取文献包成员：' + entry.filename) from error

        entries = archive.infolist()
        if len(entries) > 1000 or sum(entry.file_size for entry in entries) > MAX_UNPACKED:
            raise ValueError('文献包文件数量或解压体积超过上限')
        names = {}
        for entry in entries:
            name = entry.filename.replace('\\', '/')
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or ':' in name or '\x00' in name or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('文献包包含不安全路径或符号链接')
            if entry.flag_bits & 1:
                raise ValueError('请先移除文献包密码再导入')
            if entry.is_dir():
                continue
            normalized = name.casefold()
            if normalized in names:
                raise ValueError('文献包含重复路径，无法确定原件')
            names[normalized] = entry
        catalogs = [entry for name, entry in names.items() if name.endswith('文献索引.csv')]
        if len(catalogs) != 1:
            raise ValueError('文献包需包含唯一的“文献索引.csv”，列为：分类、文件名、年份、语种、完整著录')
        catalog = catalogs[0]
        if catalog.file_size > 5_000_000:
            raise ValueError('文献索引超过5MB')
        raw = read_member(catalog)
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeError:
            text = raw.decode('gb18030')
        rows = list(csv.DictReader(io.StringIO(text)))
        required = {'文件名', '完整著录'}
        if not rows or not required.issubset(rows[0]) or len(rows) > 500:
            raise ValueError('文献索引缺少文件名、完整著录，或记录数量超过500条')
        records = []
        indexed = set()
        for number, row in enumerate(rows, 1):
            relative = '/'.join(part.strip('/\\') for part in (row.get('分类', ''), row['文件名']) if part).replace('\\', '/')
            entry = names.get(relative.casefold())
            if not entry:
                matches = [item for item in names.values() if PurePosixPath(item.filename.replace('\\', '/')).name == row['文件名']]
                entry = matches[0] if len(matches) == 1 else None
            if not entry or not entry.filename.lower().endswith('.pdf'):
                raise ValueError(f'索引第{number}条未找到唯一PDF原件：{row["文件名"]}')
            if entry.filename in indexed:
                raise ValueError('索引重复引用同一PDF，请先核对索引')
            indexed.add(entry.filename)
            pdf = read_member(entry)
            if not pdf.startswith(b'%PDF-') or len(pdf) > MAX_FILE:
                raise ValueError(f'原件不是有效PDF或超过大小上限：{row["文件名"]}')
            try:
                reader = PdfReader(io.BytesIO(pdf), strict=False)
                if reader.is_encrypted and not reader.decrypt(''):
                    raise ValueError('PDF已加密')
                pages = len(reader.pages)
                if not pages:
                    raise ValueError('PDF没有页面')
                first_page = reader.pages[0].extract_text() or ''
            except Exception as error:
                raise ValueError(f'无法读取PDF原件：{row["文件名"]}') from error
            metadata = citation_metadata(row['完整著录'], row.get('年份', ''), row.get('语种', ''))
            warnings = []
            if row.get('页数', '').isdigit() and int(row['页数']) != pages:
                warnings.append(f'索引记载{row["页数"]}页，原件实际{pages}页')
            found_dois = list(dict.fromkeys(re.findall(r'10\.\d{4,9}/[^\s<>]+', unicodedata.normalize('NFKC', first_page), re.I)))
            if not metadata['doi'] and len(found_dois) == 1:
                metadata['doi'] = found_dois[0].rstrip('.,;。)')
                metadata['doi_source'] = 'PDF首页文字，待核对'
            metadata.update(
                category=row.get('分类', ''), catalog_number=row.get('论文编号', str(number)),
                archive_member=entry.filename, original_pages=pages, import_warnings=warnings,
            )
            records.append({
                'metadata': metadata, 'member': entry.filename, 'filename': row['文件名'],
                'sha256': hashlib.sha256(pdf).hexdigest(), 'pages': pages,
            })
        remaining = [entry.filename for entry in names.values() if entry.filename.lower().endswith('.pdf') and entry.filename not in indexed]
        if remaining:
            raise ValueError('存在未列入索引的PDF，请补齐索引后导入：' + '；'.join(remaining[:5]))
        return {
            'filename': PurePosixPath(filename.replace('\\', '/')).name,
            'sha256': hashlib.sha256(data).hexdigest(), 'catalog': catalog.filename,
            'records': records, 'pdf_count': len(records), 'pages': sum(item['pages'] for item in records),
            'attachments': [entry.filename for entry in names.values() if entry.filename not in indexed],
            'warnings': ['按当前CSV索引与PDF原件建立文献库；其他清单和说明保留在原始压缩包中，不自动合并历史条目。'],
        }


@db.atomic
def import_archive(pid, data, filename, expected_sha256=None):
    security.require_project(pid)
    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise HTTPException(409, '压缩包已变化，请重新预览')
    preview = inspect_archive(data, filename)
    existing = [{**row, 'meta': db.loads(row['metadata'], {})} for row in db.rows('SELECT * FROM bibliography WHERE project_id=?', (pid,))]
    archive_sha = security.put_blob(data)
    imported = linked = unchanged = 0
    reference_ids = []
    parse_tasks = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for record in preview['records']:
            meta = {**record['metadata'], 'archive_sha256': archive_sha}
            pdf = archive.read(record['member'])
            original, added = parsers.register_file(pid, record['filename'], pdf, 'research')
            same = next((item for item in existing if item.get('file_id') == original['id']), None)
            if same is None:
                same = next((item for item in existing if (
                    meta.get('doi') and doi_key(meta['doi']) == doi_key(item['meta'].get('doi'))
                ) or title_key(meta['title']) == title_key(item['meta'].get('title', ''))), None)
            if same:
                if not same['file_id']:
                    linked_meta = {**same['meta'], 'scope': 'full_text', 'archive_member': record['member'], 'archive_sha256': archive_sha, 'original_pages': record['pages']}
                    linked_meta.setdefault('source_citation', meta['citation_raw'])
                    from .reference_service import save as save_reference
                    current_reference=db.one('SELECT verification FROM bibliography WHERE id=?',(same['id'],))
                    save_reference(pid,linked_meta,original['id'],same['id'],current_reference['verification'])
                    same['file_id'] = original['id']
                    same['meta'] = linked_meta
                    linked += 1
                else:
                    unchanged += 1
                rid = same['id']
            else:
                from .reference_service import save as save_reference
                rid=save_reference(pid,meta,original['id'])['id']
                existing.append({'id': rid, 'file_id': original['id'], 'meta': meta})
                imported += 1
            reference_ids.append(rid)
            if added or original['status'] in ('queued', 'failed'):
                job = tasks.enqueue(pid, 'parse', {'file_id': original['id'], 'native_only': True, 'skip_ai_autopilot': True})
                parse_tasks.append(job['id'])
    result = {key: value for key, value in preview.items() if key != 'records'}
    result.update(imported=imported, linked=linked, unchanged=unchanged, reference_ids=reference_ids, parse_tasks=parse_tasks, verification='unverified', paid_calls=0)
    prior = db.one("SELECT id FROM project_revisions WHERE project_id=? AND kind='reference_archive' AND json_extract(content,'$.sha256')=?", (pid, archive_sha))
    if not prior:
        db.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)', (db.uid(), pid, 'reference_archive', db.dumps(result), db.now()))
    db.audit(pid, 'reference_archive_imported', {'sha256': archive_sha, 'imported': imported, 'linked': linked, 'unchanged': unchanged})
    return result


async def upload_bytes(file):
    if not (file.filename or '').lower().endswith('.zip'):
        raise HTTPException(422, '请选择ZIP文献包')
    data = await file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise HTTPException(422, '文献包超过120MB')
    return data


@router.post('/archive-preview')
async def archive_preview(pid: str, file: UploadFile = File(...)):
    security.require_project(pid)
    return inspect_archive(await upload_bytes(file), file.filename or '文献包.zip')


@router.post('/archive-import')
async def archive_import(pid: str, file: UploadFile = File(...), expected_sha256: str = Form('')):
    security.require_project(pid)
    return import_archive(pid, await upload_bytes(file), file.filename or '文献包.zip', expected_sha256 or None)


@router.get('/archives')
def archive_history(pid: str):
    security.require_project(pid)
    return [{'id': row['id'], 'created_at': row['created_at'], **db.loads(row['content'], {})} for row in db.rows("SELECT * FROM project_revisions WHERE project_id=? AND kind='reference_archive' ORDER BY created_at DESC", (pid,))]


@router.get('/archives/{sha256}/download')
def download_archive(pid: str, sha256: str):
    security.require_project(pid)
    row = db.one("SELECT content FROM project_revisions WHERE project_id=? AND kind='reference_archive' AND json_extract(content,'$.sha256')=?", (pid, sha256))
    if not row:
        raise HTTPException(404, '文献包不存在于当前项目')
    return FileResponse(security.safe_object(sha256), filename=db.loads(row['content'])['filename'])
