"""Offline protocol observations for saved responses; never calls a model or opens a database."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'prompts/writing-quality-scenarios.v1.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def dimensions():
    """Reuse the existing eight dimensions without importing application startup."""
    tree = ast.parse((ROOT / 'backend/app/writing_agent/diagnostic_scoring.py').read_text(encoding='utf-8'))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'DIMENSIONS'
                                                      for target in statement.targets):
            return ast.literal_eval(statement.value)
    raise ValueError('Existing diagnostic dimensions are unavailable')


def at(value, path):
    try:
        for part in path:
            value = value[part]
        return value
    except (KeyError, IndexError, TypeError):
        return None


def observe(check, value):
    actual = at(value, check['path'])
    expected = check.get('value')
    operation = check['operation']
    if operation == 'equals':return actual == expected
    if operation == 'contains':return isinstance(actual, (str, list, dict)) and expected in actual
    if operation == 'absent':return isinstance(actual, (str, list, dict)) and expected not in actual
    raise ValueError('Unknown deterministic check: ' + operation)


def observed_metrics(saved):
    saved=saved or {};usage=saved.get('usage') or {}
    result={key: usage.get(key) if isinstance(usage,dict) and type(usage.get(key)) is int and usage[key]>=0 else None
            for key in ('input_tokens','output_tokens')}
    cost=saved.get('cost')
    result['cost']=cost if (isinstance(cost,dict) and type(cost.get('amount')) in (int,float)
        and math.isfinite(cost['amount']) and cost['amount']>=0 and isinstance(cost.get('currency'),str)
        and cost['currency'].strip()) else None
    duration=saved.get('duration_seconds')
    result['duration_seconds']=duration if type(duration) in (int,float) and math.isfinite(duration) and duration>=0 else None
    result['basis']='supplied_saved_response_observations_not_an_independent_invoice'
    return result


def report(dataset, responses):
    rubric = dimensions()
    known = {case['id'] for case in dataset['cases']}
    if not isinstance(responses, list) or any(not isinstance(row, dict) or row.get('case_id') not in known for row in responses):
        raise ValueError('Every saved response must reference a frozen scenario case_id')
    ids = [row['case_id'] for row in responses]
    if len(ids) != len(set(ids)):raise ValueError('Duplicate scenario responses are not comparable')
    indexed = {row['case_id']: row for row in responses}
    output = []
    for case in dataset['cases']:
        saved = indexed.get(case['id'])
        value = saved.get('response') if saved else None
        observations = [{'name': check['name'], 'passed': observe(check, value) if saved else None,
                         'scope': 'deterministic_contract_only_not_semantic_truth'} for check in case['checks']]
        reviews = saved.get('human_reviews', []) if saved else []
        human = []
        for review in reviews:
            scores = review.get('dimensions') if isinstance(review, dict) else None
            if not isinstance(scores, dict) or set(scores) != set(rubric):
                raise ValueError('Human review requires the existing eight dimensions')
            if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
                raise ValueError('Human review requires an explicit reviewer identity')
            for item in scores.values():
                if not isinstance(item, dict) or item.get('score') is not None and (type(item['score']) is not int or item['score'] not in (0, 1, 2)):
                    raise ValueError('Human scores must be null or 0/1/2')
                if not isinstance(item.get('rationale'), str) or not item['rationale'].strip():
                    raise ValueError('Each human dimension needs its rationale')
            human.append({'dimensions': scores, 'reviewer_record_hash': digest(review['reviewer']),
                          'provenance': 'supplied_offline_review_not_independently_attested'})
        output.append({'case_id': case['id'], 'risk_category': case['risk_category'],
            'response_present': saved is not None, 'response_hash': digest(value) if saved else None,
            'contract_checks': observations, 'dimensions': {key: {'score': None, 'rationale': 'Requires explicit human assessment'} for key in rubric},
            'supplied_human_reviews': human, 'human_approval': False,
            'semantic_status': 'unassessed', 'material_version': digest(case['input']),
            'observed_metrics': observed_metrics(saved)})
    return {'schema': 'writing-quality-benchmark.v1', 'generated_at': datetime.now(timezone.utc).isoformat(),
        'dataset_hash': digest(dataset), 'rubric': rubric, 'cases': output,
        'summary': {'cases': len(output), 'saved_responses': len(responses),
            'contract_checks_passed': sum(item['passed'] is True for row in output for item in row['contract_checks']),
            'contract_checks_failed': sum(item['passed'] is False for row in output for item in row['contract_checks']),
            'semantic_unassessed_cases': len(output), 'external_model_calls': 0},
        'scope': 'Offline observations of frozen synthetic scenarios; protocol checks are not human approval, real-model gains or population accuracy.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=FIXTURES)
    parser.add_argument('--responses', type=Path, help='JSON array of {case_id,response,human_reviews?}')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output and any(args.output.resolve() == source.resolve() or
        args.output.exists() and source.exists() and args.output.samefile(source)
        for source in (args.fixtures, args.responses) if source):
        parser.error('Output must not overwrite the dataset or saved responses')
    dataset = json.loads(args.fixtures.read_text(encoding='utf-8'))
    responses = json.loads(args.responses.read_text(encoding='utf-8')) if args.responses else []
    encoded = json.dumps(report(dataset, responses), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n', encoding='utf-8')
    else:print(encoded)


if __name__ == '__main__':main()
