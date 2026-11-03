"""Stable transport contracts at the cross-module command boundary."""
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,ValidationError


class ProductRef(BaseModel):
    model_config=ConfigDict(strict=True,extra='allow')
    type:str=Field(min_length=1,max_length=80)
    id:str=Field(min_length=1,max_length=160)
    version:str=Field(min_length=1,max_length=160)
    project_id:str|None=None


class ReuseCommand(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    sources:list[ProductRef]=Field(min_length=1,max_length=20)
    target:Literal['note','asset','draft','figure','material','plan','review']
    section_id:str|None=None
    revision_id:str|None=None
    title:str|None=Field(default=None,max_length=500)


class RelationQuery(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    source:ProductRef
    limit:int=Field(default=60,ge=1,le=200)


class ExecutionCommand(BaseModel):
    model_config=ConfigDict(strict=True,extra='allow')
    code:str=Field(min_length=1,max_length=64000)
    timeout_seconds:int=Field(default=30,ge=1,le=90)
    file_ids:list[str]=Field(default_factory=list,max_length=10)
    product_refs:list[ProductRef]=Field(default_factory=list,max_length=20)
    message_id:str|None=None


def reuse_command(body):
    try:return ReuseCommand.model_validate(body).model_dump(exclude_unset=True)
    except ValidationError as error:
        raise HTTPException(422,{'code':'INVALID_PRODUCT_COMMAND','message':'产物流转的来源、用途或目标参数无效','fields':[{'loc':list(item['loc']),'type':item['type']} for item in error.errors()]}) from error
