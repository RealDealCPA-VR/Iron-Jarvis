/**
 * PLAIN WORDS FOR A LEDGER EVENT (v1.232.0 audit U12, v1.314.0 UX wave 2;
 * lifted into lib/ in v1.354.0, product-readiness G20; v1.449.0: EVERY
 * event type, plus the payload's own detail).
 *
 * The Time-travel feed (components/TimeTravelFeed) said these words first.
 * The run page's live activity rows printed `JSON.stringify(payload)` for
 * any event it had no special case for, and it cannot import them from the
 * feed: ~11 test files mock `@/components/TimeTravelFeed` with a factory that
 * exports the component alone, and a mocked module throws on any other
 * export. So the table lives here, dependency-light, and the feed re-exports
 * it (one table, never two — `dashboard/__tests__/run-page-words-v13540.test.ts`
 * pins the feed's copy equal to this one until it does).
 *
 * v1.449.0 (readiness plan 0h.5 item 14): the Activity page printed the raw
 * event id ("trust.lowered", "webhook.rejected") for 67 of the 87 event
 * types. Now EVERY `EventType` member of `core/events.py` — and every name
 * in its `OTHER_PUBLISHED_TYPES` — has a headline here, and
 * `dashboard/__tests__/event-words-every-type-v1449.test.ts` reads that
 * Python file and FAILS on a new type with no words (the
 * `tests/test_background_names_v13550` pattern); `comm/notifier.EVENT_WORDS`
 * is the daemon twin, pinned equal by `tests/test_integrate_wave4_v13540.py`.
 * `eventDetail` adds what the payload says in a few words ("A webhook was
 * refused · github-push · bad signature") — never a path, a key, an id or a
 * raw payload dump: only the keys named per type, each value checked by
 * `safeDetailValue`.
 */

import { friendlyModelName } from "@/lib/friendlyModelName";
import { providerDisplay } from "@/lib/onboarding";
import { outcomeWords, statusWords } from "@/lib/statusWords";
import { maskDetailText } from "@/lib/stepDetail";
import { toolWords } from "@/lib/toolWords";

/** Plain words for the event types the ledger projects straight into a row's
 *  `summary` (`_event_summary` says "provider.routed <model>" for a decision,
 *  and `actor` falls back to the type when nothing stamped an origin). A row
 *  read "decision by provider.routed / provider.routed" — the type twice and
 *  no words. Unknown types keep their summary verbatim (a new kind must read
 *  oddly, never vanish — and the pin above fails on it). The values are read
 *  by a Python regex (`"key": "value"` on one line, no `"` inside a value), so
 *  keep each row on one line with plain double quotes. */
