# HZL 账号安全：第 8 步全测试清单

> 后续更新：原 29 项失败已全部修复。当前结果见 [29 项失败修复报告](HZL账号安全_29项失败修复报告.md)；本文件保留为修复前的历史记录。

日期：2026-11-20。157 个 test*.py 文件全部列入清单。
139 个文件有 pytest 收集用例；20 个脚本单独执行，其中 2 个同时有 pytest 用例。两者合并恰好覆盖 157 个文件。
pytest.ini 的原有 ignore 未删除或新增；没有新增 skip/xfail，没有删除测试或放宽断言。
完整 pytest：2307 passed / 29 failed / 1 skipped，另有 23 个 subtest 通过、143 个警告；退出码 1。
独立脚本：16 个退出码 0，1 个历史断言失败，3 个环境/数据安全边界阻塞。
脚本退出码 0 与 pytest 用例通过分开统计；不对脚本内部检查数做重复总计。

## 所有文件

| 文件 | pytest 记录 | 独立脚本 |
| --- | --- | --- |
| tests/test_account_journey.py | 6 passed | — |
| tests/test_account_profile.py | 36 passed | — |
| tests/test_agent_checkpointer.py | 3 passed | — |
| tests/test_agent_grounding.py | 1 passed | — |
| tests/test_agent_guard.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_agent_tool_binding.py | 15 passed | — |
| tests/test_announcements.py | 17 passed | — |
| tests/test_asr.py | 24 passed | — |
| tests/test_asr_stream.py | 1 failure, 29 passed | — |
| tests/test_auth_cookie_secure.py | 8 passed | — |
| tests/test_auth_gate.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_backfill_auto_calls.py | 8 passed | — |
| tests/test_baseline_agent_tools.py | 10 passed | — |
| tests/test_baseline_chat_stream.py | 5 passed | — |
| tests/test_baseline_environment.py | 4 passed | — |
| tests/test_baseline_kb_api.py | 7 passed | — |
| tests/test_baseline_rag_core.py | 14 passed | — |
| tests/test_baseline_refactor_edges.py | 20 passed | — |
| tests/test_boot_splash.py | 9 passed | — |
| tests/test_calendar_courses.py | 41 passed | — |
| tests/test_campus_state.py | 18 passed | — |
| tests/test_campus_sync_wiring.py | 28 passed | — |
| tests/test_capture_api.py | 11 passed | — |
| tests/test_capture_parse.py | 54 passed | — |
| tests/test_cet.py | 15 passed | — |
| tests/test_chat_layout.py | 28 passed | — |
| tests/test_chat_scroll.py | 9 passed | — |
| tests/test_checkin.py | 9 passed | — |
| tests/test_chroma_singleton.py | 4 passed | — |
| tests/test_citations.py | 11 passed | — |
| tests/test_compress_v2.py | 40 passed | — |
| tests/test_compress_wiring.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_console_nav.py | 29 passed | — |
| tests/test_context_compress.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_cost_guards.py | 3 passed | — |
| tests/test_course_reminder.py | 31 passed | — |
| tests/test_daily.py | 5 passed | — |
| tests/test_dark_contrast.py | 10 passed | — |
| tests/test_dashboard.py | 36 passed | — |
| tests/test_dashboard_real_users.py | 26 passed | — |
| tests/test_deadline_cloud.py | 15 passed | — |
| tests/test_delegate_flow.py | 15 passed | — |
| tests/test_delegate_layout.py | 5 passed | — |
| tests/test_deleted_user.py | 4 passed | — |
| tests/test_dialogue_import.py | 26 passed | — |
| tests/test_email_registration.py | 13 passed | — |
| tests/test_embed_guard.py | 8 passed | — |
| tests/test_fab_drag.py | 7 passed | — |
| tests/test_feedback_rate.py | 5 passed | — |
| tests/test_feedback_reply.py | 9 passed | — |
| tests/test_focus_sync.py | 12 passed | — |
| tests/test_font_subset_coverage.py | 3 passed | — |
| tests/test_forum.py | 24 passed | — |
| tests/test_forum_image.py | 14 passed | — |
| tests/test_forum_mod.py | 21 passed | — |
| tests/test_forum_report.py | 14 passed | — |
| tests/test_forum_ui.py | 43 passed | — |
| tests/test_forum_upload_api.py | 13 passed | — |
| tests/test_frontend_copy.py | 6 passed | — |
| tests/test_geo_cache.py | 1 passed | — |
| tests/test_geo_locate.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_grounding.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_hybrid_answer_pick.py | 9 passed | — |
| tests/test_hybrid_context.py | 3 passed | — |
| tests/test_hybrid_expr.py | 3 passed | — |
| tests/test_hybrid_history.py | 8 passed | — |
| tests/test_hybrid_human_suspend.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_hybrid_orchestrator.py | 6 passed | — |
| tests/test_hybrid_switcher.py | 5 passed | — |
| tests/test_hybrid_workflow.py | 7 passed | — |
| tests/test_injection_guardrails.py | 11 passed | — |
| tests/test_ios_pwa.py | 9 passed | — |
| tests/test_isolation_tools.py | 8 passed | — |
| tests/test_jwt_header_check.py | 9 passed | — |
| tests/test_jwt_login_flow.py | 15 passed | — |
| tests/test_kb_board.py | 18 passed | — |
| tests/test_kb_favorites.py | 6 passed | — |
| tests/test_kb_fork.py | 12 passed | — |
| tests/test_kb_isolation.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_kb_public.py | 27 passed | — |
| tests/test_kb_push.py | 5 passed | — |
| tests/test_kb_registry_reload.py | 4 passed | — |
| tests/test_kb_views.py | 6 passed | — |
| tests/test_layout_explains.py | 11 passed | — |
| tests/test_leak_guard.py | 27 passed | — |
| tests/test_library_favorites_ui.py | 12 passed | — |
| tests/test_llm_gate.py | 12 passed | — |
| tests/test_location_note.py | 8 passed | — |
| tests/test_mail.py | 40 passed | — |
| tests/test_metrics.py | 13 passed | — |
| tests/test_naming_consistency.py | 9 passed | — |
| tests/test_no_param_shadowing.py | 1 passed | — |
| tests/test_owner_isolation.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_p0_grounding_e2e.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_p2_polish.py | 14 passed | — |
| tests/test_page_notes.py | 8 passed | — |
| tests/test_password_reset.py | 17 passed | — |
| tests/test_path.py | 6 passed | — |
| tests/test_pipelines_concurrency.py | 6 passed | — |
| tests/test_plan_and_bind.py | 21 passed | — |
| tests/test_profile_bias.py | 11 passed | — |
| tests/test_prompt_evo.py | 32 passed | — |
| tests/test_prompt_security_hardening_2026_11_14.py | 22 passed | — |
| tests/test_push_devices.py | 23 passed | — |
| tests/test_push_schedule.py | 18 passed | — |
| tests/test_query_api.py | 1 failure, 13 passed | — |
| tests/test_query_enhance.py | 7 failure, 23 passed, 1 skipped | — |
| tests/test_query_enhance_in_chat.py | 11 passed | — |
| tests/test_query_rewrite.py | 9 passed | — |
| tests/test_radius_scale.py | 6 passed | — |
| tests/test_rate_limit_shared.py | 7 passed | — |
| tests/test_rem_create.py | 5 passed | — |
| tests/test_reminder_core.py | 63 passed | — |
| tests/test_reminder_tick.py | 45 passed | — |
| tests/test_reminders_api.py | 37 passed | — |
| tests/test_reminders_ui.py | 205 passed | — |
| tests/test_report_ui.py | 6 passed | — |
| tests/test_resume_strip.py | 4 passed | — |
| tests/test_retrieve_select.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_router.py | 29 passed | — |
| tests/test_scenarios.py | 6 passed | 退出码 0 |
| tests/test_scheduler.py | 23 passed | — |
| tests/test_security_email_settings.py | 18 passed | — |
| tests/test_security_hardening_2026_10_29.py | 18 passed | — |
| tests/test_security_mail.py | 25 passed | — |
| tests/test_sediment.py | 1 failure, 35 passed | — |
| tests/test_shell_merge.py | 12 passed | — |
| tests/test_slash_no_redirect.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_sse_disconnect.py | 9 passed | — |
| tests/test_startup_migration.py | 2 passed | — |
| tests/test_tag_hooks.py | 22 passed | — |
| tests/test_tag_taxonomy.py | 22 passed | — |
| tests/test_task_core.py | 18 failure, 45 passed | — |
| tests/test_task_remind_time.py | 1 failure, 14 passed | — |
| tests/test_tasks_api.py | 41 passed | — |
| tests/test_tier2_singleflight.py | 4 passed | — |
| tests/test_tier3_content.py | 6 passed | — |
| tests/test_timeutil_consistency.py | 12 passed | — |
| tests/test_tool_approval.py | 17 passed | — |
| tests/test_tool_guard.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_tool_playbook.py | 11 passed | — |
| tests/test_tool_registry.py | 无收集用例，单独执行 | 退出码 1（原断言失败） |
| tests/test_truncation_detect.py | 17 passed | — |
| tests/test_upload_stream.py | 4 passed | — |
| tests/test_usage_stats.py | 5 passed | 退出码 0 |
| tests/test_user_prefs.py | 23 passed | — |
| tests/test_verification.py | 31 passed | — |
| tests/test_verify_tag.py | 26 passed | — |
| tests/test_w1.py | 无收集用例，单独执行 | 退出码 1（环境/安全边界阻塞） |
| tests/test_w2.py | 无收集用例，单独执行 | 退出码 1（环境/安全边界阻塞） |
| tests/test_w3.py | 无收集用例，单独执行 | 退出码 1（环境/安全边界阻塞） |
| tests/test_web_import_creates_index.py | 无收集用例，单独执行 | 退出码 0 |
| tests/test_web_prefetch.py | 11 passed | — |
| tests/test_web_search_config.py | 4 passed | — |
| tests/test_web_search_tool.py | 4 passed | — |
| tests/test_web_sources.py | 4 passed | — |
| tests/test_wordbank.py | 9 passed | — |

