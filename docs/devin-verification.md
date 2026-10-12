# Devin adapter verification

## Scope

Explicit captured-output filter, user-triggered Devin CLI skill, POSIX installer and numeric local output estimates. No automatic hooks, command executor, native-session collector, provider tokens, quality, dollars or ACU accounting.

No live Devin workspace available: the adapter has not been run against a live Devin instance. Genuine CLI discovery and local subprocess tests do not establish model adherence or delivery. Free account signup exposed no numerical included quota; no model session was started.

## Reproducible focused checks

```bash
DEVIN_TEST_BINARY=/absolute/path/to/devin python -m pytest tests/test_devin*.py -q
python -m pytest tests/test_release_installable.py tests/test_install_skip_verify_warning.py tests/test_cowork_committed_plugin.py tests/test_launchd_no_churn.py -q
cd docs-site && npm ci && npm run build
```

On the final reviewed implementation, the focused suite ran 153 tests with no failures on CPython 3.12.14. It includes 48 separate local filter subprocess cases, real install/refresh/uninstall, private capture and numeric-state modes, strict enum routing, raw fallback, recovery mutation, retained-writer/prefix-seal limits, parent symlink swaps, cross-capture mixing, hostile HOME command execution, malformed manifest refusal, installed dependency drift, sparse template materialization, killed fresh/refresh install recovery and actual sparse checksum verification. These are local subprocess cases, not 48 live Devin model sessions.

The opt-in discovery check used genuine downloaded Devin CLI 3000.11.3 (9c803229faa4), asserting list metadata `/token-optimizer-output [user]`, selected installed directory and `Triggers: user`, without starting a model session. CI without `DEVIN_TEST_BINARY` skips this one integration test.

Release/install regression selection: 37 passed. Additional Codex command regressions: 58 passed, 3 skipped. Docs: 74 pages built. Desktop platform page and mobile install page were visually inspected from the local static build. No claim that the new docs are live on the public site.

## Repository-wide manifest

The collected suite was split by whole test file into eight balanced batches. The exact file lists are included below; each file ran once in the repository-wide accounting. Tests added later are additionally covered by the focused suite.

| Batch | Python | Passed | Skipped | Failures |
|---|---|---:|---:|---|
|0|3.12|893|24|0|
|1|3.10|702|0|0|
|2|3.10|803|9|0|
|3|3.12|575|2|0|
|4|3.10|633|5|0|
|5|3.12|585|1|0|
|6|3.12|790|7|9 identical upstream failures|
|7|3.10|988|0|0|

Batch 6 reproduced the exact same nine failures and 790 passed/7 skipped on pristine upstream main 58b80bcbd112a014c76846313d7719b2e70093a7. The three affected files ran alone on both upstream and feature with 26 passed. This is pre-existing batch-order contamination, not a clean full-suite result. The failures are recorded rather than hidden:

- `test_daemon_reconcile_protects_live.py::test_setup_daemon_uninstall_still_removes_plist`
- `test_dashboard_data.py::test_sidechain_marker_is_first_prompt_only_and_nested_paths_are_explicit`
- `test_weekly_full_savings.py::test_collector_refreshes_growing_session_without_duplicating`
- `test_weekly_full_savings.py::test_window_parser_excludes_outside_activity_and_dedups_streaming`
- `test_weekly_full_savings.py::test_activity_window_uses_grown_old_session_and_includes_child`
- `test_weekly_full_savings.py::test_child_growth_refresh_does_not_double_count_cached_parent`
- `test_weekly_full_savings.py::test_new_uncollected_activity_is_included`
- `test_weekly_full_savings.py::test_child_activity_after_parent_stops_is_still_charged`
- `test_weekly_full_savings.py::test_older_collector_cannot_overwrite_newer_row`

Python 3.10's missing stdlib tomllib caused additional baseline failures in initial runs; affected batches were rerun on 3.12. Mirror drift during active development was fixed by normal generators, not by changing parity tests.

