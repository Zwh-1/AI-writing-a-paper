"""Generate a reproducible source-only entry audit; never opens user data."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps')]
from app.ai_runtime.inventory import scan


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=scan(ROOT/'backend/app')
    report['baseline']={'semantic_quality':'requires_human_labels_and_budgeted_trials',
                        'performance':'requires_representative_workloads',
                        'requests_sent':0,'user_database_opened':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'call_sites':len(report['call_sites']),'entries':len(report['entries']),'requests_sent':0}))


if __name__=='__main__':main()
