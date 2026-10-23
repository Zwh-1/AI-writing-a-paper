"""Run with the bundled Python after stopping the workbench. Never prints keys."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps')]
from app import db,model_routing

if __name__=='__main__':
    db.init()
    print(json.dumps(model_routing.migrate_from_summary(sys.argv[1] if len(sys.argv)>1 else ''),ensure_ascii=False))