### Batch 0

```text
tests/test_175gaunt_correctness.py
tests/test_antigravity_install_atomicity.py
tests/test_antigravity_session.py
tests/test_autocompact_pct_override_kept.py
tests/test_bundled_prices.py
tests/test_checkpoint_skip_notice.py
tests/test_cleanup_surgical_removal.py
tests/test_codex_command_compress.py
tests/test_codex_uninstall_residue.py
tests/test_command_text_redaction.py
tests/test_context_window_contradiction.py
tests/test_counted_cumulative.py
tests/test_credential_patterns.py
tests/test_cursor_safe_python.py
tests/test_daemon_restart_reap.py
tests/test_dashboard_live_runway.py
tests/test_dashboard_user_flag.py
tests/test_devin_install.py
tests/test_ensure_health_daemon_revive_first.py
tests/test_fresh_nudge_once.py
tests/test_hermes_fleet.py
tests/test_hook_launcher_failopen_guard.py
tests/test_hooks_root_no_window.py
tests/test_launchd_no_churn.py
tests/test_luna_counted_coverage.py
tests/test_openai_gpt56_pricing.py
tests/test_pipeline_analyzer.py
tests/test_post_flush_extensions.py
tests/test_py_trust_shared_gate.py
tests/test_quality_cache_gate.py
tests/test_read_cache_clear_compacted.py
tests/test_redaction_boundary_guards.py
tests/test_replay_benchmark.py
tests/test_routing_advisor.py
tests/test_runway_meter_freshness.py
tests/test_session_log_platform.py
tests/test_sessionstart_runner.py
tests/test_status_bar_compact_window.py
tests/test_statusline_wrap.py
tests/test_stuck_session_compact_restore_budget.py
tests/test_usage_recommendations.py
tests/test_windows_detached_child_e2e.py
tests/test_zz_worktree_immutable.py
```

### Batch 1

```text
tests/test_175gaunt_robustness.py
tests/test_antigravity_install_sh.py
tests/test_antigravity_state.py
tests/test_baseline_safety.py
tests/test_cache_instability_surfaces.py
tests/test_child_stdio_fallbacks.py
tests/test_cleanup_write_and_confirm.py
tests/test_codex_continue_to.py
tests/test_codex_updated_tool_output_limitation.py
tests/test_compact_advice.py
tests/test_context_window_disclosure.py
tests/test_cowork_adapter.py
tests/test_credential_patterns_url_hardening.py
tests/test_cursor_session.py
tests/test_daemon_snapshot_isolation.py
tests/test_dashboard_populations.py
tests/test_dashboard_version_guard.py
tests/test_devin_state.py
tests/test_ensure_health_daemon_skew.py
tests/test_git_context_no_window.py
tests/test_hermes_rollup_occupancy_bridge.py
tests/test_hook_module_dispatch.py
tests/test_host_safety_guard.py
tests/test_lease_denial_logging.py
tests/test_mcp_counter.py
tests/test_openclaw_dist_freshness.py
tests/test_pipeline_dedup_dashboard_buckets.py
tests/test_posttooluse_bash_compress_hook.py
tests/test_python_launcher_cache.py
tests/test_quality_cache_lock_bootstrap.py
tests/test_read_cache_first_read_retarget.py
tests/test_redaction_speed.py
tests/test_resolved_cli_hints.py
tests/test_runtime_env_claudecode.py
tests/test_runway_resume_lean_addback.py
tests/test_session_store_fts.py
tests/test_settings_never_clobbered.py
tests/test_statusline_claude_config_dir.py
tests/test_stdout_error_patterns_perf.py
tests/test_subagent_cache_ttl.py
tests/test_userpromptsubmit_runner.py
tests/test_windows_fchmod_guard.py
```

### Batch 2

