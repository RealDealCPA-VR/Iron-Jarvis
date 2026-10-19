/**
 * What unattended work does when it needs your OK (v1.355.0, G65).
 *
 * A schedule, a goal and a workflow run with nobody watching. When one of
 * their steps needs approval it waits a bounded time — the daemon's
 * `agents/runtime.SESSION_APPROVAL_TIMEOUT_S` (300 s) — rings the bell and
 * the desktop notice, and then refuses that step (the run records it as
 * needing you). The create forms never said so, even for the template that
 * moves files, so a user found out only from a run that came back short.
 *
 * ONE sentence, said the same way under every form that sets up unattended
 * work (Schedules, Goals, Workflows). `UNATTENDED_ASK_MINUTES` is pinned equal
 * to the daemon's constant by tests/test_model_doors_v13550.py, so the words
 * cannot drift from the clock they describe.
 *
 * Dependency-free on purpose: pages import it, and ~70 test files mock
 * lib/api.ts wholesale.
 */

/** How long an unattended ask waits, in minutes (runtime: 300 s). */
export const UNATTENDED_ASK_MINUTES = 5;

/** The one sentence the create forms show. */
export const UNATTENDED_ASK_NOTE =
  "If a step needs your OK it waits 5 minutes (bell and desktop notice); if nobody answers, that step is skipped.";
