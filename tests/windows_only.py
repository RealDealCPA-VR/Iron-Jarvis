"""Tests that only make sense on Windows.

The first Linux and macOS runs of the suite (2026-11-10 and -11) failed these
because the TEST is about a Windows thing: a Windows-style path written in the
test, a Store app, Edge's install folder, Windows OCR, Excel, cmd.exe, a
``.cmd`` shim, ``kernel32``, or a recorded answer that holds the Windows words.
``conftest.pytest_collection_modifyitems`` skips them everywhere but Windows.

A test goes here ONLY when the thing it checks does not exist off Windows. A
test that fails on Linux because Iron Jarvis has a gap there stays out of this
list and stays red, so the gap is seen.

``"*"`` = the whole file. A name covers every parametrized case of that test.
"""

from __future__ import annotations

WINDOWS_ONLY: dict[str, tuple[str, ...]] = {
    # Store apps and their aliases (reparse points under WindowsApps).
    "test_app_alias_v14071.py": (
        "test_a_store_program_is_recognised_by_its_folder",
        "test_a_uwp_alias_is_never_started_directly",
        "test_an_alias_whose_target_exists_resolves_to_it",
        "test_direct_argv_keeps_the_arguments_and_never_changes_the_callers_list",
    ),
    # A Windows path is written in the test itself.
    "test_attachment_handoff_v1196.py": ("test_live_file_line_change_arg_moves_ONLY_the_change_clause",),
    "test_auto_approve_v14060.py": ("test_an_edit_inside_the_folder_passes_and_outside_asks",),
    "test_generate_media_tool_v1346.py": ("test_append_inline_media_never_doubles_a_path_the_reply_shows",),
    "test_capability_proposal_v1178.py": ("test_a_windows_suffixed_interpreter_is_the_same_shell",),
    "test_desktop_logs_v1229.py": ("test_open_logs_handler_behaviour",),
    "test_desktop_spotlight_outcome_v1449.py": (
        "test_a_row_with_a_folder_names_it_and_offers_open_folder",
        "test_on_windows_a_click_on_the_toast_opens_the_folder_it_names",
    ),
    # cmd.exe's words and a .cmd shim.
    "test_bash_dialect_review_v1386.py": ("test_the_posix_hint_checks_the_path_the_line_ran_with",),
    "test_cli_resolution_v1124.py": ("test_which_cli_falls_past_bare_path",),
    # Edge's and Chrome's Windows install folders.
    "test_browser_setup_edge_v1261.py": (
        "test_an_edge_only_pc_reports_exactly_edge",
        "test_both_installed_reports_chrome_first_then_edge_once_each",
        "test_the_answer_is_cached_for_the_process",
    ),
    # A native Windows shell with no child (the stray mouse-report filter).
    "test_build_panes_v1333.py": (
        "test_a_mouse_report_typed_into_an_idle_windows_shell_is_dropped_and_the_pane_told_to_stop",
        "test_a_program_relaunched_during_the_grace_is_watched_not_reset",
        "test_a_program_that_left_the_console_does_not_count_as_running_in_the_pane",
        "test_a_program_that_switches_tracking_off_itself_gets_no_reset",
        "test_a_switch_on_while_the_watcher_finishes_gets_a_watcher_of_its_own",
        "test_the_child_check_reads_the_process_table_at_most_once_a_second",
        "test_the_mouse_watcher_switches_tracking_off_when_the_program_that_asked_dies",
    ),
    # Excel and its workbook links (Windows paths, COM).
    "test_excel_live_v1466.py": ("test_a_name_with_a_dot_never_picks_another_workbook",),
    "test_excel_workbook_links_v14691.py": (
        "test_a_check_that_fails_is_a_refusal",
        "test_a_colour_rule_cannot_read_another_workbook_jarvis_may_not_read",
        "test_a_file_name_alone_is_judged_by_where_that_workbook_is_open_from",
        "test_a_formula_written_without_its_equals_sign_is_judged_too",
        "test_a_link_by_name_is_judged_by_where_excel_has_that_workbook_open_from",
        "test_a_link_into_a_folder_jarvis_may_not_read_is_refused",
        "test_a_link_into_a_protected_folder_is_refused_with_no_limit_set",
        "test_a_link_with_its_folder_is_judged_without_asking_excel",
        "test_the_edit_tool_writes_a_link_jarvis_may_read_and_refuses_one_it_may_not",
        "test_the_users_own_setting_limits_links_too",
    ),
    # kernel32 and Windows OCR.
    "test_freeze_trace_v14091.py": (
        "test_a_healthy_process_writes_no_dump",
        "test_a_held_interpreter_lock_still_dumps_every_threads_stack",
    ),
    "test_ocr_isolation_v14102.py": ("test_a_stuck_engine_costs_one_answer_never_the_app",),
    # Recorded answers that hold the Windows shell's words or Windows line ends.
    "test_build_app_antigravity_v1439.py": ("test_the_golden_file_is_what_the_real_daemon_sends_for_antigravity",),
    "test_build_app_codex_plan_v14410.py": ("test_the_dashboards_seam_is_what_the_daemon_sends",),
    "test_build_app_grok_v1416.py": ("test_the_golden_file_is_what_the_real_daemon_sends_for_grok",),
    "test_build_app_opencode_v1413.py": ("test_the_golden_file_is_what_the_real_daemon_sends_for_opencode",),
    "test_team_first_prompt_v1400.py": (
        "test_a_team_turn_with_team_first_off_is_what_v1399_4_sent",
        "test_team_first_on_adds_the_instruction_after_the_members_and_leads_every_helper_tool",
        "test_team_first_set_off_is_what_v1399_4_sent_too",
    ),
    "test_harness_bench_frontier_v1383.py": ("test_the_core_and_hard_tasks_did_not_change",),
    "test_harness_bench_team_tasks_v1400.py": (
        "test_the_earlier_tiers_did_not_change",
        "test_the_team_tasks_did_not_change",
    ),
}


def is_windows_only(nodeid: str) -> bool:
    """``tests/test_x.py::test_name[case]`` is in the list."""
    path, _, rest = nodeid.partition("::")
    names = WINDOWS_ONLY.get(path.replace("\\", "/").rsplit("/", 1)[-1])
    if not names:
        return False
    if "*" in names:
        return True
    return rest.split("[", 1)[0].rsplit("::", 1)[-1] in names
