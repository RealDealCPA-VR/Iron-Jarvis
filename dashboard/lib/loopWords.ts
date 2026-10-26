/**
 * BACKGROUND TASKS IN THE USER'S WORDS (v1.355.0, product-readiness G32).
 *
 * The daemon keys every background loop (and every boot step) by an engine
 * name in `loop_health` (daemon/app.py `_tick`): `slack_socket`, `fleet`,
 * `rehydrate_goals`. The bell, Everything › Status and the home line used to
 * print that name ("Background task slack_socket has been failing for 12
 * min") and link to the chat home. This file is the ONE place that says, for
 * each name:
 *
 *   - `label`   what the task does for the user ("Slack messages");
 *   - `problem` the failure as one plain sentence, no full stop ("Slack
 *               messages aren't arriving");
 *   - `href`    where its fix lives (the channels page for Slack, the phone
 *               and the calendar; Fleet for model servers; Connections for the
 *               account switcher; Schedules; the team view for queued jobs;
 *               Creative for media; Everything › Status when there is no
 *               better door);
 *   - `still`   what keeps working while it is down, as a sentence, or ""
 *               when the app cannot vouch for it (a model-server or account
 *               problem can stop chat itself, so those say nothing).
 *
 * An unknown name reads "A background task isn't working" and opens Status;
 * the engine name is never shown (callers keep it in a hover title).
 * tests/test_background_names_v13550.py reads daemon/app.py and fails when a
 * loop or boot step the daemon can report has no row here (or a row names a
 * loop that no longer exists): a new loop ships with its words.
 *
 * Dependency-free apart from lib/mission (runtime-free: one type-only import): the bell is on
 * every page and ~71 test files mock lib/api wholesale.
 */

import { agentsPath } from "./mission";

/** Everything › Status: the page that lists every failing task by name. */
export const STATUS_HREF = "/everything#status";

/** What keeps working while a background task is down (the usual case). */
export const CHAT_STILL_WORKS = "Chat keeps working.";

/** The question the home line's "Ask Jarvis" puts in the composer. It arms
 *  the health readers (app_health, run_doctor, pack_status — the failure
 *  vocabulary in tools/autoselect), so the answer reads the app's own health
 *  instead of guessing. Prefilled, never sent (the consent rule). */
export const HEALTH_QUESTION = "Why is a background task failing?";

export interface LoopWords {
  label: string;
  problem: string;
  href: string;
  still: string;
}

interface Row {
  label: string;
  problem: string;
  href: string;
  /** Absent = CHAT_STILL_WORKS; "" = say nothing. */
  still?: string;
}

const TEAM = agentsPath({ kind: "team" });

