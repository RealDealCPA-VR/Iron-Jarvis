/**
 * WHERE A BUILD SESSION OPENS (v1.369.0, IJ harness Phase 6).
 *
 * An IJ harness session's tool calls, findings and events carry the session
 * id `build:<thread id>` (daemon harness/sessions `session_id_for`). Before
 * this file every row that held one linked `/sessions/build:<id>` — an Agents
 * run page that does not exist for it (a dead link), and the waiting-for-you
 * row of a Build session read "A chat" and opened `/chat?thread=`, where a
 * session never shows. One rule now: a Build session opens in Build, on THAT
 * session (`/terminals?session=<id>`, the stable address Build reads).
 *
 * IMPORT-FREE on purpose: the bell is on every page, and ~71 test files mock
 * lib/api wholesale — a helper living beside an api import would vanish under
 * them (lib/missionLinks's rule). (lib/buildWords is import-free too, so its
 * words may be read here.)
 */

import { CODING_SESSION } from "./buildWords";

/** The process / tool session id prefix of a Build session's calls. */
export const BUILD_SESSION_PREFIX = "build:";

/** Where a Build session opens: Build, on THAT session. */
export function buildSessionHref(threadId: string): string {
  return `/terminals?session=${encodeURIComponent(threadId)}`;
}

/** v1.369.0 review: a link to a session clicked while Build is ALREADY open
 *  (the bell's rows are on every page). Next keeps the page's state on a
 *  same-page navigation that only changes the query, so the page never
 *  re-reads `?session=` — the address changed and the screen did not. The
 *  link's click announces the session with this window event; Build (the
 *  page, and the IJ harness view when it is showing) opens it. */
export const BUILD_SESSION_EVENT = "ij:open-build-session";

/** The session a Build link names (`/terminals?session=<id>`), else null. */
export function sessionOfHref(href: string | null | undefined): string | null {
  const h = typeof href === "string" ? href.trim() : "";
  const q = h.indexOf("?");
  if (q < 0 || h.slice(0, q).replace(/\/+$/, "") !== "/terminals") return null;
  try {
    const id = new URLSearchParams(h.slice(q + 1)).get("session");
    return id && id.trim() ? id.trim() : null;
  } catch {
    return null;
  }
}

/** Tell an open Build page that a link to one of its sessions was pressed.
 *  Only on Build (anywhere else the navigation itself opens it). Answers
 *  whether it was announced. */
export function announceBuildSessionLink(href: string | null | undefined): boolean {
  if (typeof window === "undefined") return false;
  const id = sessionOfHref(href);
  if (!id) return false;
  if (window.location.pathname.replace(/\/+$/, "") !== "/terminals") return false;
  window.dispatchEvent(new CustomEvent(BUILD_SESSION_EVENT, { detail: { id } }));
  return true;
}

/** Harness gaps (fix 2): the same trap for a link to a TERMINAL
 *  (`/terminals?focus=<id>`). The one Sign in press now stands under a
 *  failed coding-session reply — ON Build — and its "Finish signing in
 *  there" link changed only the address: Build neither showed the new
 *  terminal (its list is read once) nor left the coding sessions view. The
 *  link's click announces the terminal with this event; Build adds it to
 *  its list and shows it in the Terminals view. */
export const BUILD_TERMINAL_EVENT = "ij:open-build-terminal";

/** The terminal a Build link names (`/terminals?focus=<id>`), else null. */
export function terminalOfHref(href: string | null | undefined): string | null {
  const h = typeof href === "string" ? href.trim() : "";
  const q = h.indexOf("?");
  if (q < 0 || h.slice(0, q).replace(/\/+$/, "") !== "/terminals") return null;
  try {
    const id = new URLSearchParams(h.slice(q + 1)).get("focus");
    return id && id.trim() ? id.trim() : null;
  } catch {
    return null;
  }
}

/** Tell an open Build page that a link to one of its terminals was pressed.
 *  Only on Build (anywhere else the navigation itself opens it). */
export function announceBuildTerminalLink(href: string | null | undefined): boolean {
  if (typeof window === "undefined") return false;
  const id = terminalOfHref(href);
  if (!id) return false;
  if (window.location.pathname.replace(/\/+$/, "") !== "/terminals") return false;
  window.dispatchEvent(new CustomEvent(BUILD_TERMINAL_EVENT, { detail: { id } }));
  return true;
}

/** The session id a BUILD_SESSION_EVENT carries, else null. */
export function sessionOfEvent(e: Event): string | null {
  const d = (e as CustomEvent<{ id?: unknown }>).detail;
  return d && typeof d.id === "string" && d.id.trim() ? d.id.trim() : null;
}

/** The session's thread id from a `build:<id>` session id, else null. */
export function buildThreadOf(sessionId: string | null | undefined): string | null {
  const s = typeof sessionId === "string" ? sessionId.trim() : "";
  if (!s.startsWith(BUILD_SESSION_PREFIX)) return null;
  const id = s.slice(BUILD_SESSION_PREFIX.length);
  return id ? id : null;
}

