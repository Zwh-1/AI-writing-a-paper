"""Typed content obligations are not a second source of project facts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from ..ai_request_cache import digest

INSTRUCTION=' task_spec声明本章必须回答的内容责任、证据类型和结论强度。逐项解释实际机制和适用条件；检索相关与派生理解不能代替原始证据。缺少实测或必要前提时报告具体缺口，不得写成已经验证的结果。'


class EvidenceRequirement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    any_of: list[str] = Field(default_factory=list)
    measurement_required: bool = False


class Obligation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    obligation_id: str
    question: str
    required: bool = True
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)
    claim_strength_cap: str = 'described'
    acceptance_checks: list[str] = Field(default_factory=lambda: ['source_current', 'claim_support', 'task_coverage'])
    depends_on: list[str] = Field(default_factory=list)
    gap_policy: Literal['report_gap', 'pause_claim'] = 'report_gap'


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['task.spec.v1'] = 'task.spec.v1'
    project_id: str
    section_id: str
    operation: str
    base_revision_id: str | None = None
    obligations: list[Obligation] = Field(default_factory=list)
    source_versions: dict = Field(default_factory=dict)


def compile_spec(pid, section, context, manifest):
    brief = context.get('writing_brief') or {}
    plan = context.get('argument_plan') or {}
    cards = plan.get('paragraph_cards') or []
    by_point = {point: card for card in cards for point in card.get('point_ids', [])}
    checkpoints = {item['point_id']: item for item in brief.get('argument_checkpoints', [])}
    obligations = []
    for point in brief.get('points', []):
        card = by_point.get(point['id'], {})
        checkpoint = checkpoints.get(point['id'], {})
        needs = card.get('evidence_requirement') or checkpoint.get('needs') or []
        # Existing checkpoint semantics treat source/project/code as alternatives.
        requirements = [EvidenceRequirement(any_of=sorted(set(needs)),
                         measurement_required=card.get('strength_cap', checkpoint.get('strength')) in ('measured', 'evaluated'))] if needs else []
        obligations.append(Obligation(obligation_id=point['id'], question=point.get('text', ''),
            required=point.get('required', True), evidence_requirements=requirements,
            claim_strength_cap=card.get('strength_cap', checkpoint.get('strength', 'described')),
            depends_on=card.get('claim_ids', []), gap_policy='report_gap'))
    # The immutable manifest remains authoritative. Never copy raw locators,
    # OCR diagnostics or all source maps into every model-facing obligation.
    from .versions import source_versions
    versions = source_versions(manifest)
    version_fields=('core_version_id','contract_version_id','rule_package_id','target_snapshot_id')
    version_refs={key:manifest[key] for key in version_fields[:4] if manifest.get(key) is not None}
    version_refs['manifest_versions_hash']=digest(versions)
    value = TaskSpec(project_id=pid, section_id=section['id'],
        operation=context.get('operation', 'generate'), base_revision_id=section.get('current_revision_id'),
        obligations=obligations, source_versions=version_refs).model_dump()
    value['hash'] = digest(value)
    return value


def attach(task, section, context, manifest):
    from ..ai_runtime.policy import quality_enabled
    if not quality_enabled(task):
        return
    spec = compile_spec(task['project_id'], section, context, manifest)
    if not spec['obligations']:
        # Native agents already carry a complete section contract. An empty
        # duplicate task book adds tokens without adding a content obligation.
        context.pop('task_spec',None)
        manifest.pop('task_spec',None)
        manifest.pop('task_spec_hash',None);manifest.pop('task_spec_version',None)
        return
    # The writer already receives these exact responsibilities in writing_brief
    # and argument_plan. Keep the shared typed contract server-side, rather
    # than sending a third copy of the same content to the model.
    context.pop('task_spec',None)
    manifest['task_spec'] = spec
    manifest['task_spec_hash'] = spec['hash']
    manifest['task_spec_version'] = spec['schema_version']


def merge_point_reviews(reviews):
    """Keep disagreements sticky, independent of batching and result order."""
    if isinstance(reviews, dict):
        reviews = list(reviews.values())
    merged = {}
    for review in reviews:
        if not isinstance(review, dict) or not isinstance(review.get('point_id'), str):
            continue
        identity = review['point_id']
        previous = merged.get(identity)
        if previous is None:
            merged[identity] = dict(review)
        elif previous.get('status') != review.get('status') or previous.get('disputed'):
            merged[identity] = {**previous, 'status': 'unknown', 'disputed': True,
                'reason': '不同核验批次对任务覆盖存在分歧，需复核实际正文'}
        else:
            indices = [i for row in (previous, review)
                       for i in (row.get('paragraph_indices') if isinstance(row.get('paragraph_indices'), list) else [])]
            merged[identity]['paragraph_indices'] = list(dict.fromkeys(i for i in indices if type(i) is int))
    return merged


def assess(spec, quality, paragraphs, *, context=None, manifest=None):
    """Coverage derives from the existing independent reviewer, never token overlap."""
    semantic = quality.get('semantic') or {}
    coverage = semantic.get('task_coverage') or {}
    reviews = semantic.get('point_reviews') or coverage.get('points') or coverage.get('point_reviews') or []
    by_point = merge_point_reviews(reviews)
    from ..writing_evidence_quality import bound_categories, _asserts_measurement
    pid = spec.get('project_id') if context is not None else None
    categories = {}
    output = []
    dependencies = {}
    for item in spec.get('obligations', []):
        reviewed = by_point.get(item['obligation_id'], {})
        indices=reviewed.get('paragraph_indices',[])
        indices_valid = (isinstance(indices, list) and bool(indices) and
                         all(type(i) is int and 0 <= i < len(paragraphs) for i in indices))
        indices = indices if indices_valid else []
        relevant=[claim for claim in semantic.get('claims',[]) if claim.get('paragraph_index') in indices]
        supported=bool(relevant) and all(claim.get('support')=='supported' and claim.get('quote_verified') for claim in relevant)
        blockers={'result_evidence_level','claim_mapping_invalid','citation_mapping_invalid',
                  'citation_source_invalid','claim_span_invalid','citation_evidence_gap'}
        blocked=any(finding.get('kind') in blockers and finding.get('paragraph_index') in indices
                    for finding in quality.get('findings',[]))
        bound = set()
        for index in indices:
            if index not in categories:
                categories[index] = bound_categories(pid, paragraphs[index], context, manifest)
            bound.update(categories[index])
        requirements = item.get('evidence_requirements') or []
        evidence_ok = all((not requirement.get('any_of') or bool(set(requirement['any_of']) & bound))
                         and (not requirement.get('measurement_required') or 'run_result' in bound)
                         for requirement in requirements)
        measurement_claim = any(_asserts_measurement(paragraphs[i].get('text', '')) for i in indices)
        strength_ok = (not measurement_claim or 'run_result' in bound and
                       item.get('claim_strength_cap') not in ('static', 'designed', 'planned', 'proposed', 'required'))
        checks = {'source_current': quality.get('mapping', {}).get('status') == 'checked' and not blocked,
                  'claim_support': supported,
                  'task_coverage': semantic.get('coverage_complete') is True and reviewed.get('status') == 'complete' and indices_valid,
                  'evidence_requirements': evidence_ok, 'claim_strength_cap': strength_ok}
        checks['acceptance_checks'] = all(checks.get(name) is True for name in item.get('acceptance_checks', []))
        status = 'verified' if all(checks.values()) else 'needs_review'
        output.append({'obligation_id': item['obligation_id'], 'required': item['required'],
                       'question': item.get('question', ''), 'status': status, 'review': reviewed or None,
                       'checks': checks, 'bound_evidence_types': sorted(bound)})
        dependencies[item['obligation_id']] = item.get('depends_on') or []
    by_id = {row['obligation_id']: row for row in output}
    claim_reviews = {claim.get('claim_id'): claim for claim in semantic.get('claims', [])}
    def dependency_ok(identity, active):
        if identity in active:
            return False
        if identity not in by_id:
            claim = claim_reviews.get(identity, {})
            return claim.get('support') == 'supported' and claim.get('quote_verified') is True
        row = by_id[identity]
        return row['status'] == 'verified' and all(dependency_ok(dep, active | {identity}) for dep in dependencies[identity])
    dependency_results = {identity: all(dependency_ok(dep, {identity}) for dep in deps)
                          for identity, deps in dependencies.items()}
    for row in output:
        row['checks']['dependencies'] = dependency_results[row['obligation_id']]
        if not row['checks']['dependencies']:
            row['status'] = 'needs_review'
    required = [row for row in output if row['required']]
    done = sum(row['status']=='verified' for row in required)
    return {'schema': 'obligation.coverage.v1', 'task_spec_hash': spec['hash'],
            'obligations': output, 'required_count': len(required), 'verified_count': done,
            'complete': bool(required) and done == len(required),
            'scope': 'existing_semantic_point_reviews_not_human_approval',
            'paragraph_count': len(paragraphs)}
