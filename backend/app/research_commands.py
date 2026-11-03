"""Validated research commands used by every HTTP compatibility entry."""
from fastapi import HTTPException
from . import documents,research_records as store,assistant_service as ai,reading_service as reading

def create_record(pid,body):
    kind=body.get('kind');data=body.get('data',{})
    if not isinstance(data,dict):raise HTTPException(422,'记录内容必须为对象')
    if kind not in ('note','agent','conversation','canvas'):raise HTTPException(422,'请使用该功能的专用操作入口')
    if kind=='agent':data=ai.validate_agent(data)
    if kind=='canvas':reading.validate_canvas(data.get('scene',{}))
    if kind=='note' and data.get('document'):
        normalized=documents.normalize(data['document']);data={**data,'document':normalized,'text':documents.plain(normalized)}
    if kind=='conversation':
        if data.get('agent_id','academic') not in {agent['id'] for agent in ai.agents(pid)}:raise HTTPException(422,'所选智能体不存在')
        for fid in data.get('file_ids',[]):store.require_file(pid,fid,True)
    if not isinstance(data.get('title',''),str) or len(data.get('title',''))>1000:raise HTTPException(422,'标题无效')
    return store.save(pid,kind,data,owner=str(body.get('owner_id','')))

def update_record(pid,identity,body):
    current=store.get(pid,identity);data=body.get('data',{})
    if not isinstance(data,dict):raise HTTPException(422,'记录内容必须为对象')
    if current['kind'] not in ('note','agent','conversation','canvas','annotation','comment'):raise HTTPException(422,'该记录不能直接修改')
    if current['kind']=='agent':data=ai.validate_agent(data)
    if current['kind']=='canvas':reading.validate_canvas(data.get('scene',{}))
    if current['kind']=='note' and data.get('document'):
        normalized=documents.normalize(data['document']);data={**data,'document':normalized,'text':documents.plain(normalized)}
    if current['kind']=='annotation':data={**current['data'],'note':str(data.get('note',''))[:12000]}
    if current['kind']=='comment':data={**current['data'],'resolved':data.get('resolved') is True}
    return store.save(pid,current['kind'],data,identity,current['owner_id'],body.get('revision'))
