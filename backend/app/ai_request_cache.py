"""Bounded immutable block reuse and cross-process task-slot singleflight."""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading

from fastapi import HTTPException
from . import db

_BLOCKS = OrderedDict()
_BLOCK_BYTES = 0
_GUARD = threading.RLock()
_SLOTS = {}
MAX_BLOCKS = 128
MAX_BYTES = 32 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def namespace():
    value=str(Path(db.DB_PATH).resolve())
    return value.casefold() if os.name=='nt' else value


def clear_blocks():
    global _BLOCK_BYTES
    with _GUARD:
        _BLOCKS.clear()
        _BLOCK_BYTES = 0


def stable_block(key, builder):
    """Cache only model data; callers still revalidate provenance and permissions."""
    from .ai_cache_policy import settings
    global _BLOCK_BYTES
    if not settings()['stable_block_lru_enabled']:
        return deepcopy(builder()), False
    identity = digest([namespace(), 'prompt-block.v2', key])
    with _GUARD:
        entry = _BLOCKS.get(identity)
        if entry is not None:
            _BLOCKS.move_to_end(identity)
            return deepcopy(entry[0]), True
    value = builder()
    size = len(canonical(value).encode('utf-8'))
    if size > MAX_BYTES or MAX_BLOCKS < 1:
        return deepcopy(value), False
    with _GUARD:
        previous = _BLOCKS.pop(identity, None)
        if previous is not None:
            _BLOCK_BYTES -= previous[1]
        _BLOCKS[identity] = (deepcopy(value), size)
        _BLOCK_BYTES += size
        while _BLOCKS and (len(_BLOCKS) > MAX_BLOCKS or _BLOCK_BYTES > MAX_BYTES):
            _, removed = _BLOCKS.popitem(last=False)
            _BLOCK_BYTES -= removed[1]
    return deepcopy(value), False


def slot_key(task, manifest, stage, index):
    return digest([namespace(), task['project_id'], task['id'], manifest.get('section_id'), stage, index])


@contextmanager
def singleflight(task, manifest, stage, index, timeout=615):
    # One slot has one shared FileLock in this process, even with many threads.
    # A content/attempt/layout change must not create a different lock.
    from filelock import FileLock, Timeout
    from .ai_cache_policy import settings
    if not settings()['writing_singleflight_enabled']:
        yield
        return
    identity = slot_key(task, manifest, stage, index)
    folder = Path(db.DB_PATH).resolve().parent / 'tmp' / 'ai-request-locks'
    folder.mkdir(parents=True, exist_ok=True)
    with _GUARD:
        entry = _SLOTS.setdefault(identity, [FileLock(str(folder / (identity + '.lock'))), 0])
        entry[1] += 1
    try:
        try:
            with entry[0].acquire(timeout=max(0, float(timeout))):
                yield
        except Timeout as error:
            raise HTTPException(409, {'code': 'AI_REQUEST_IN_PROGRESS',
                                     'message': '相同任务阶段仍在处理，未重复发送请求'}) from error
    finally:
        with _GUARD:
            entry[1] -= 1
            if entry[1] == 0:
                _SLOTS.pop(identity, None)


def semantic_key(task, system, context, versions, stage, index):
    payload = db.loads(task.get('payload'), {})
    return digest({'schema': 'writing-recovery.v2', 'database': namespace(),
        'project': task['project_id'], 'task': task['id'], 'section': versions.get('section_id'),
        'stage': stage, 'index': index, 'layout': payload.get('prompt_layout_version', 'legacy'),
        'model_view': payload.get('model_view_version', 'legacy'),
        'intent': payload.get('generation_intent_revision', 0),
        'system': system, 'context': context, 'versions': versions})


def wire_key(task, system, user, manifest, cfg, credential_hash, images=None, wire_request_hash=None):
    return digest({'schema': 'ai-wire.v2', 'database': namespace(), 'project': task['project_id'],
        'task': task['id'], 'stage': manifest.get('writing_stage', manifest.get('stage')),
        'index': manifest.get('writing_unit_index'), 'layout': manifest.get('prompt_layout_version', 'legacy'),
        'endpoint': {key: cfg.get(key) for key in ('base_url', 'protocol', 'model', 'provider_endpoint_id')},
        'credential': credential_hash, 'system': system, 'user': user,
        'wire_request_hash':wire_request_hash,
        'images': [hashlib.sha256(canonical(image).encode()).hexdigest() for image in images or []],
        'options': {key: cfg.get(key) for key in ('max_output_tokens', 'temperature', 'top_p', 'seed',
                                                'thinking_enabled', 'reasoning_effort', 'json_mode',
                                                'streaming','agent_transport')}})
