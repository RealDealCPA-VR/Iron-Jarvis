/**
 * "GIVE THIS TO THE TEAM AS AN OBJECTIVE" (v1.455.0, readiness Wave G,
 * item 26).
 *
 * A plain chat reply to a long request of several separate pieces of work
 * carries ONE door to the Agents page's objective composer (the daemon's
 * `tools/autoselect.mission_offer`, wired into both chat lanes). Pressing it
 * opens the composer with the user's request already in the box — nothing
 * starts until the user presses Start there.
 *
 * THE CHANNEL is lib/codingStart's: the request is the user's own words, so
 * it never rides an address; the press puts it in this tab's sessionStorage
 * and the composer takes it once, when it opens. A record older than
 * {@link MISSION_START_TTL_MS} is ignored. Never staged from a Local only
 * chat (an objective runs on the default model; the daemon offers no door
 * there either).
 *
 * Import-free (dozens of test files mock lib/api wholesale).
 */

import { requestBefore, type RequestMessage } from "@/lib/codingStart";

/** The team offer's own address — `tools/autoselect.MISSION_OFFER_HREF`
 *  (tests/test_chat_finds_the_right_thing_v14550 holds the two equal). */
export const MISSION_OFFER_HREF = "/agents?from=chat";

/** sessionStorage key: the request waiting for the objective composer. */
export const MISSION_START_KEY = "ij.agents.startRequest";

/** How long a staged request waits for the composer to open. */
export const MISSION_START_TTL_MS = 5 * 60_000;

/** The longest request carried. */
export const MISSION_START_MAX_CHARS = 20_000;

/** Is `href` the team offer's door? */
export function isMissionOffer(href: string | null | undefined): boolean {
  return typeof href === "string" && href.trim() === MISSION_OFFER_HREF;
}

/** Put a request where the composer takes it. Never throws. */
export function stageMissionStart(text: string, now: number = Date.now()): void {
  try {
    const t = String(text ?? "").slice(0, MISSION_START_MAX_CHARS);
    if (!t.trim()) return;
    window.sessionStorage.setItem(MISSION_START_KEY, JSON.stringify({ text: t, at: now }));
  } catch {
    /* storage blocked: the composer opens empty */
  }
}

/** The staged request, or "" (none, too old, or unreadable); TAKEN — read
 *  once, then forgotten. */
export function takeMissionStart(now: number = Date.now()): string {
  try {
    const raw = window.sessionStorage.getItem(MISSION_START_KEY);
    if (!raw) return "";
    window.sessionStorage.removeItem(MISSION_START_KEY);
    const r = JSON.parse(raw) as { text?: unknown; at?: unknown };
    const at = typeof r.at === "number" && Number.isFinite(r.at) ? r.at : 0;
    if (!at || now - at > MISSION_START_TTL_MS || at - now > 60_000) return "";
    return typeof r.text === "string" ? r.text.slice(0, MISSION_START_MAX_CHARS) : "";
  } catch {
    return "";
  }
}

/** The team door under reply `index` was pressed: stage the request it
 *  answers. Answers false always — the door's own link then opens the Agents
 *  page (a different route, so a plain navigation is right). A Local only
 *  chat stages nothing. */
export function startMissionFromDoor(
  messages: readonly RequestMessage[],
  index: number,
  href: string,
  localOnly: boolean = false,
): boolean {
  if (!isMissionOffer(href) || localOnly) return false;
  stageMissionStart(requestBefore(messages, index));
  return false;
}