JUnit 的每文件记录包含 subtest；本表不据此重算 pytest 主用例总数。
无收集用例的脚本均已实际单独执行，不把 pytest 导入成功或 collected 0 当成通过。
本清单范围为 tests/。tools/ 中本功能浏览器工具另有汇总；CI 其他历史运维工具不冒称已逐一验收。

## 29 项失败明细

全部同名用例在原始提交的临时副本中也失败。数据路径拦截属于未满足的测试前提，不当作产品缺陷。

- `tests.test_asr_stream.TestDegradation::test_capabilities_reports_both` — 原始提交复现。
- `tests.test_query_api::test_shipped_vocab_still_loads` — 原始提交复现。
- `tests.test_query_enhance::test_golden_regression_does_not_degrate` — 原始提交复现。
- `tests.test_query_enhance::test_adjudication_loads_all_words` — 原始提交复现。
- `tests.test_query_enhance::test_block_words_excludes_conditional` — 原始提交复现。
- `tests.test_query_enhance::test_conditional_word_depends_on_collocation` — 原始提交复现。
- `tests.test_query_enhance::test_clarify_only_fires_when_word_present` — 原始提交复现。
- `tests.test_query_enhance::test_conditional_adjudication_keeps_golden_row_working` — 原始提交复现。
- `tests.test_query_enhance::test_x2_broken_sentences_are_fixed` — 原始提交复现。
- `tests.test_sediment.TestStreamWiring::test_kb_proposal_frame_before_done` — 原始提交复现。
- `tests.test_task_core.TestCreate::test_remind_creates_linked_reminder` — 原始提交复现。
- `tests.test_task_core.TestUpdate::test_change_due_does_not_touch_fire_at` — 原始提交复现。
- `tests.test_task_core.TestUpdate::test_change_remind_moves_fire_at_only` — 原始提交复现。
- `tests.test_task_core.TestUpdate::test_clear_remind_disables_but_keeps_row` — 原始提交复现。
- `tests.test_task_core.TestUpdate::test_title_syncs_to_reminder` — 原始提交复现。
- `tests.test_task_core.TestUpdate::test_setting_remind_later_creates_it` — 原始提交复现。
- `tests.test_task_core.TestLifecycle::test_complete_stops_the_reminder` — 原始提交复现。
- `tests.test_task_core.TestLifecycle::test_delete_removes_linked_reminder` — 原始提交复现。
- `tests.test_task_core.TestIsolation::test_cross_user_is_none_everywhere` — 原始提交复现。
- `tests.test_task_core.TestBulk::test_complete_marks_all_and_stops_reminders` — 原始提交复现。
- `tests.test_task_core.TestBulk::test_reopen_does_not_resurrect_past_reminder` — 原始提交复现。
- `tests.test_task_core.TestBulk::test_delete_removes_linked_reminder` — 原始提交复现。
- `tests.test_task_core.TestReminderBoundary::test_reminder_list_excludes_task` — 原始提交复现。
- `tests.test_task_core.TestReminderBoundary::test_reminder_summary_excludes_task` — 原始提交复现。
- `tests.test_task_core.TestReminderBoundary::test_closing_a_source_does_not_touch_task_reminders` — 原始提交复现。
- `tests.test_task_core.TestReminderBoundary::test_linked_reminder_does_not_eat_quota` — 原始提交复现。
- `tests.test_task_core.TestReminderBoundary::test_worker_sends_and_records_sent` — 原始提交复现。
- `tests.test_task_core.TestReminderBoundary::test_once_advance_disables` — 原始提交复现。
- `tests.test_task_remind_time.TestBackfill::test_past_and_done_and_already_set_rows_are_skipped` — 原始提交复现。

## 历史跳过

未添加新 skip；既有测试因缺少 X2 归档资料被跳过：

- `tests.test_query_enhance::test_x2_no_query_ever_gets_rewritten`：缺 X2 真实问法（docs/组员交付_2026-10-24 未归档）

## 命令与原始证据

主命令：` .venv\Scripts\python.exe tools/run_isolated_tests.py tests -q `。
脚本命令：` .venv\Scripts\python.exe tools/run_isolated_tests.py --script tests/test_名称.py `。
运行前设置 PYTHONUTF8=1、PYTHONIOENCODING=utf-8。
原始日志和 XML 保留在项目 .venv/step8-*；每文件源码和日志摘要保存在 HZL账号安全_第8步测试证据.json。
不要直接执行需要真实服务的 W1/W2/W3；本次保留隔离失败结果，没有连接现有 8000 服务或读取真实 .env。
