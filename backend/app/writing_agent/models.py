from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from ..schemas import ClaimSpan,CitationIntent

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class JudgmentClaim(Strict):
    local_id:str=Field(min_length=1,max_length=80)
    statement:str=Field(min_length=1,max_length=4000)
    evidence_ids:list[str]=Field(default_factory=list,max_length=30)
    conditions:list[str]=Field(default_factory=list,max_length=20)
    not_established:list[str]=Field(default_factory=list,max_length=20)
class Judgment(Strict):
    schema_version:Literal['judgment.v1']='judgment.v1'
    task_goal:str=Field(min_length=1,max_length=6000)
    claims:list[JudgmentClaim]=Field(max_length=16)
    required_explanations:list[str]=Field(default_factory=list,max_length=20)
    missing_evidence:list[str]=Field(default_factory=list,max_length=30)
    blocking_gaps:list[str]=Field(default_factory=list,max_length=30)
class DraftParagraph(Strict):
    local_id:str=Field(min_length=1,max_length=80)
    text:str=Field(min_length=1,max_length=20000)
    reference_ids:list[str]=Field(default_factory=list,max_length=30)
    evidence_ids:list[str]=Field(default_factory=list,max_length=30)
    claim_spans:list[ClaimSpan]=Field(default_factory=list,max_length=100)
    citation_intents:list[CitationIntent]=Field(default_factory=list,max_length=100)
class Draft(Strict):
    paragraphs:list[DraftParagraph]=Field(min_length=1,max_length=40)
    missing_materials:list[str]=Field(default_factory=list,max_length=30)
class Finding(Strict):
    node_id:str=''
    sentence_id:str=''
    severity:Literal['blocker','warning','info']='warning'
    kind:str=Field(max_length=100)
    message:str=Field(min_length=1,max_length=3000)
    evidence_ids:list[str]=Field(default_factory=list,max_length=30)
    rationale:str=Field(default='',max_length=4000)
class Verification(Strict):
    findings:list[Finding]=Field(default_factory=list,max_length=150)
    coverage_complete:bool=False
    summary:str=Field(default='',max_length=4000)
class RepositoryInput(Strict):
    root:str=Field(min_length=1,max_length=2000)
    egress:Literal['local_only','selected_content']='local_only'
    include:list[str]=Field(default_factory=list,max_length=100)
    max_file_bytes:int=Field(default=2097152,ge=1024,le=10485760)
    max_total_bytes:int=Field(default=209715200,ge=1024,le=536870912)
class SessionInput(Strict):
    section_id:str
    operation:Literal['generate_unit','revise_unit']='generate_unit'
    base_revision_id:str|None=None
    expected_project_state_revision:int=Field(ge=0)
    contract_version_id:str
    target_node_ids:list[str]=Field(default_factory=list,max_length=100)
    profile_id:str
    policy:dict=Field(default_factory=dict)

class StartSessionInput(Strict):
    section_id:str
    base_revision_id:str|None=None
    expected_project_state_revision:int=Field(ge=0)
    profile_id:str|None=None
    verification_profile_id:str|None=None
    budget_limit:float|None=Field(default=None,gt=0,strict=True,allow_inf_nan=False)
