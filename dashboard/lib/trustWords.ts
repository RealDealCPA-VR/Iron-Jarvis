/**
 * v1.356.0 (G77, product-readiness wave 6): what a LOW-TRUST run cannot do,
 * in words a new user reads on the "low trust" chip's hover.
 *
 * The daemon keeps a run that started from an inbound message (or read
 * something flagged as an injection) away from every tool in
 * `core/trust.LOW_TRUST_DENY` and `core/trust.CODE_DENY` (the tools that run
 * code the model wrote). The chip used to say only "low trust" and the
 * daemon's reason; it never said what that blocks. The families below are
 * that set's MEANING, and `tests/test_trust_words_v13560.py` reads both this
 * file and the real set: a tool added to the deny set without a family here
 * (or a family naming a tool the set no longer holds) fails that pin, so the
 * sentence can never quietly fall behind what the daemon blocks.
 *
 * Plain data, no imports: the chip is on the session board and the run page.
 */

/** Every kept-away tool, by what it would have changed. The ORDER is the
 *  order the sentence says them in. */
export const LOW_TRUST_FAMILIES: ReadonlyArray<{ words: string; tools: readonly string[] }> = [
  {
    words: "settings",
    tools: [
      "config_set",
      "config_change",
      "config_change_protected",
      "config_secret",
      "config_undo",
      // v1.435.0 (Wave 3 group I): any undo, and Local only for a chat.
      "change_undo",
      "chat_local_only",
      "secret_set",
      // v1.438.0 (Wave 4 group C): a chat persona is how chat answers — set
      // up like a setting, its words reach every chat that uses it.
      "persona_create",
      "persona_update",
      "persona_delete",
      // v1.438.0 (Wave 4 group A): a folder's defaults and coding team for
      // new coding sessions.
      "build_defaults_set",
      "build_defaults_clear",
      "build_team_set",
      "build_team_clear",
      // 22-a (R1.9): whether Iron Jarvis runs a folder's tests after each
      // Build › Code reply.
      "build_checks_set",
      // 26-a (R2.16): whether you are told about a pull request's new
      // review comments (a flagged comment must not silence the others).
      "pr_comments_quiet",
      // v1.506.0 (north-star plan 6.2): answering a suggestion on the
      // Improvements page (an accept can change how every chat answers).
      "suggestion_answer",
    ],
  },
  {
    words: "schedules",
    tools: [
      "schedule_create",
      "schedule_update",
      "schedule_delete",
      "workflow_create",
      "workflow_update",
      "workflow_delete",
      "workflow_schedule",
      "workflow_run",
      "worklist_add",
      "assign_work",
      // v1.438.0 (Wave 4 group A): a night task queued, cancelled or moved —
      // unattended work a later night starts.
      "night_task_add",
      "night_task_cancel",
      "night_task_move",
    ],
  },
  {
    words: "rules",
    tools: [
      "reflex_create",
      "reflex_update",
      "reflex_delete",
      "webhook_add",
      "webhook_update",
      "webhook_delete",
      "sentinel_add",
      "sentinel_update",
      "sentinel_delete",
      "goal_add",
      "goal_update",
      "goal_delete",
    ],
  },
  {
    words: "connections",
    tools: [
      "channel_toggle",
      "channel_connect",
      "app_connect",
      "app_switch",
      "app_disconnect",
      "app_import",
      "channel_remove",
      "channel_alerts",
      "pack_restart",
      "provider_rescan",
      "delegate_remote",
      // v1.438.0 (Wave 4 group B): a model server, an account.
      "model_server_add",
      "model_server_default",
      "model_server_remove",
      "account_add",
      // v1.448.0 (Wave A item 4): a tool server, a web service.
      "mcp_server_add",
      "web_service_add",
      // Next leaps 41-a: a paired computer's switch turned off (it stops
      // that computer's tasks running here).
      "peer_allow_off",
      // Next leaps 43-a: a model downloaded (it fills the drive).
      "model_download",
    ],
  },
  {
    words: "memory",
    tools: [
      "memory_write",
      "memory_propose",
      "ltm_append",
      "notebook",
      "remember_preference",
      "brain_save",
      "brain_change",
      // v1.438.0 (chat-first plan Wave 4 group C): a kept preference or
      // lesson reaches every later answer.
      "preference_edit",
      "preference_forget",
      "lesson_add",
      // v1.438.0 (Wave 4 group B).
      "memory_base_add",
      // v1.499.0 (leap 3 Phase 3): what is kept for one client reaches
      // every later chat in its folders.
      "client_answer",
      "client_update",
      "client_preference_add",
      "client_preference_change",
      "client_preference_forget",
    ],
  },

  // v1.435.0 (chat-first plan Wave 3 group G): projects from chat — a
  // project grounds every later chat filed in it.
  {
    words: "projects",
    tools: ["project_create", "project_update", "project_archive", "project_focus"],
  },
  {
    words: "agents or skills",
    tools: [
      "create_agent",
      "skill_create",
      "skill_update",
      "capability_propose",
      "tool_create",
      "tool_delete",
      "agent_create",
      "agent_update",
      "agent_pause",
      "agent_delete",
    ],
  },
  {
    words: "terminals",
    tools: ["pane_send", "pane_spawn"],
  },
  // v1.364.0 (IJ harness Phase 1): a program that keeps running cannot be
  // put in the low-trust sandbox, so starting one, or typing into one, is
  // kept away (reading its output and stopping it are not).
  {
    words: "programs",
    tools: ["process_start", "process_write"],
  },
  // Third fix plan (PG3): code the model writes runs on this computer with
  // the network open, outside the low-trust sandbox, so running it is kept
  // away too (`core/trust.CODE_DENY`).
  {
    words: "code it writes",
    tools: ["run_code", "code_run", "repl"],
  },
  {
    words: "your web services",
    tools: ["rest_call"],
  },
  // v1.371.0: a screenshot of a page on this PC or network goes to the
  // model, so flagged content must not point it at another app's page.
  {
    words: "screenshots of pages",
    tools: ["page_screenshot"],
  },
  // v1.376.0 (IJ harness part 2, Phase 5): the agent's own browser —
  // flagged content must not open, click, type in or read back a page.
  {
    words: "pages in its own browser",
    tools: ["page_open", "page_click", "page_type", "page_read"],
  },
  // v1.444.0: a file the browser downloaded — flagged content must not
  // rename, move or copy the user's downloads.
  {
    words: "downloaded files",
    tools: ["download_manage"],
  },
  {
    words: "spending",
    tools: ["generate_media"],
  },
];

/** What a low-trust run cannot do, one sentence (the chip's hover). */
export const LOW_TRUST_CANNOT =
  "This run can't change settings, schedules, rules, connections, memory, projects, agents or skills, " +
  "can't type into your terminals, can't start programs that keep running, can't run code it writes, " +
  "can't call your web services, can't take screenshots of pages, can't use pages in its own browser, " +
  "can't rename, move or copy downloaded files " +
  "and can't spend credits. " +
  "It can still read, search and answer.";

/** The chip's whole hover: the daemon's reason (and since when), then what
 *  the run cannot do. */
export function lowTrustTitle(reason: string | null | undefined, since: string): string {
  const why = (reason ?? "").trim() || "This run is in low trust";
  const lead = since ? `${why} — since ${since}` : why;
  return `${lead}. ${LOW_TRUST_CANNOT}`;
}
