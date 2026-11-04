"""Read-only requested/actual provider identity; old pool destinations stay unknown."""
from copy import deepcopy

from . import db

FIELDS=('base_url','model','protocol','credential_hash','currency')


def _object(value):
    if isinstance(value,dict):return value
    parsed=db.loads(value,{}) if isinstance(value,str) else {}
    return parsed if isinstance(parsed,dict) else {}


def _text(value):
    return value if isinstance(value,str) and value.strip() else None


def effective(run):
    """Project identity without rewriting historical requested fields.

    An explicit but incomplete actual snapshot is incomplete evidence. Fields
    are never filled from the requested provider merely to make it look known.
    """
    row=dict(run);meta=_object(row.get('manifest'));requested_cfg=_object(meta.get('provider'))
    requested={name:_text(requested_cfg.get(name)) for name in FIELDS}
    requested['model']=requested['model'] or _text(row.get('model'))
    requested['protocol']=requested['protocol'] or _text(row.get('protocol'))
    requested['credential_hash']=_text(meta.get('credential_hash')) or requested['credential_hash']
    actual_cfg=_object(meta.get('actual_provider'))
    if not actual_cfg:actual_cfg=_object(meta.get('provider_snapshot'))
    present=any(name in meta for name in ('actual_provider','provider_snapshot','actual_provider_credential_hash'))
    actual={name:_text(actual_cfg.get(name)) for name in FIELDS}
    actual['credential_hash']=_text(meta.get('actual_provider_credential_hash')) or actual['credential_hash']
    if present:
        selected=actual;source='actual'
    elif meta.get('pool_used')=='pool' or meta.get('pool_endpoint'):
        selected={name:None for name in FIELDS};source='unknown_pool'
    else:
        selected=requested;source='legacy_requested'
    return {'requested':requested,'actual':actual,'effective':dict(selected),'source':source,
            'actual_known':present and all(actual[name] is not None for name in ('base_url','model','protocol','credential_hash'))}


def config_for(run):
    """Return only configuration whose identity is justified by this run."""
    row=dict(run);meta=_object(row.get('manifest'));identity=effective(row)
    if identity['source']=='actual':
        configuration=_object(meta.get('actual_provider'))
        if not configuration:configuration=_object(meta.get('provider_snapshot'))
    elif identity['source']=='legacy_requested':configuration=_object(meta.get('provider'))
    else:configuration={}
    return {**deepcopy(configuration),**identity['effective']}


def project(run):
    """Compatibility row for consumers that group by effective model/protocol."""
    row=dict(run);identity=effective(row)
    return {**row,'model':identity['effective']['model'],'protocol':identity['effective']['protocol'],
            'provider_identity':identity}


def base(value):
    return str(value or '').rstrip('/').removesuffix('/v1')


def matches(run,configuration,credential_hash=None):
    identity=effective(run)['effective']
    if not identity['base_url'] or not identity['model'] or not identity['protocol']:return False
    if (base(identity['base_url'])!=base(configuration.get('base_url'))
            or identity['model']!=configuration.get('model')
            or identity['protocol']!=configuration.get('protocol')):return False
    return credential_hash is None or identity['credential_hash']==credential_hash
