// Rules (Reflex) — the words and readings the Rules page shares (v1.449.0,
// Wave D item 34). Pure: no React, no fetch.

export type ReflexSource = "webhook" | "comm" | "email" | "calendar" | "slack" | "file" | "download" | "run_ended";
export type ReflexAction = "workflow" | "remote_agent" | "session";

/** The everyday rule: a message arrives (from one of these) → an agent does a
 *  task. The form opens on this shape; the other sources and actions sit
 *  behind "Another kind of rule". */
export const EVERYDAY_SOURCES: readonly ReflexSource[] = ["comm", "email", "slack"];
export const EVERYDAY_ACTION: ReflexAction = "session";

/** Is a stored rule the everyday shape (so its Edit opens on the short form)? */
export function isEveryday(r: { source: string; action: string }): boolean {
  return (EVERYDAY_SOURCES as readonly string[]).includes(r.source) && r.action === EVERYDAY_ACTION;
}

/* ---- GET /triggers: is a message source listening? --------------------- */

/** GET /triggers — world-trigger status. Tolerate missing fields.
 *  v1.439.0: the daemon answers `{triggers: {email, calendar, slack}}`
 *  (routes/triggers.py `_status`), and email/Slack say `enabled` +
 *  `credentialed`, never `active`; `triggersOf` reads both shapes. */
export interface TriggerSource {
  active?: boolean;
  enabled?: boolean;
  credentialed?: boolean;
}
export interface TriggersParts {
  email?: TriggerSource;
  calendar?: { enabled?: boolean; has_url?: boolean; lead_minutes?: number };
  slack?: TriggerSource;
}
export interface TriggersStatus extends TriggersParts {
  triggers?: TriggersParts;
}

export function triggersOf(data: TriggersStatus | null | undefined): TriggersParts {
  if (!data) return {};
  return data.triggers && typeof data.triggers === "object" ? data.triggers : data;
}

/** A source can start work: it is on AND can receive (or an older shape's
 *  own `active`). */
export function sourceActive(s: TriggerSource | undefined): boolean {
  if (!s) return false;
  if (typeof s.active === "boolean") return s.active;
  return !!s.enabled && !!s.credentialed;
}

export const SLACK_OFF_WORDS = "Slack isn't listening — turn it on in Notifications.";
export const EMAIL_OFF_WORDS = "Email isn't listening — turn it on in Notifications.";
export const CALENDAR_OFF_WORDS = "The calendar isn't being read — turn it on under Triggers below.";

/** The line a rule's row says when its source cannot start it now, or "".
 *  `data` null/undefined (not read yet, or an older Iron Jarvis) says
 *  nothing: a guess must never read as "off". A message rule ("comm") and a
 *  webhook have no listening switch here. */
export function sourceOffWords(source: string, data: TriggersStatus | null | undefined): string {
  if (!data) return "";
  const parts = triggersOf(data);
  if (source === "slack") return parts.slack && sourceActive(parts.slack) ? "" : SLACK_OFF_WORDS;
  if (source === "email") return parts.email && sourceActive(parts.email) ? "" : EMAIL_OFF_WORDS;
  if (source === "calendar") {
    const c = parts.calendar;
    return c && c.enabled && c.has_url ? "" : CALENDAR_OFF_WORDS;
  }
  return "";
}

/** Where the off line's fix lives. */
export function sourceOffHref(source: string): string {
  return source === "calendar" ? "#reflex-triggers" : "/channels";
}

/* ---- Leap 05-a: a rule that paused itself ------------------------------- */

/** The Rules page's two answers to a rule that paused itself (D-05.3). */
export const RESUME = "Resume";
export const KEEP_PAUSED = "Keep it paused";
/** What Keep it paused does, said under it. */
export const KEEP_PAUSED_WORDS = "Keep it paused turns the rule off until you turn it on again.";
/** What a paused file rule does with files that land meanwhile. */
export const PAUSED_FILES_WAIT = "New files wait and go once you resume it.";

/** A rule that paused itself (the daemon's `paused_at`), and its words. */
export function pausedWords(r: { paused_at?: string | null; paused_reason?: string | null }): string {
  if (!r.paused_at) return "";
  const why = typeof r.paused_reason === "string" && r.paused_reason.trim() ? r.paused_reason.trim() : "Paused.";
  return why;
}

/** Leap 05-a: a watcher that starts a rule says so on the Watchers page. */
export function startsRuleWords(name?: string | null): string {
  const n = typeof name === "string" ? name.trim() : "";
  return n ? `Starts the rule ${n}` : "Starts a rule";
}

/* ---- Leap 05-b: "when another task fails", and every start in one place -- */

/** Which kind of task a "when another task fails" rule listens for. */
export type EndedWhat = "schedule" | "workflow" | "rule" | "agent" | "any";
/** How that task must have ended. */
export type EndedOutcome = "failed" | "part_failed" | "needs_you" | "any";

/** The form's choices, in the words the page shows. Never "start" as a noun
 *  (the master plan's calm note for R2.9). */
export const ENDED_WHAT_CHOICES: readonly { value: EndedWhat; label: string }[] = [
  { value: "schedule", label: "One of your schedules" },
  { value: "workflow", label: "A workflow" },
  { value: "rule", label: "A rule's run" },
  { value: "agent", label: "An agent run" },
  { value: "any", label: "Any other task" },
];
export const ENDED_OUTCOME_CHOICES: readonly { value: EndedOutcome; label: string }[] = [
  { value: "failed", label: "Fails" },
  { value: "part_failed", label: "Finishes with part of it failed" },
  { value: "needs_you", label: "Ends needing you" },
  { value: "any", label: "Falls short in any way" },
];

/** The Rules page's second section: what runs at a time (your schedules). */
export const AT_A_TIME = "At a time";
/** Under its heading. */
export const AT_A_TIME_WORDS = "Your schedules. Each one opens on Schedules, where it is changed.";
/** The canvas trigger node's list of what already runs a workflow. */
export const RUNS_IT_NOW = "Runs it now";

/** One row of GET /starts: a schedule or a rule, read from its own store. */
export interface StartRow {
  id: string;
  kind: string;
  name: string;
  when_words: string;
  does_words: string;
  enabled: boolean;
  last_words: string;
  href: string;
  store: "schedule" | "rule";
}

function text(v: unknown, cap = 300): string {
  return typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, cap) : "";
}

/** An address the page may open: one of the app's own pages only. */
function ownHref(v: unknown): string {
  return typeof v === "string" && v.startsWith("/") && !v.startsWith("//") ? v : "";
}

/** GET /starts, decoded: a row with no id or name, or an address that is not
 *  one of the app's own pages, is dropped; 200 at most. */
export function decodeStarts(data: unknown): StartRow[] {
  const raw = data && typeof data === "object" ? (data as { starts?: unknown }).starts : null;
  if (!Array.isArray(raw)) return [];
  const out: StartRow[] = [];
  for (const r of raw.slice(0, 200)) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    const id = text(o.id, 100);
    const name = text(o.name, 120);
    const href = ownHref(o.href);
    const store = o.store === "schedule" ? "schedule" : o.store === "rule" ? "rule" : null;
    if (!id || !name || !href || !store) continue;
    out.push({
      id,
      kind: text(o.kind, 20),
      name,
      when_words: text(o.when_words),
      does_words: text(o.does_words),
      enabled: o.enabled !== false,
      last_words: text(o.last_words),
      href,
      store,
    });
  }
  return out;
}