export const EVENT_WORDS: Record<string, string> = {
  "provider.routed": "Picked a model for this turn",
  "provider.failover": "Switched provider after a failure",
  // v1.314.0: never "mock" in words, and true to the router since v1.162.0 —
  // this event now means the chosen model could not answer (it refuses; the
  // only time the demo model still answers is the mock-default trap, where
  // no real model answered either).
  "provider.downgraded": "No real model answered",
  "autonomy.proposed": "Proposed an action on its own",
  "autonomy.executed": "Ran an action on its own",
  "llm.completed": "Model call finished",
  "session.created": "Session started",
  "session.completed": "Session finished",
  "agent.started": "Agent started",
  "agent.state_changed": "Agent changed state",
  "agent.completed": "Agent finished",
  "action.reverted": "Action undone",
  "tool.executed": "Ran a tool",
  "tool.denied": "Refused a tool",
  // v1.314.0 (UX wave 2): the events the Activity page printed raw.
  "delegation.started": "Handed work to a teammate",
  "delegation.completed": "Teammate finished",
  "comm.desktop": "Sent a desktop notification",
  // v1.433.0 (A10): the answers to a review (they now publish an event).
  "review.approved": "Review approved and merged",
  "review.rejected": "Review declined",
  // v1.446.0 (ease-of-use review item 20): a rule that could not start.
  "reflex.failed": "A rule couldn't start",
  // v1.449.0 (readiness 0h.5 item 14): the rest of core/events.py's
  // EventType, in the order it declares them.
  "session.queued": "A run was queued until a slot frees",
  "runs.busy": "Many unattended runs are working at once",
  "sessions.interrupted": "A restart cut off running work",
  "agent.paused": "An agent was paused at its allowance",
  "agent.allowance_warning": "An agent is near its monthly allowance",
  "assignment.created": "A task was queued for an agent",
  "assignment.started": "An agent started a queued task",
  "assignment.finished": "An agent finished a queued task",
  "assignment.blocked": "A queued task was blocked after repeated failures",
  "assignment.requeued": "Queued tasks were put back after a restart",
  "artifact.generated": "A file was made",
  "memory.updated": "Memory was updated",
  "workflow.completed": "Workflow finished",
  "workflow.step_started": "A workflow step started",
  "workflow.step_completed": "A workflow step finished",
  "plan.created": "The agent made a plan",
  "plan.step_started": "A plan step started",
  "plan.step_completed": "A plan step finished",
  "approval.requested": "An agent asked for your OK",
  "approval.resolved": "An agent's ask was answered",
  "interrupt.asked": "A card or question was shown",
  "interrupt.answered": "A card or question was answered",
  "autonomy.nudged": "Carried on by itself",
  "batch.file_done": "A document in a batch was processed",
  "review.requested": "A change is waiting for your review",
  "provider.failed": "A model failed to answer",
  "context.trimmed": "Older messages were left out to fit the model",
  "context.compacted": "Older messages were summarized",
  "webhook.received": "A webhook arrived",
  "webhook.rejected": "A webhook was refused",
  "comm.received": "A message arrived",
  "comm.rejected": "A message from an unknown sender was refused",
  "comm.dropped": "A message was dropped during a restart",
  "reflex.fired": "A rule fired",
  "schedule.fired": "A schedule fired",
  "computeruse.run_finished": "A computer-use run finished",
  "skill.proposal_created": "A new skill was proposed",
  "coach.proposal": "A coaching suggestion for an agent",
  "preference.suggested": "Suggested remembering a preference",
  "preference.kept": "A preference was kept",
  "preference.declined": "A preference was declined",
  "ui.page_opened": "A page was opened",
  "config.changed": "A setting changed",
  "chat.thread_updated": "A chat was updated",
  "build.account_limit": "A coding session hit its account's limit",
  "build.resumed": "A coding session carried on",
  "build.rewound": "A coding session was rewound",
  "build.turn_finished": "A coding session's reply finished",
  "chat.turn_finished": "A long chat reply finished",
  "build.turn_stalled": "A coding session's reply stalled",
  "build.night_summary": "The night shift's summary is ready",
  "build.ci_step": "A pull-request check step",
  "build.ci_done": "A pull-request check ended",
  "library.proposal": "A lesson or rule was proposed for a folder",
  "agent_thread.updated": "A team conversation was updated",
  "remote.message": "A remote agent messaged back",
  "browser.connected": "The browser add-on connected",
  "browser.disconnected": "The browser add-on disconnected",
  "browser.tab_activated": "You switched browser tabs",
  "browser.navigation_completed": "A browser tab finished loading a page",
  "browser.tab_removed": "A browser tab was closed",
  "browser.download_completed": "A browser download finished",
  "detection.finding": "A safety check flagged something",
  "trust.lowered": "Trust was lowered after suspicious content",
  "context.blocked": "Suspicious text was removed before it reached the model",
  "grant.created": "A standing approval was granted",
  "grant.revoked": "A standing approval was revoked",
  "mcp.loaded": "Tool extensions loaded",
  // core/events.py OTHER_PUBLISHED_TYPES (published by name).
  "comm.email_composed": "An email was drafted or sent",
  "desktop.incident": "The desktop app reported a problem",
  "envelope.adapted": "The request was adapted to the model's limits",
  "envelope.probe_started": "Started measuring a model's limits",
  "envelope.probe_completed": "Finished measuring a model's limits",
  "goal.created": "A goal was created",
  "goal.iteration_started": "A goal's run started",
  "goal.iteration_completed": "A goal's run finished",
  "goal.iteration_refused": "A goal's run was refused",
  "goal.satisfied": "A goal was satisfied",
  "goal.tripped": "A goal's breaker tripped",
  "goal.stopped": "A goal was stopped",
  "mcp.pack_failed": "A tool extension failed",
  "media.completed": "A picture, video or sound is ready",
  "media.failed": "A picture, video or sound could not be made",
  "slack.event": "A Slack message arrived",
  "workflow.waiting": "A workflow is waiting for your answer",
};

/** v1.314.0: "agent running→completed" (eval/observability._event_summary
 *  for agent.state_changed) as a sentence. */
const AGENT_STATE_RE = /^agent ([a-z_]+)\s*→\s*([a-z_]+)$/;
/** v1.314.0: a token row ("<provider>/<model> · <in>+<out> tok", the same
 *  _event_summary). Anchored on the " tok" tail so a path with a slash in an
 *  ordinary summary is never mistaken for one. */
