from fastapi import APIRouter
from . import security, db
from . import ai_autopilot as autopilot

router=APIRouter()

@router.get('/projects/{pid}/ai-autopilot')
def status(pid):
    security.require_project(pid)
    return autopilot.report(pid)

@router.post('/projects/{pid}/ai-autopilot')
def start(pid,body:dict):
    return autopilot.start(pid,body)

@router.post('/projects/{pid}/ai-autopilot/repair-exceptions')
def repair_exceptions(pid,body:dict):
    from .exception_repair import start
    return start(pid,body)

@router.put('/projects/{pid}/ai-autopilot')
def configure(pid,body:dict):
    security.require_project(pid)
    config=autopilot.policy(pid)
    if body.get('enabled') and not config.get('budget_limit'):raise ValueError('请先设置预算并启动一次AI托管')
    if 'enabled' in body:config['enabled']=bool(body['enabled'])
    db.set_setting('ai_autopilot_policy:'+pid,config)
    return config
