"""Read-only account/model statistics; never print credentials or invoke generation."""
import json,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import provider,model_routing,security


def main():
    for label,cfg,profile in [('primary',provider.config(),''),('gpt',model_routing.config(),'gpt-fallback')]:
        key=security.get_key(profile)
        if not key:print(json.dumps({'profile':label,'configured':False}));continue
        with provider.request_scope(cfg,key):
            try:
                with provider.client({**cfg,'timeout_seconds':15}) as client:
                    response=client.get(provider.usage_endpoint(cfg['base_url']),headers=provider.headers('openai'))
                if response.status_code!=200:print(json.dumps({'profile':label,'http':response.status_code}));continue
                raw=response.json();data=raw.get('data',raw)
                fields=('model','requests','input_tokens','output_tokens','cost','actual_cost','account_cost','input_price','output_price','unit','currency')
                stats=[{k:s[k] for k in fields if k in s} for s in data.get('model_stats',[]) if isinstance(s,dict) and s.get('model')==cfg['model']]
                print(json.dumps({'profile':label,'http':response.status_code,'root_fields':list(data),'model_stats':stats,'normalized':provider.normalize_account_usage(raw)},ensure_ascii=False))
            except Exception as error:print(json.dumps({'profile':label,'error_type':type(error).__name__,'error':provider.redact(error)},ensure_ascii=False))


if __name__=='__main__':main()