/** True for a `build:<id>` session id. */
export function isBuildSessionId(sessionId: string | null | undefined): boolean {
  return buildThreadOf(sessionId) !== null;
}

/** Where a row naming a session id opens: a Build session in Build, any
 *  other session on its run page. */
export function sessionLinkFor(sessionId: string): string {
  const build = buildThreadOf(sessionId);
  return build ? buildSessionHref(build) : `/sessions/${encodeURIComponent(sessionId)}`;
}

/** The bell's headline for a Build session's account that could not serve
 *  (`build.account_limit`, Phase 5): a limit, or — not a reset — an account
 *  that must sign in again, was removed, or is turned off. */
export function buildLimitHeadline(kind: unknown, accountName: unknown): string {
  const name = typeof accountName === "string" && accountName.trim() ? accountName.trim() : "";
  const who = name ? `The ${name} account` : "A Build session's account";
  if (kind === "signin") return `${who} needs to sign in again`;
  if (kind === "missing") return name ? `The ${name} account was removed` : "A Build session's account was removed";
  if (kind === "disabled") return `${who} is turned off`;
  if (kind === "busy") return `${who} is busy right now`;
  return `${who} reached its limit`;
}

/** v1.393.0 (long jobs, Phase 4): the bell's headline when a coding reply
 *  carried on with ANOTHER account of its group — the reply line's own words
 *  (lib/buildAccounts accountMoveLine). */
export function buildMovedHeadline(fromName: string, toName: string): string {
  return `${fromName} reached its limit; carried on with ${toName} (same group)`;
}

/** v1.393.0: the body of a limit row whose reply waits by itself — the
 *  session's title and when it carries on ("" parts dropped). */
export function buildLimitWaitingBody(title: unknown, resetsAt: unknown): string {
  const t = typeof title === "string" ? title.trim() : "";
  const clipped = t.length > 100 ? `${t.slice(0, 99)}…` : t;
  const at = typeof resetsAt === "string" ? new Date(resetsAt) : null;
  const when =
    at && !Number.isNaN(at.getTime())
      ? `it carries on by itself after the reset at ${shortTime(at)}`
      : "it carries on by itself";
  return [clipped, when].filter((b) => b).join(" · ");
}

/** "4:12 PM", with the day when it is not today (lib/ironProxy localTime's
 *  words, kept here so this file stays import-free). */
function shortTime(d: Date, now: Date = new Date()): string {
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  return `${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}, ${time}`;
}

/** How long a Build turn took, in words ("took 3 min 12 s"), or "" — the
 *  twin of desktop/main.js `runOutcomeBuildTook` and comm/notifier
 *  `build_took_words` (one fixture: one-notice-v13600). */
export function buildTookWords(seconds: unknown): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
  const whole = Math.round(seconds);
  if (whole < 60) return `took ${whole} s`;
  const min = Math.floor(whole / 60);
  const rest = whole % 60;
  return rest ? `took ${min} min ${rest} s` : `took ${min} min`;
}

/** The bell's headline for `build.turn_finished` (v1.374.0): a Build turn
 *  that ran longer than a minute ended. */
export function buildFinishedHeadline(outcome: unknown): string {
  return outcome === "failed" ? "Your Build session could not finish" : "Your Build session finished";
}

/** Its line under the headline: the session's title and how long it took
 *  ("" when neither is known). */
export function buildFinishedBody(title: unknown, seconds: unknown): string {
  const t = typeof title === "string" ? title.trim() : "";
  const clipped = t.length > 120 ? `${t.slice(0, 119)}…` : t;
  return [clipped, buildTookWords(seconds)].filter((b) => b).join(" · ");
}

/** v1.392.0 (long jobs, Phase 3): the bell's headline for
 *  `build.turn_stalled` — a Build reply that made no progress for ten
 *  minutes (once per episode). The twin of comm/notifier
 *  `BUILD_STALLED_HEADLINE` and desktop/main.js (one fixture:
 *  one-notice-v13600). v1.392.0 review: a NEW sentence, so it names the
 *  session the user's way (lib/buildWords, v1.389.0) — never "Build
 *  session". */
export const BUILD_STALLED_HEADLINE = `Your ${CODING_SESSION} has stopped making progress`;

/** Its line: the session's title and the live line's own sentence ("No
 *  progress for 10 minutes — waiting on …"), "" when neither is known. */
export function buildStalledBody(title: unknown, words: unknown): string {
  const t = typeof title === "string" ? title.trim() : "";
  const clipped = t.length > 120 ? `${t.slice(0, 119)}…` : t;
  const w = typeof words === "string" ? words.trim() : "";
  return [clipped, w].filter((b) => b).join(" · ");
}
