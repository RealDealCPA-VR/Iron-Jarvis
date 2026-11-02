/**
 * A REQUEST IS NEVER RETYPED (v1.426.0, Wave 6, E2 of the 2026-11-01 audit:
 * "chat's 'use a coding session' offer opens Build › Code empty: the request
 * is retyped").
 *
 * A plain chat reply to "build me a todo app" / "fix the failing test in my
 * repo" carries ONE door into a coding session (the daemon's
 * `tools/autoselect.coding_offer`, wired into both chat lanes). Pressing it
 * opens Chat › Code's start composer with the user's request already in the
 * box, and the chat's project folder chosen when the chat has one — nothing
 * is sent until the user presses Send there (the consent rule `/chat?ask=`
 * keeps for a question).
 *
 * THE CHANNEL: the request is the user's own words, which can be long and
 * private, so it never rides an address. The press puts it in this window's
 * sessionStorage (this tab only, gone when the tab closes) beside the door's
 * plain address, and the Code view takes it once, when it opens. A record
 * older than {@link CODING_START_TTL_MS} is ignored (a press that never
 * reached the Code view must not fill a later one).
 *
 * Import-free apart from the chat surface's announce (dozens of test files
 * mock lib/api wholesale).
 */

import { announceChatViewLink } from "@/lib/chatSurface";

/** The coding offer's own address — `tools/autoselect.CODING_OFFER_HREF`
 *  (tests/test_coding_offer_start_v14260 holds the two equal). */
export const CODING_OFFER_HREF = "/chat?view=code";

/** sessionStorage key: the request waiting for the Code view. */
export const CODING_START_KEY = "ij.code.startRequest";

/** How long a staged request waits for the Code view to open. */
export const CODING_START_TTL_MS = 5 * 60_000;

/** The longest request carried (the box holds what the chat sent). */
export const CODING_START_MAX_CHARS = 20_000;

/** What the start composer opens with. */
export interface CodingStart {
  /** The user's request, as they typed it in the chat. */
  text: string;
  /** The chat's project folder, or null (the composer starts with none). */
  folder: string | null;
}

/** Is `href` the coding offer's door? */
export function isCodingOffer(href: string | null | undefined): boolean {
  return typeof href === "string" && href.trim() === CODING_OFFER_HREF;
}

/** Put a request where the Code view takes it. Never throws (storage may be
 *  blocked: the Code view then opens empty, as before). */
export function stageCodingStart(start: CodingStart, now: number = Date.now()): void {
  try {
    const text = String(start.text ?? "").slice(0, CODING_START_MAX_CHARS);
    const folder = typeof start.folder === "string" && start.folder.trim() ? start.folder : null;
    window.sessionStorage.setItem(CODING_START_KEY, JSON.stringify({ text, folder, at: now }));
  } catch {
    /* storage blocked */
  }
}

/** The staged request, or null (none, too old, or unreadable). Reads only:
 *  {@link clearCodingStart} removes it once it is shown. */
export function peekCodingStart(now: number = Date.now()): CodingStart | null {
  try {
    const raw = window.sessionStorage.getItem(CODING_START_KEY);
    if (!raw) return null;
    const r = JSON.parse(raw) as { text?: unknown; folder?: unknown; at?: unknown };
    const at = typeof r.at === "number" && Number.isFinite(r.at) ? r.at : 0;
    if (!at || now - at > CODING_START_TTL_MS || at - now > 60_000) return null;
    const text = typeof r.text === "string" ? r.text.slice(0, CODING_START_MAX_CHARS) : "";
    const folder = typeof r.folder === "string" && r.folder.trim() ? r.folder : null;
    if (!text.trim() && !folder) return null;
    return { text, folder };
  } catch {
    return null;
  }
}

/** Forget the staged request. Never throws. */
export function clearCodingStart(): void {
  try {
    window.sessionStorage.removeItem(CODING_START_KEY);
  } catch {
    /* storage blocked */
  }
}

/** The fields of a chat message this reads (the chat page's own shape). */
export interface RequestMessage {
  role: string;
  content?: string;
  continuation?: boolean;
  steer?: boolean;
}

/** The user's request a reply at `index` answers: the nearest earlier
 *  message the USER typed (never the app's hidden "carry on" turn, never a
 *  note sent while it worked). "" when there is none. */
export function requestBefore(messages: readonly RequestMessage[], index: number): string {
  for (let i = Math.min(index, messages.length) - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role !== "user" || m.continuation === true || m.steer === true) continue;
    return typeof m.content === "string" ? m.content : "";
  }
  return "";
}

/** The coding door under reply `index` was pressed: stage the request (and
 *  the chat's project folder) and, on the chat page, switch to the Code view
 *  in place. Answers true when the press was handled here (the link's own
 *  navigation is then not needed); false for any other door. */
export function startCodingFromDoor(
  messages: readonly RequestMessage[],
  index: number,
  href: string,
  folder: string | null,
): boolean {
  if (!isCodingOffer(href)) return false;
  stageCodingStart({ text: requestBefore(messages, index), folder });
  return announceChatViewLink(href);
}
