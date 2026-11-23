// Rules (Reflex) — the words and readings the Rules page shares (v1.449.0,
// Wave D item 34). Pure: no React, no fetch.

export type ReflexSource = "webhook" | "comm" | "email" | "calendar" | "slack" | "file" | "download";
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