```text
tests/test_175gaunt_security.py
tests/test_antigravity_isolation.py
tests/test_archive_cap_redaction.py
tests/test_baseline_savings.py
tests/test_card_harness_parity.py
tests/test_claude_config_dir.py
tests/test_cli_password_redaction.py
tests/test_codex_full_features.py
tests/test_codex_windows_command.py
tests/test_compaction_timing_eval.py
tests/test_context_window_for_model_str.py
tests/test_cowork_collector.py
tests/test_credential_redaction_gating.py
tests/test_cursor_state.py
tests/test_daemon_sweep_hardening.py
tests/test_dashboard_regen_reject_log.py
tests/test_dashboard_version_selfheal.py
tests/test_docs_claims.py
tests/test_ensure_health_quality_cache.py
tests/test_golden_user.py
tests/test_hermes_rollup_occupancy_update.py
tests/test_hook_never_blocks_cross_harness.py
tests/test_hotpath_runtime_cache.py
tests/test_lease_reclaim_released.py
tests/test_measure_no_window.py
tests/test_opener_injection.py
tests/test_placeholder_interior_redaction.py
tests/test_posttooluse_runner.py
tests/test_python_launcher_cache_hit_processes.py
tests/test_quality_cache_marker_check_only.py
tests/test_read_cache_marker_key_redaction.py
tests/test_redaction_vectors.py
tests/test_respond_to_bash_detector.py
tests/test_runtime_env_cursor.py
tests/test_safe_int_ro_connect_parity.py
tests/test_session_store_fts_mirror.py
tests/test_settings_self_heal.py
tests/test_statusline_compact_window.py
tests/test_stop_lock_ownership.py
tests/test_thrash_guard.py
tests/test_utf8_locale.py
tests/test_windows_hook_launcher.py
```

### Batch 3

```text
tests/test_agent_scan_dirs.py
tests/test_antigravity_measure_dispatch.py
tests/test_archive_default_exempt_octocode.py
tests/test_bash_compress_self_check_failopen.py
tests/test_check_regression_guards_unreadable.py
tests/test_claude_md_aggregation.py
tests/test_coach_codex_integrity.py
tests/test_codex_goal_scoping.py
tests/test_cold_resume_safety.py
tests/test_compression_event_redaction.py
tests/test_continuity_scoping.py
tests/test_cowork_committed_plugin.py
tests/test_crossturn_dedup.py
tests/test_custom_redaction_patterns.py
tests/test_daemon_threading.py
tests/test_dashboard_regen_retry.py
tests/test_dashboard_window_align.py
tests/test_double_fire_read_cache.py
tests/test_exit_cleanup.py
tests/test_grok_adapter.py
tests/test_hermes_session_occupancy.py
tests/test_hook_pipe_eof_on_parent_kill.py
tests/test_i18n_continuity.py
tests/test_leaselock_acquire_free_retry.py
tests/test_media_and_live_state_guards.py
tests/test_orphan_plugin_identities.py
tests/test_plain_46_window_promotion.py
tests/test_powershell_matchers.py
tests/test_python_launcher_exec_state.py
tests/test_quality_cache_release_on_timeout.py
tests/test_readme_integrity.py
tests/test_redundant_read_failopen.py
tests/test_resume_lean_savings.py
tests/test_runtime_env_opencode.py
tests/test_savings_card_young_install.py
tests/test_sessionend_defer_default.py
tests/test_settings_write_safety.py
tests/test_statusline_no_window.py
tests/test_stop_runner.py
tests/test_tool_search_diagnostic.py
tests/test_verbosity_calibration.py
tests/test_windows_process_enum.py
```

### Batch 4

