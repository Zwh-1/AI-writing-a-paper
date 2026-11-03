"""Project, activity and egress checks at all content read boundaries."""
from fastapi import HTTPException
from . import db,security

def require_file(pid,fid,remote=False):
    security.require_project(pid)
    row=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(fid,pid))
    if not row:raise HTTPException(404,'原件不属于当前项目')
    if not row['active']:raise HTTPException(409,'原件已停用')
    if remote and not security.file_allows_remote(fid,pid):raise HTTPException(403,'原件当前不允许发送到远端服务')
    return row
