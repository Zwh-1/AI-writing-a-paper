from pydantic import BaseModel,Field,ConfigDict
class CandidateParagraph(BaseModel):
    model_config=ConfigDict(extra='ignore')
    text:str=Field(default='',max_length=200000)
    source_ids:list[str]=Field(default_factory=list,max_length=60)
    reference_ids:list[str]=Field(default_factory=list,max_length=60)
    fact_ids:list[str]=Field(default_factory=list,max_length=60)
    preserve_node_id:str|None=None
class SectionCandidate(BaseModel):
    model_config=ConfigDict(extra='ignore')
    paragraphs:list[CandidateParagraph]=Field(min_length=1,max_length=600)
    missing_materials:list[str]=Field(default_factory=list,max_length=100)
    notes:str=''
class OutlineSection(BaseModel):
    title:str=Field(min_length=1,max_length=300)
    kind:str='body'
    goal:str=Field(default='',max_length=6000)
    target_words:int=Field(default=1500,ge=50,le=100000)
class OutlineCandidate(BaseModel):
    sections:list[OutlineSection]=Field(min_length=1,max_length=100)
    missing_materials:list[str]=Field(default_factory=list,max_length=100)