const TOKEN_SUMMARY_RE = /^([\w.-]+)\/(\S*) · (\d+\+\d+ tok)$/;

const EVENT_TYPE_RE = /^([a-z_]+\.[a-z_.]+)\b\s*(.*)$/s;

/** The row's second line: the event's human label (plus the summary's own
 *  detail, e.g. the model picked) when the summary is a bare event type. */
export function humanSummary(summary: string | null | undefined): string {
  const s = (summary ?? "").trim();
  if (!s) return "";
  const st = AGENT_STATE_RE.exec(s);
  if (st) return `Agent went from ${st[1]} to ${st[2]}`;
  // A token row names the provider in words; the scripted model is "Demo
  // model", never "mock" (its model id is "mock-1", so it is dropped too).
  // A real row keeps its model id — it is the record of what ran.
  const tok = TOKEN_SUMMARY_RE.exec(s);
  if (tok) {
    if (tok[1] === "mock") return `Demo model · ${tok[3]}`;
    return [providerDisplay(tok[1]), tok[2], tok[3]].filter(Boolean).join(" · ");
  }
  const m = EVENT_TYPE_RE.exec(s);
  if (!m) return s;
  const words = EVENT_WORDS[m[1]];
  if (!words) return s;
  const detail = m[2].trim();
  return detail ? `${words} · ${detail}` : words;
}

// --------------------------------------------------------------------------
// v1.449.0: the payload's own detail, in a few safe words
// --------------------------------------------------------------------------

/** The most characters one detail value may take on the line. */
export const DETAIL_VALUE_MAX = 80;

/** A value that is a path (a drive, a slash, a backslash), an id or a key
 *  (a long run of token characters with a digit, or a known key prefix), a
 *  URL, or JSON is NEVER shown on the headline — the payload itself stays
 *  under the row for anyone who wants it.
 *
 *  Review (v1.449.0): a run's `task` and a preference's `text` are the
 *  USER'S OWN WORDS, and this is a CPA firm's PC — "Email bob@firm.com the
 *  K-1, ssn 123-45-6789" used to reach the Activity headline whole. So a
 *  value is also DROPPED (never masked and shown half) when the dashboard's
 *  own mask (lib/stepDetail, the twin of detections.redact) would change it,
 *  when it carries an "@" (an e-mail, a user:pass@host), nine or more digits
 *  in a row with separators (an SSN, a card, an EIN, a phone, an account
 *  number), a JWT, a PEM block, or a credential word with a value after it
 *  ("password hunter2"). The headline alone is still words. */
const PATH_RE = /^[A-Za-z]:[\\/]|[\\/]/;
const TOKEN_RE = /^[A-Za-z0-9_-]{20,}$/;
const KEY_RE = /\b(?:sk|xai|gsk|hf|ghp|xox[a-z])[-_][A-Za-z0-9]|\b(?:AIza|AQ\.)[A-Za-z0-9_-]{10,}|\b(?:AKIA|ASIA)[0-9A-Z]{12,}|\beyJ[A-Za-z0-9_-]{8,}\.|-----BEGIN /i;
const ID_RE = /^(?:[a-z]+_)?[0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4}$/i;
const URL_RE = /^[a-z][a-z0-9+.-]*:\/\//i;
/** Nine or more digits, each pair split by at most one space or dash. */
const NUMBER_RUN_RE = /\d(?:[ -]?\d){8,}/;
/** A credential word followed by something that could be its value. */
const CREDENTIAL_WORD_RE = /\b(?:password|passwd|passphrase|secret|token|api[ -]?key|bearer|basic)\b\s*[:=]?\s*\S/i;

export function safeDetailValue(v: unknown): string {
  if (typeof v !== "string") return "";
  const s = v.replace(/\s+/g, " ").trim();
  if (!s) return "";
  if (PATH_RE.test(s) || URL_RE.test(s) || KEY_RE.test(s)) return "";
  if (TOKEN_RE.test(s) || ID_RE.test(s)) return "";
  if (s.startsWith("{") || s.startsWith("[")) return "";
  if (s.includes("@") || NUMBER_RUN_RE.test(s) || CREDENTIAL_WORD_RE.test(s)) return "";
  if (maskDetailText(s) !== s) return "";
  return s.length > DETAIL_VALUE_MAX ? `${s.slice(0, DETAIL_VALUE_MAX - 1).trimEnd()}…` : s;
}

/** A provider named for the headline: its plain name, "your model server"
 *  for the user's own endpoint or a fleet node (the ids `providerDisplay`
 *  does not know are those — "fleet-custom" is an id, never a word), else
 *  nothing (review of v1.449.0). */