```text
tests/test_antigravity_docs.py
tests/test_antigravity_native_windows.py
tests/test_archive_preview_clusters.py
tests/test_bash_compress_self_validation.py
tests/test_checkpoint_credential_redaction.py
tests/test_claude_md_recommendations.py
tests/test_coach_codex_torture.py
tests/test_codex_hook_path.py
tests/test_collect_dashboard_dispatch_bounded.py
tests/test_concurrency_perf_hardening.py
tests/test_copilot_capability_persistence.py
tests/test_cowork_doctor_url.py
tests/test_cursor_doctor.py
tests/test_daemon_measure_py_resolution.py
tests/test_daemon_uninstall_identity_sweep.py
tests/test_dashboard_selfheal_bugB.py
tests/test_delta_negative_savings_guard.py
tests/test_double_hook_registration.py
tests/test_expand_hint_copysafe.py
tests/test_harness_gaps.py
tests/test_hermes_state_wal.py
tests/test_hook_robustness_regressions.py
tests/test_install_skip_verify_warning.py
tests/test_leaselock_cohort_throttle.py
tests/test_module_runner_guard.py
tests/test_orphan_reclaim_atomicity.py
tests/test_platform_savings_isolation.py
tests/test_pricing_coverage.py
tests/test_python_launcher_probe.py
tests/test_quality_fill_host_sanity.py
tests/test_reclaim_sweep_serialization.py
tests/test_refetch_live_state_tool_segment.py
tests/test_retention_contract.py
tests/test_runtime_env_wsl_mnt.py
tests/test_savings_model_mix_reprice.py
tests/test_sessionend_fossil_reconcile.py
tests/test_setup_hook_uninstall.py
tests/test_statusline_quality_cache_dir.py
tests/test_structure_map_ts_coverage.py
tests/test_transformation_card_fix.py
tests/test_verbosity_diet_guard.py
tests/test_windows_session_identity.py
```

### Batch 5

```text
tests/test_antigravity_doctor.py
tests/test_antigravity_proto.py
tests/test_archive_roundtrip_ids.py
tests/test_bash_hook_worktree_skip.py
tests/test_checkpoint_label_format_guard.py
tests/test_cleanup_command.py
tests/test_coach_gaunt_injection.py
tests/test_codex_integrity.py
tests/test_command_filters_safety.py
tests/test_concurrency_throttle_claims.py
tests/test_copilot_home_env_collision.py
tests/test_cowork_hardening.py
tests/test_cursor_hook_bridge.py
tests/test_daemon_midsession_pulse.py
tests/test_daemon_uninstall_tombstone_persists.py
tests/test_dashboard_selfheal_on_timeout.py
tests/test_desktop_plugin_packaging.py
tests/test_duplicate_asset_sync.py
tests/test_expand_search.py
tests/test_heal_hardening.py
tests/test_hint_interpreter.py
tests/test_hook_runtime_parity.py
tests/test_keepwarm_plist_heal.py
tests/test_live_fill_crosscheck.py
tests/test_never_cut_own_skills.py
tests/test_osrc_sidechain_scope.py
tests/test_plugin_data_identity.py
tests/test_project_scoped_skills.py
tests/test_python_launcher_set_e_safety.py
tests/test_quality_filter_audit.py
tests/test_recovery_output.py
tests/test_reject_log_throttle.py
tests/test_robustness_fixes.py
tests/test_runtime_override_unknown.py
tests/test_savings_render.py
tests/test_sessionend_shape_parity.py
tests/test_skill_usage_slash_commands.py
tests/test_statusline_self_disable.py
tests/test_structure_map_ts_methods.py
tests/test_transformation_undercount.py
tests/test_version_constant_tracks_manifest.py
tests/test_windows_spawn_no_window.py
```

### Batch 6

