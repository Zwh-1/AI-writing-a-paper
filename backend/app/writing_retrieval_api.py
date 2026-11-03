"""Local writing material discovery; never issues a paid model request."""
from fastapi import APIRouter
from . import writing_retrieval as service

router = APIRouter()


@router.post('/projects/{pid}/writing-retrieval/search')
def search(pid: str, body: dict):
    return service.search(pid, body.get('section_id'), body.get('query'),
                          limit=body.get('limit', 8), channels=body.get('channels'),
                          source_refs=body.get('source_refs'), remote=True)


@router.post('/projects/{pid}/writing-retrieval/prepare')
def prepare(pid: str, body: dict):
    return service.prepare(pid, body.get('section_id'), body.get('query'),
                           operation=body.get('operation', 'generate'), limit=body.get('limit', 8),
                           max_chars=body.get('max_chars', 24000), source_refs=body.get('source_refs'),
                           channels=body.get('channels'), remote=True)


@router.post('/projects/{pid}/writing-retrieval/read')
def read(pid: str, body: dict):
    return service.read(pid, body.get('section_id'), body.get('candidates'), body.get('query'),
                        operation=body.get('operation', 'generate'), max_chars=body.get('max_chars', 24000), remote=True)


@router.post('/projects/{pid}/writing-retrieval/validate')
def validate(pid: str, body: dict):
    return {'valid': service.validate(pid, body.get('bundle'), remote=True)}
