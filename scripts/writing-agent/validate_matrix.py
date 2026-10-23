"""Map each V4 behavioral requirement to observed test cases, keeping semantics separate."""
import argparse
import ast
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
MAPPING={
 'T01':['test_transport_chat_tools_keeps_provider_message_state','test_responses_transport_sets_reasoning_and_preserves_function_items','test_model_profile_cannot_override_route_secret_or_budget'],
 'T02':['test_exact_single_request_reconciles_conservative_account_amount','test_comparison_budget_is_shared_by_all_arms','test_mandatory_context_over_budget_stops_without_saving_truncated_input','test_actual_wire_input_is_frozen_with_tool_schema_and_exact_budget','test_scoped_budget_cannot_lose_a_bill_group_anchored_in_another_scope'],
 'T03':['test_invalid_charged_response_is_kept_for_user_review','test_workflow_has_separate_judgment_check_and_no_auto_human','test_complete_invalid_json_gets_one_recorded_repair_without_hidden_loops'],
 'T04':['test_diagnostic_gold_isolation_and_no_fake_scores','test_comparison_keeps_original_inputs_and_all_phase_calls','test_diagnostic_uses_frozen_dataset_after_source_file_changes'],
 'T05':['test_comparison_keeps_original_inputs_and_all_phase_calls','test_position_swap_records_bias_without_granting_quality','test_dimensional_reviews_preserve_disagreement_and_never_promote_machine'],
 'T06':['test_migrations_atomic_and_repeatable','test_version_six_upgrades_without_resetting_rows','test_migration_rollback_keeps_old_version_and_no_partial_tables','test_migration_refuses_future_database'],
 'T07':['test_path_escape_and_cross_project_evidence_rejected','test_project_state_compare_and_swap','test_package_is_immutable_snapshot'],
 'T08':['test_path_escape_and_cross_project_evidence_rejected','test_source_is_frozen_and_secrets_excluded','test_configured_secret_is_blocked_before_context_persistence','test_native_junction_does_not_escape_frozen_scope'],
 'T09':['test_commit_snapshot_is_distinct_from_dirty_working_tree','test_working_scope_addition_is_not_claimed_stable'],
 'T10':['test_source_is_frozen_and_secrets_excluded','test_valid_utf8_original_replacement_characters_are_preserved_with_warning','test_search_does_not_silently_ignore_after_line_2000'],
 'T11':['test_syntax_index_uses_real_typescript_and_literal_routes','test_requested_excerpt_is_not_mislabeled_as_truncated_snapshot','test_import_alias_and_parse_gaps_are_distinct_from_missing_function'],
 'T12':['test_import_alias_and_parse_gaps_are_distinct_from_missing_function','test_optional_missing_results_do_not_force_fabricated_runtime_evidence'],
 'T13':['test_code_cannot_be_promoted_to_execution','test_registered_check_unavailable_never_creates_success_evidence','test_absence_of_execution_proof_is_not_a_pass_claim'],
 'T14':['test_workflow_has_separate_judgment_check_and_no_auto_human','test_human_approval_cannot_bypass_missing_evidence'],
 'T15':['test_project_state_compare_and_swap','test_outline_dependency_cycle_cannot_be_saved'],
 'T16':['test_tool_deny_cache_and_egress_revoke','test_coherence_expands_original_and_adoption_rechecks_new_premise'],
 'T17':['test_registered_check_unavailable_never_creates_success_evidence','test_sandbox_never_falls_back_to_host_when_docker_unavailable'],
 'T18':['test_mandatory_context_over_budget_stops_without_saving_truncated_input','test_no_rules_no_writing_call','test_block_local_only_rule_before_model_call','test_exact_shared_code_preserves_conditions_and_required_canonical_text'],
 'T19':['test_tool_deny_cache_and_egress_revoke','test_derived_material_cannot_bypass_parent_egress','test_configured_secret_is_blocked_before_context_persistence'],
 'T20':['test_saved_response_can_reparse_without_repeating_paid_call','test_paid_draft_local_reparse_preserves_raw_response_and_calls_zero_models','test_recheck_cannot_bypass_unknown_remote_stage','test_cancel_prevents_any_model_request','test_worker_recovery_does_not_resend_sent_request','test_completed_paid_call_is_not_automatically_regenerated_on_recovery'],
 'T21':['test_unknown_remote_outcome_never_retried_implicitly','test_concurrent_requests_do_not_release_unknown_reservation'],
 'T22':['test_old_worker_cannot_reclaim_new_lease','test_workflow_has_separate_judgment_check_and_no_auto_human'],
 'T23':['test_optional_missing_results_do_not_force_fabricated_runtime_evidence','test_unreviewed_outputs_cannot_freeze_calibration_or_qualify'],
 'T24':['test_selected_paragraph_generation_preserves_unselected_content','test_ai_revision_preserves_locked_paragraph_and_table','test_partial_candidate_replaces_original_location'],
 'T25':['test_sentence_positions_chinese_emoji_decimal_and_quote','test_sentence_results_preserve_document_order_not_random_ids','test_scoped_identifier_aliases_preserve_prose_and_roundtrip_only_identity_fields'],
 'T26':['test_sentence_analysis_never_hides_unclassified','test_annotations_batch_output_without_losing_any_sentence','test_nested_sentence_findings_are_validated_and_preserved'],
 'T27':['test_revision_has_independent_old_new_regression','test_human_approval_cannot_bypass_missing_evidence'],
 'T28':['test_coherence_expands_original_and_adoption_rechecks_new_premise','test_expansion_limit_retains_exact_unresolved_scope'],
 'T29':['test_changed_upstream_cannot_be_adopted','test_joint_conflict_never_partially_commits','test_joint_review_unblocks_atomic_mutual_patch_and_rebinds_versions','test_editing_base_text_stops_before_another_paid_stage'],
 'T30':['test_strict_adoption_transaction_and_idempotency','test_adoption_failure_rolls_back_text_mapping_and_receipt','test_legacy_draft_is_idempotent_analyzed_and_preserves_stale_causes','test_legacy_explicit_key_conflict_and_atomic_rollback'],
 'T31':['test_partial_merge_cannot_inherit_strict_review_without_recheck','test_partial_candidate_replaces_original_location'],
 'T32':['test_whole_paper_review_uses_bounded_original_text_chunks','test_indivisible_scope_over_budget_is_explicitly_incomplete','test_manual_whole_paper_review_cannot_apply_to_changed_revisions'],
 'T33':['test_source_change_only_invalidates_tracked_consumers','test_separate_stale_causes_survive_unrelated_strict_adoption','test_manual_stale_resolution_is_scoped_to_current_revision','test_historical_session_does_not_invalidate_replaced_current_dependencies'],
 'T34':['test_revision_has_independent_old_new_regression','test_restore_creates_new_revision'],
 'T35':['test_http_routes_retain_auth_and_conflict_details','test_blind_packet_hides_arm_and_keeps_review_identity','test_dimensional_reviews_preserve_disagreement_and_never_promote_machine','test_frozen_quality_policy_requires_critical_scores_and_stays_assisted'],
 'T36':['test_revision_conflict_does_not_overwrite','test_unknown_remote_outcome_never_retried_implicitly','test_manual_stale_resolution_is_scoped_to_current_revision'],
 'T37':['test_backup_has_new_objects_and_removes_local_root','test_old_sync_explicitly_rejects_new_objects_instead_of_dropping','test_zip_traversal_restore_rejected'],
 'T38':['test_strict_export_requires_current_full_review','test_legacy_strict_is_rejected_in_shared_service'],
}
SEMANTIC={'T05':['D09','D15'],'T11':['real_code_pilot'],'T12':['real_code_pilot'],'T13':['D03'],'T14':['D13'],'T15':['D13'],'T23':['D01','D02','D04','D05'],'T26':['D07'],'T27':['D07','D10'],'T28':['D06','D11'],'T32':['D16'],'T34':['D12','D14']}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--junit',default='docs/writing-agent/completion-final-tests.xml');parser.add_argument('--browser',default='docs/writing-agent/completion-browser-report.json');args=parser.parse_args()
    source={}
    for path in (ROOT/'tests').glob('test_*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node,ast.FunctionDef) and node.name.startswith('test_'):source[node.name]={'file':path.relative_to(ROOT).as_posix(),'line':node.lineno}
    junit=ET.parse(ROOT/args.junit);observed={}
    for case in junit.findall('.//testcase'):
        name=case.get('name','').split('[')[0];observed.setdefault(name,[]).append({'name':case.get('name'),'status':'failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'})
    plan=(ROOT.parent/'论文写作工作台_详细实施方案_V4.0_2026-10-23.md').read_text(encoding='utf-8')
    requirements=dict(re.findall(r'\| (T\d\d) \| (.*?) \|',plan))
    browser_path=ROOT/args.browser;browser=json.loads(browser_path.read_text(encoding='utf-8')) if browser_path.exists() else None
    rows=[]
    for key,names in MAPPING.items():
        checks=[{'name':name,'source':source.get(name),'observed':observed.get(name,[])} for name in names]
        ok=all(c['source'] and c['observed'] and all(x['status']=='passed' for x in c['observed']) for c in checks)
        needs_browser=key in ('T25','T35','T36')
        if needs_browser:ok=ok and bool(browser and browser.get('passed'))
        rows.append({'id':key,'requirement':requirements.get(key),'programmatic_gate':'passed' if ok else 'incomplete','tests':checks,'browser_required':needs_browser,'browser_artifact':args.browser if needs_browser else None,'semantic_followup':SEMANTIC.get(key,[]),'semantic_acceptance':'requires_separate_real_outputs_and_human_or_calibrated_policy' if key in SEMANTIC else 'not_a_model_accuracy_claim'})
    result={'scope':'Behavioral regression mapping, not a proof of semantic correctness','junit':args.junit,'requirements':rows,'count':len(rows),'all_programmatic_gates_passed':len(rows)==38 and all(r['programmatic_gate']=='passed' for r in rows),'human_semantic_acceptance_granted':False}
    target=ROOT/'docs/writing-agent/completion-validation-matrix.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'requirements':len(rows),'programmatic_gates_passed':sum(r['programmatic_gate']=='passed' for r in rows),'incomplete':[r['id'] for r in rows if r['programmatic_gate']!='passed'],'semantic_claim':'not_granted','report':str(target)},ensure_ascii=False))
    raise SystemExit(0 if result['all_programmatic_gates_passed'] else 1)

if __name__=='__main__':main()