```text
tests/test_antigravity_hook_bridge.py
tests/test_antigravity_runtime.py
tests/test_archive_unsafe_tool_use_id.py
tests/test_bridges_no_window.py
tests/test_checkpoint_pointer.py
tests/test_cleanup_does_not_latch_daemon_optout.py
tests/test_coach_gaunt_isolation.py
tests/test_codex_marketplace_plugin.py
tests/test_command_filters_toml.py
tests/test_consent_gate_race_window.py
tests/test_copilot_safe_python.py
tests/test_cowork_once_per_session_guard.py
tests/test_cursor_install.py
tests/test_daemon_reconcile_protects_live.py
tests/test_dashboard_data.py
tests/test_dashboard_selfheal_singleflight.py
tests/test_deterministic_candidates.py
tests/test_duplicate_script_sync.py
tests/test_flagged.py
tests/test_hermes_context_fill_nudge.py
tests/test_history_locks.py
tests/test_hook_scripts_no_window.py
tests/test_kill_stale_fail_closed.py
tests/test_live_savings_endpoint.py
tests/test_never_lose_to_baseline.py
tests/test_parity_5138_behavioral.py
tests/test_plugin_disabled_by_host.py
tests/test_provider_prefixed_model_ids.py
tests/test_python_launcher_windows_pythonw.py
tests/test_quality_top_drag.py
tests/test_redaction_assignment_rule.py
tests/test_release_installable.py
tests/test_rollup_deadline.py
tests/test_runtimes_no_window.py
tests/test_session_identity_guard.py
tests/test_sessionstart_context_tax.py
tests/test_stale_lease_sweeper.py
tests/test_statusline_signal.py
tests/test_structure_map_v2_pagerank.py
tests/test_uninstall_docs_assertions.py
tests/test_weekly_full_savings.py
tests/test_windows_start_time_local.py
```

### Batch 7

```text
tests/test_antigravity_install.py
tests/test_antigravity_safe_python.py
tests/test_async_hook_wiring.py
tests/test_build_output_compress.py
tests/test_checkpoint_retention.py
tests/test_cleanup_settings_write_safety.py
tests/test_coach_gaunt_robustness.py
tests/test_codex_sessionstart_json_contract.py
tests/test_command_runtime_detection.py
tests/test_context_window_claude5_family.py
tests/test_cost_accuracy_200.py
tests/test_cowork_standdown.py
tests/test_cursor_measure_wiring.py
tests/test_daemon_regenerate_served_path.py
tests/test_dashboard_dollar_threshold.py
tests/test_dashboard_usd_savings.py
tests/test_devin_compress.py
tests/test_effective_compact_window.py
tests/test_fleet_pricing_and_serve.py
tests/test_hermes_context_windows.py
tests/test_hook_entry_budgets.py
tests/test_hook_timeout_bounds.py
tests/test_kill_stale_hints.py
tests/test_locks_orphan_sweep.py
tests/test_nudge_guards_behavioral.py
tests/test_pinned_baseline.py
tests/test_posix_session_identity.py
tests/test_pull_checkpoint.py
tests/test_quality_cache_bootstrap.py
tests/test_quality_warn_band_gate.py
tests/test_redaction_before_truncation.py
tests/test_relevance_scorer.py
tests/test_route_subcommand.py
tests/test_runway_card_wiring.py
tests/test_session_locale_and_hooks.py
tests/test_sessionstart_quiet_stdout.py
tests/test_status_bar_command.py
tests/test_statusline_stable_path.py
tests/test_structure_map_v2_relationships.py
tests/test_uninstall_manifest_reconcile.py
tests/test_windows_child_output.py
tests/test_windows_start_time_py39.py
```

## Final adversarial closeout

Independent architecture and security finals on V10: READY. Each reproduced152 passed/1 skipped without the optional CLI binary; this checkout reproduced153 passed including genuine standalone CLI discovery. Architecture ran72 concurrent-test repetitions and24 full state-suite repetitions with no failures, plus8 hard-kill install/refresh probes with successful retries/uninstall. Security verified source-only installed imports against matching malicious bytecode, forged pending refusal, retained partial temps and hardlink gates. More than10 adversarial perspectives completed before the clean final round.

Not run: live-session CLI discovery, model adherence/delivered context, hosted deployment, physical power-loss durability, or real cross-filesystem EXDEV behavior. These are contract-only/beta boundaries.
