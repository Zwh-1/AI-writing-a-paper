"""Read-only query contract shared with the offline CLI projection."""
from . import db, security

def query(pid, body):
    security.require_project(pid)
    from .config import ROOT
    import importlib.util
    spec=importlib.util.spec_from_file_location('yantai_query_projection',ROOT/'scripts'/'yantai_query_projection.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with db.read_session():
        with db.connect() as connection:return module.query(connection,pid,body)
