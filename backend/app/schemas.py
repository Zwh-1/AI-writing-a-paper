from typing import Literal
from pydantic import BaseModel,Field,ConfigDict

class ClaimSpan(BaseModel):
    """A proposed claim, located by exact Unicode text rather than model inference."""
    model_config=ConfigDict(extra='forbid')
    local_id:str=Field(min_length=1,max_length=80)
    text:str=Field(min_length=1,max_length=20000)
    start_cp:int|None=Field(default=None,ge=0)
    end_cp:int|None=Field(default=None,ge=0)
    text_hash:str|None=Field(default=None,max_length=64)
    role:Literal['fact','inference','purpose','transition']='fact'
    evidence_ids:list[str]=Field(default_factory=list,max_length=60)
    source_ids:list[str]=Field(default_factory=list,max_length=60)
    knowledge_unit_ids:list[str]=Field(default_factory=list,max_length=60)
    document_reading_ids:list[str]=Field(default_factory=list,max_length=30)
    fact_ids:list[str]=Field(default_factory=list,max_length=60)

class CitationIntent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    claim_id:str=Field(min_length=1,max_length=80)
    reference_id:str=Field(min_length=1,max_length=100)
    evidence_ids:list[str]=Field(default_factory=list,max_length=60)
    source_ids:list[str]=Field(default_factory=list,max_length=60)
    knowledge_unit_ids:list[str]=Field(default_factory=list,max_length=60)
    document_reading_ids:list[str]=Field(default_factory=list,max_length=30)
    quote:str=Field(default='',max_length=20000)
    source_version:str|None=Field(default=None,max_length=100)
    source_versions:dict[str,str]=Field(default_factory=dict,max_length=60)
    kind:Literal['paraphrase','quote']='paraphrase'

class CandidateParagraph(BaseModel):
    model_config=ConfigDict(extra='ignore')
    text:str=Field(default='',max_length=200000)
    source_ids:list[str]=Field(default_factory=list,max_length=60)
    reference_ids:list[str]=Field(default_factory=list,max_length=60)
    fact_ids:list[str]=Field(default_factory=list,max_length=60)
    evidence_ids:list[str]=Field(default_factory=list,max_length=60)
    visual_ids:list[str]=Field(default_factory=list,max_length=20)
    knowledge_unit_ids:list[str]=Field(default_factory=list,max_length=60)
    document_reading_ids:list[str]=Field(default_factory=list,max_length=30)
    claim_version_ids:list[str]=Field(default_factory=list,max_length=60)
    brief_points:list[str]=Field(default_factory=list,max_length=20)
    claim_spans:list[ClaimSpan]=Field(default_factory=list,max_length=100)
    citation_intents:list[CitationIntent]=Field(default_factory=list,max_length=100)
    preserve_node_id:str|None=None
class SectionCandidate(BaseModel):
    model_config=ConfigDict(extra='ignore')
    paragraphs:list[CandidateParagraph]=Field(min_length=1,max_length=600)
    missing_materials:list[str]=Field(default_factory=list,max_length=100)
    notes:str=''
    visuals:list[dict]=Field(default_factory=list,max_length=20)
    retrieval_requests:list[dict]=Field(default_factory=list,max_length=8)
    unresolved_claims:list[str]=Field(default_factory=list,max_length=100)
class OutlineSection(BaseModel):
    title:str=Field(min_length=1,max_length=300)
    kind:str='body'
    goal:str=Field(default='',max_length=6000)
    target_words:int=Field(default=1500,ge=50,le=100000)
class OutlineCandidate(BaseModel):
    sections:list[OutlineSection]=Field(min_length=1,max_length=100)
    missing_materials:list[str]=Field(default_factory=list,max_length=100)