export function providerDetail(provider: unknown): string {
  const p = typeof provider === "string" ? provider.trim() : "";
  if (!p) return "";
  const shown = providerDisplay(p);
  if (shown !== p) return safeDetailValue(shown);
  if (/^fleet-/.test(p) || p === "custom" || p === "ollama") return "your model server";
  return "";
}

/** A model id as the menu says it ("claude-sonnet-4-5-20250929" → "Sonnet
 *  4.5"); an id the names table does not know is shown only when it passes
 *  the safe-value check (a long dated id reads as a token and is left out). */
function modelDetail(model: unknown): string {
  return typeof model === "string" ? safeDetailValue(friendlyModelName(model)) : "";
}

/** A work status or ledger verdict in the ONE status vocabulary
 *  (lib/statusWords: "Part failed", never "completed with failures" — the
 *  v1.351.0 rule that every surface reads the table). */
function statusDetail(v: unknown): string {
  if (typeof v !== "string" || !v.trim()) return "";
  return safeDetailValue(outcomeWords(v)?.word ?? statusWords(v).word);
}

/** A webhook refusal's reason, said plainly (daemon/routes/comm.py's
 *  `rejected` values). An unknown reason is shown as the words it already
 *  is ("bad_signature" → "bad signature"). */
const WEBHOOK_REASONS: Record<string, string> = {
  unknown_slug: "unknown address",
  bad_signature: "bad signature",
  missing_signature: "no signature",
  invalid_signature: "bad signature",
  disabled: "turned off",
  rate_limited: "too many at once",
};

function words(v: unknown): string {
  return safeDetailValue(typeof v === "string" ? v.replace(/_/g, " ") : v);
}

function pick(payload: Record<string, unknown>, ...keys: string[]): string {
  for (const k of keys) {
    const v = safeDetailValue(payload[k]);
    if (v) return v;
  }
  return "";
}

function count(v: unknown, one: string, many: string): string {
  if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return "";
  return `${v} ${v === 1 ? one : many}`;
}

function toolDetail(payload: Record<string, unknown>): string {
  const tool = typeof payload.tool === "string" ? payload.tool : "";
  if (!tool) return "";
  const said = toolWords(tool);
  if (said) return said;
  if (tool.startsWith("mcp__")) return "an app's tool";
  return "";
}

/** The parts of the payload a person may read on the headline, per type.
 *  Every value goes through `safeDetailValue`; a type not listed here adds
 *  nothing (the headline alone is still words). */