const ROWS: Readonly<Record<string, Row>> = {
  // Loops that run all the time.
  slack_socket: {
    label: "Slack messages",
    problem: "Slack messages aren't arriving",
    href: "/channels",
    still: "Chat and your other channels keep working.",
  },
  inbound: { label: "Phone messages", problem: "Phone messages aren't arriving", href: "/channels" },
  calendar: { label: "Calendar", problem: "Your calendar isn't updating", href: "/channels" },
  scheduler: { label: "Schedules", problem: "Schedules aren't starting", href: "/schedules" },
  fleet: {
    label: "Model servers",
    problem: "Your model servers can't be checked",
    href: "/fleet",
    still: "",
  },
  iron_proxy: {
    label: "Account switcher",
    problem: "The account switcher isn't answering",
    href: "/connections",
    still: "",
  },
  build_accounts: {
    label: "Resume when ready",
    problem: "Build sessions waiting for an account to reset aren't being checked",
    href: "/terminals",
  },
  // v1.391.0 (long jobs, Phase 2): the boot's pass that carries on a coding
  // session's reply a restart cut off while it worked.
  build_resume: {
    label: "Carry on after a restart",
    problem: "Coding sessions cut off by a restart weren't carried on",
    href: "/terminals",
  },
  // v1.398.0 (night shift N1): the loop that starts the Tonight list's
  // coding tasks inside the night window and settles them when they end.
  night_shift: {
    label: "Night shift",
    problem: "Tonight's coding tasks aren't being started or checked",
    href: "/terminals",
  },
  dispatcher: { label: "Queued tasks", problem: "Queued tasks aren't starting", href: TEAM },
  autonomy: { label: "Autonomy", problem: "Work Jarvis plans on its own isn't being checked", href: "/autonomy" },
  sentinels: { label: "Watchers", problem: "Your watchers aren't checking", href: "/sentinels" },
  lesson_compaction: { label: "Lessons", problem: "Your lessons aren't being tidied", href: "/lessons" },
  skill_curator: { label: "Skills", problem: "Your skills aren't being tidied", href: "/skills" },
  auto_backup: { label: "Backups", problem: "Backups aren't being made", href: STATUS_HREF },
  backup_mirror: {
    label: "Backup copy",
    problem: "The backup copy to your other drive failed",
    href: STATUS_HREF,
  },
  fts_backfill: { label: "Chat search", problem: "Older chats aren't searchable yet", href: STATUS_HREF },
  // Steps that run once when Iron Jarvis starts.
  requeue_assignments: {
    label: "Queued tasks",
    problem: "Queued tasks weren't picked back up after the restart",
    href: TEAM,
  },
  reconcile_media_jobs: {
    label: "Pictures and media",
    problem: "Media you asked for wasn't checked after the restart",
    href: "/creative",
  },
  warm_opencode: {
    label: "OpenCode models",
    problem: "OpenCode's models weren't loaded",
    href: "/connections",
    still: "",
  },
  probe_cli_logins: {
    label: "Sign-in check",
    problem: "Your sign-ins weren't checked when Iron Jarvis started",
    href: "/connections",
    still: "",
  },
  reconcile_tool_calls: {
    label: "Cut-off steps",
    problem: "Steps the restart cut off weren't tidied up",
    href: STATUS_HREF,
  },
  reconcile_chat_turns: {
    label: "Cut-off replies",
    problem: "Replies the restart cut off weren't tidied up",
    href: STATUS_HREF,
  },
  reconcile_sessions: {
    label: "Cut-off tasks",
    problem: "Tasks the restart cut off weren't tidied up",
    href: "/sessions",
  },
  notify_interrupted_comm: {
    label: "Phone messages",
    problem: "Your phone wasn't told about a task the restart cut off",
    href: "/channels",
  },
  rehydrate_goals: { label: "Goals", problem: "Your goals weren't loaded", href: "/autonomy" },
  rehydrate_reviews: { label: "Reviews", problem: "Reviews waiting for you weren't loaded", href: "/kanban" },
  revalidate_active_project: {
    label: "Active project",
    problem: "Your active project wasn't checked",
    href: "/projects",
  },
  reconcile_workflow_runs: {
    label: "Workflows",
    problem: "Workflow runs the restart cut off weren't tidied up",
    href: "/workflows",
  },
  prune_workflow_runs: { label: "Workflows", problem: "Old workflow runs weren't cleared", href: "/workflows" },
  reconcile_proposals: {
    label: "Autonomy",
    problem: "Plans the restart cut off weren't tidied up",
    href: "/autonomy",
  },
  rehydrate_webhooks: { label: "Webhooks", problem: "Your webhooks weren't turned back on", href: "/webhooks" },
  rehydrate_terminals: {
    label: "Build terminals",
    problem: "Your Build terminals weren't brought back",
    href: "/terminals",
  },
  seed_starter_templates: {
    label: "Starter templates",
    problem: "The starter templates weren't added",
    href: "/templates",
  },
};

const UNKNOWN: Row = { label: "A background task", problem: "A background task isn't working", href: STATUS_HREF };

/** Each known task's name in the user's words. */
export const LOOP_LABELS: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(ROWS).map(([k, r]) => [k, r.label]),
);

/** Where each known task's fix lives. */
export const LOOP_HREF: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(ROWS).map(([k, r]) => [k, r.href]),
);

/** The words for one loop_health key. Never throws, never returns the key. */
export function loopWords(name: unknown): LoopWords {
  const key = typeof name === "string" ? name.trim() : "";
  const row = (key && Object.prototype.hasOwnProperty.call(ROWS, key) ? ROWS[key] : undefined) ?? UNKNOWN;
  return {
    label: row.label,
    problem: row.problem,
    href: row.href,
    still: row.still ?? CHAT_STILL_WORKS,
  };
}

/** "<problem>. <still>" — one line for a row's body or a note. */
export function loopSentence(name: unknown): string {
  const w = loopWords(name);
  return w.still ? `${w.problem}. ${w.still}` : `${w.problem}.`;
}
