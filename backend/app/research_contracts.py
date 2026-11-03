"""Version 1 research DTOs. Unknown provider fields remain forward compatible."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from fastapi import HTTPException

class Content(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    title: str = ''
    text: str = ''
    source_refs: list[dict[str, Any]] = Field(default_factory=list)

class Note(Content):
    tags: list[str] = Field(default_factory=list)
    section_id: str | None = None
    message_id: str | None = None
    annotation_id: str | None = None
    use_for_writing: bool = False

class Agent(Content):
    prompt: str = ''
    capabilities: list[Literal['knowledge','assets','document','document_edit','notes']] = Field(default_factory=list)
    model_profile: str = 'primary'

class Conversation(Content):
    agent_id: str = 'academic'
    file_ids: list[str] = Field(default_factory=list)

class Message(Content):
    role: Literal['user','assistant']
    citations: list[dict[str, Any]] = Field(default_factory=list)

class Translation(Content):
    language: str = 'zh-CN'
    parse_id: str | None = None
    unit_id: str | None = None
    source_hash: str | None = None

class Annotation(Content):
    unit_id: str
    parse_id: str
    source_hash: str
    start: int
    end: int
    quote: str
    note: str = ''

class Comment(Content):
    parent_id: str | None = None
    resolved: bool = False
    revision_id: str
    node_id: str
    quote: str

class Canvas(Content):
    scene: dict[str, Any]

class Webpage(Content):
    html: str
    parse_id: str

class ParseResult(Content):
    provider: str
    status: str

class Proposal(Content):
    base_revision_id: str | None
    operations: list[dict[str, Any]]

class Guide(Content):
    summary: str
    keypoints: list[dict[str, Any]] = Field(default_factory=list)

class Search(Content):
    items: list[dict[str, Any]] = Field(default_factory=list)

class Deletion(Content):
    items: list[dict[str, Any]] = Field(default_factory=list)

MODELS = {'note':Note,'agent':Agent,'conversation':Conversation,'message':Message,'translation':Translation,
    'annotation':Annotation,'comment':Comment,'canvas':Canvas,'webpage':Webpage,'parse_result':ParseResult,
    'search':Search,'deletion':Deletion,'proposal':Proposal,'guide':Guide}

def validate_record(kind, data):
    if kind not in MODELS or not isinstance(data,dict):raise HTTPException(422,'研究记录类型或内容无效')
    try:return MODELS[kind].model_validate(data).model_dump(exclude_unset=True)
    except ValidationError as error:raise HTTPException(422,{'code':'INVALID_RESEARCH_RECORD','message':'研究记录字段类型无效','fields':[{'loc':list(e['loc']),'type':e['type']} for e in error.errors()]}) from error
