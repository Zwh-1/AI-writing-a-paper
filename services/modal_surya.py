"""Deploy: modal deploy services/modal_surya.py. Requires a Modal account."""
import os
from pathlib import Path
import modal
ROOT=Path(__file__).resolve().parent
image=(modal.Image.debian_slim(python_version='3.12').pip_install('surya-ocr==0.17.1','fastapi','python-multipart','uvicorn').env({'SURYA_DATA_ROOT':'/data/surya','HF_HOME':'/data/model-cache'}).add_local_file(str(ROOT/'surya_service.py'),'/app/surya_service.py'))
app=modal.App('yantai-surya');volume=modal.Volume.from_name('yantai-surya-data',create_if_missing=True)
def service_module():
    import sys
    sys.path.insert(0,'/app')
    import surya_service
    return surya_service

@app.function(image=image,gpu=os.environ.get('SURYA_MODAL_GPU','L4'),timeout=3600,volumes={'/data':volume},max_containers=1)
def process_job(identity):
    volume.reload();service_module().process(identity);volume.commit()

@app.function(image=image,timeout=600,volumes={'/data':volume},max_containers=1,secrets=[modal.Secret.from_name('yantai-surya-auth')])
@modal.asgi_app()
def web():
    service=service_module()
    class Dispatcher:
        def submit(self,ignored,identity):
            volume.commit();return process_job.spawn(identity)
    service.pool=Dispatcher()
    @service.app.middleware('http')
    async def refresh(request,call_next):
        if request.method=='GET':volume.reload()
        return await call_next(request)
    return service.app
