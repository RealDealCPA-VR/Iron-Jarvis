/**
 * v1.356.0 (G77, product-readiness wave 6): what a LOW-TRUST run cannot do,
 * in words a new user reads on the "low trust" chip's hover.
 *
 * The daemon keeps a run that started from an inbound message (or read
 * something flagged as an injection) away from every tool in
 * `core/trust.LOW_TRUST_DENY`. The chip used to say only "low trust" and the
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
      "secret_set",
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
      "pack_restart",
      "provider_rescan",
      "delegate_remote",
    ],
  },
  {
    words: "memory",
    tools: ["memory_write", "memory_propose", "ltm_append", "notebook", "remember_preference"],
  },
  {
    words: "agents or skills",
    tools: ["create_agent", "skill_create", "capability_propose", "tool_create", "tool_delete"],
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
  {
    words: "spending",
    tools: ["generate_media"],
  },
];

/** What a low-trust run cannot do, one sentence (the chip's hover). */
export const LOW_TRUST_CANNOT =
  "This run can't change settings, schedules, rules, connections, memory, agents or skills, " +
  "can't type into your terminals, can't start programs that keep running, " +
  "can't call your web services, can't take screenshots of pages and can't spend credits. " +
  "It can still read, search and answer.";

/** The chip's whole hover: the daemon's reason (and since when), then what
 *  the run cannot do. */
export function lowTrustTitle(reason: string | null | undefined, since: string): string {
  const why = (reason ?? "").trim() || "This run is in low trust";
  const lead = since ? `${why} — since ${since}` : why;
  return `${lead}. ${LOW_TRUST_CANNOT}`;
}