export function eventDetail(type: string, payload: Record<string, unknown> | null | undefined): string[] {
  const p = payload && typeof payload === "object" ? payload : {};
  const parts: string[] = [];
  const add = (s: string) => {
    if (s && !parts.includes(s)) parts.push(s);
  };
  switch (type) {
    case "webhook.rejected": {
      add(pick(p, "slug"));
      const reason = typeof p.reason === "string" ? p.reason.trim() : "";
      add(WEBHOOK_REASONS[reason] ?? words(reason));
      break;
    }
    case "webhook.received":
      add(pick(p, "slug"));
      break;
    case "tool.executed":
    case "tool.denied":
    case "trust.lowered":
    case "grant.created":
    case "grant.revoked":
    case "interrupt.asked":
    case "interrupt.answered":
    case "approval.requested":
    case "approval.resolved":
      add(toolDetail(p));
      if (type === "trust.lowered") add(words(p.category));
      if (type === "interrupt.answered" || type === "approval.resolved") add(words(p.decision));
      break;
    case "session.created":
    case "session.completed":
    case "agent.started":
    case "agent.completed":
    case "delegation.started":
    case "delegation.completed":
    case "session.queued":
      add(pick(p, "task", "title"));
      if (type === "delegation.started" || type === "delegation.completed") add(pick(p, "agent"));
      break;
    case "assignment.created":
    case "assignment.started":
    case "assignment.finished":
    case "assignment.blocked":
      add(pick(p, "title"));
      add(pick(p, "assignee"));
      if (type === "assignment.blocked") add(pick(p, "blocked_reason"));
      break;
    case "assignment.requeued":
    case "sessions.interrupted":
      add(count(p.count, "run", "runs"));
      break;
    case "runs.busy":
      add(count(p.running, "run", "runs"));
      break;
    case "agent.paused":
    case "agent.allowance_warning":
    case "coach.proposal":
    case "remote.message":
      add(pick(p, "name", "agent"));
      if (type === "agent.paused") add(pick(p, "reason"));
      break;
    case "workflow.completed":
    case "workflow.step_started":
    case "workflow.step_completed":
    case "workflow.waiting":
      add(pick(p, "workflow"));
      add(pick(p, "step"));
      if (type === "workflow.completed" || type === "workflow.step_completed") add(statusDetail(p.status));
      break;
    case "reflex.fired":
    case "reflex.failed":
      add(pick(p, "rule"));
      if (type === "reflex.failed") add(pick(p, "reason"));
      break;
    case "schedule.fired":
      add(pick(p, "name", "schedule"));
      break;
    case "provider.failed":
    case "provider.failover":
    case "provider.downgraded":
    case "provider.routed":
      add(providerDetail(p.provider));
      if (type === "provider.routed") add(modelDetail(p.model));
      if (type === "provider.failover") add(words(p.why));
      break;
    case "mcp.pack_failed":
      add(pick(p, "name"));
      add(pick(p, "reason"));
      break;
    case "mcp.loaded":
      add(count(p.tools, "tool", "tools"));
      break;
    case "media.completed":
    case "media.failed":
      add(words(p.kind));
      break;
    case "goal.created":
    case "goal.iteration_started":
    case "goal.iteration_completed":
    case "goal.iteration_refused":
    case "goal.satisfied":
    case "goal.tripped":
    case "goal.stopped":
      add(pick(p, "name"));
      if (type === "goal.tripped" || type === "goal.iteration_refused") add(pick(p, "reason"));
      break;
    case "build.account_limit":
    case "build.resumed":
    case "build.turn_finished":
    case "build.turn_stalled":
    case "build.ci_step":
    case "build.ci_done":
    case "chat.turn_finished":
      add(pick(p, "title"));
      if (type === "build.account_limit") add(pick(p, "account_name"));
      if (type === "build.turn_finished" || type === "chat.turn_finished") add(statusDetail(p.outcome));
      break;
    case "build.rewound":
      add(count(p.turns, "message", "messages"));
      add(count(p.files, "file", "files"));
      break;
    case "context.blocked":
      add(pick(p, "source"));
      add(count(p.count, "piece", "pieces"));
      break;
    case "context.trimmed":
      add(count(p.dropped_blocks, "block left out", "blocks left out"));
      break;
    case "detection.finding":
      add(pick(p, "rule", "title"));
      add(words(p.severity));
      break;
    case "comm.received":
    case "comm.rejected":
    case "comm.dropped":
    case "slack.event":
      add(pick(p, "channel", "channel_name"));
      break;
    case "config.changed": {
      const keys = Array.isArray(p.keys) ? p.keys.length : 0;
      add(count(keys, "setting", "settings"));
      break;
    }
    case "skill.proposal_created":
      add(pick(p, "skill_name"));
      break;
    case "library.proposal":
      add(pick(p, "folder_name"));
      break;
    case "batch.file_done":
      add(statusDetail(p.status));
      break;
    case "computeruse.run_finished":
      add(statusDetail(p.status));
      break;
    case "preference.suggested":
    case "preference.kept":
    case "preference.declined":
      add(pick(p, "text"));
      break;
    case "desktop.incident":
      add(words(p.kind));
      break;
    case "envelope.adapted":
    case "envelope.probe_started":
    case "envelope.probe_completed":
      add(modelDetail(p.model));
      break;
    default:
      break;
  }
  return parts;
}

/** The whole headline for a feed row: the event's words, then the payload's
 *  detail — "A webhook was refused · github-push · bad signature". An
 *  unknown type (no words) answers "" so the caller falls back to the
 *  summary as before. */
export function eventHeadline(
  type: string | null | undefined,
  payload: Record<string, unknown> | null | undefined,
): string {
  const t = (type ?? "").trim();
  const base = t ? EVENT_WORDS[t] : "";
  if (!base) return "";
  const detail = eventDetail(t, payload);
  return detail.length ? `${base} · ${detail.join(" · ")}` : base;
}

/** A feed row's headline (components/TimeTravelFeed): the summary's own
 *  words when the ledger already wrote them as a sentence (a token row
 *  "Demo model · 120+40 tok", an agent state change), else the event's words
 *  with its payload detail, else the summary in words as before. */
export function rowHeadline(e: {
  type?: string | null;
  summary?: string | null;
  payload?: Record<string, unknown> | null;
}): string {
  const s = (e.summary ?? "").trim();
  if (s && (TOKEN_SUMMARY_RE.test(s) || AGENT_STATE_RE.test(s))) return humanSummary(s);
  return eventHeadline(e.type, e.payload) || humanSummary(s);
}
