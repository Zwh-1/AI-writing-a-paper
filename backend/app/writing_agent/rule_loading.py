from pathlib import Path
from .common import digest

def loaded_rules(root,files):
    names={'AGENTS.md','AI_AGENT_RULES.md','CODEX.md','CLAUDE.md'}
    return [{'path':name,'sha256':digest(raw),'text':raw.decode('utf-8-sig')} for name,raw in files.items() if Path(name).name in names]
