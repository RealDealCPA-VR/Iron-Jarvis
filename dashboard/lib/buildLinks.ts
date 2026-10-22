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
 * them (lib/missionLinks's rule).
 */

/** The process / tool session id prefix of a Build session's calls. */
export const BUILD_SESSION_PREFIX = "build:";

/** Where a Build session opens: Build, on THAT session. */
export function buildSessionHref(threadId: string): string {
  return `/terminals?session=${encodeURIComponent(threadId)}`;
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
