/**
 * BEFORE AND AFTER PICTURES under a coding reply (R2.15, phase 27-a; the
 * daemon's `harness/evidence`) — the pure half.
 *
 * A reply line that changed a screen file carries `evidence`:
 * `{kind: "pictures", screen: true, items: [{id, label, where, at, sha}],
 * note, beforeNote?, folder?}` (whitelisted here, the daemon's twin is
 * `evidence.saved_record`). No items: the reply is only MARKED, and the
 * screen offers Show me (decision D-27.1: pictures only when you ask).
 *
 * "show me it works" typed alone in the session presses the same Show me as
 * the button under the newest reply, when that reply changed a screen and has
 * no after picture (`isShowMeOnly`, `showMeReply`, `requestShowMe`) — one
 * press path, so the offer of the automatic setting is
 * made in one place. Typed with a task ("make it blue and show me it works"),
 * it is an ordinary message: the daemon reads the words and takes the before
 * and after itself.
 */

import { API_BASE, ijToken } from "@/lib/api";

export type ReplyPictureId = "before" | "after";

export interface ReplyPicture {
  id: ReplyPictureId;
  label: string;
  /** host[:port] and path, never the query (the daemon's own reading). */
  where: string;
  /** When it was taken (ISO, UTC). */
  at: string;
  /** The first 16 hex of the picture's hash. */
  sha: string;
}

export interface ReplyPictures {
  items: ReplyPicture[];
  note: string;
  /** The app's own folder the pictures are kept in ("" when none yet). */
  folder: string;
}

// ---- the words (VOCABULARY.md: before and after pictures, Show me) ----
export const SHOW_ME = "Show me";
export const SHOW_ME_TITLE = "Take a picture of the page open in the Browser now, to see how this change looks";
export const SHOW_ME_WORKING = "Taking the picture…";
export const OFFER_WORDS =
  "Take these pictures by themselves after every reply that changes a screen? Each such reply starts an invisible browser on this PC twice.";
export const OFFER_YES = "Turn on";
export const OFFER_NO = "Not now";
export const OFFER_ON_WORDS = "Turned on. Settings can turn it off.";
export const OFFER_OFF_WORDS = "Not turned on. Show me is always here.";
export const GONE_WORDS = "This picture is no longer kept.";
export const STRIP_LABEL = "Before and after pictures of the page";

const IDS: readonly ReplyPictureId[] = ["before", "after"];

function str(v: unknown, cap: number): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

/** A reply line's `evidence`, whitelisted; null unless it changed a screen. */
export function decodePictures(raw: unknown): ReplyPictures | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  if (r.kind !== "pictures" || r.screen !== true) return null;
  const items: ReplyPicture[] = [];
  for (const it of Array.isArray(r.items) ? r.items : []) {
    if (!it || typeof it !== "object") continue;
    const o = it as Record<string, unknown>;
    const id = o.id as ReplyPictureId;
    if (!IDS.includes(id) || items.some((x) => x.id === id)) continue;
    items.push({
      id,
      label: id === "before" ? "Before" : "After",
      where: str(o.where, 200),
      at: str(o.at, 40),
      sha: str(o.sha, 16).replace(/[^0-9a-f]/gi, ""),
    });
  }
  items.sort((a, b) => IDS.indexOf(a.id) - IDS.indexOf(b.id));
  return { items, note: str(r.note, 600), folder: str(r.folder, 500) };
}

/** The address an `<img>` loads a kept picture from (the token rides the
 *  query: an `<img>` sends no Authorization header; the hash keeps a new
 *  after from showing an old one). */
export function pictureSrc(threadId: string, turnId: string, item: ReplyPicture): string {
  const token = ijToken();
  return `${API_BASE}/build/sessions/${encodeURIComponent(threadId)}/evidence/${encodeURIComponent(turnId)}/${item.id}?v=${encodeURIComponent(item.sha)}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
}

/** The press: `POST` this with `{turn_id, preview_url}`. */
export function showMePath(threadId: string): string {
  return `/build/sessions/${encodeURIComponent(threadId)}/evidence`;
}

/** "11:42" — when a picture was taken, in this PC's own clock. */
export function pictureTime(at: string): string {
  const t = Date.parse(at && !/[zZ]|[+-]\d\d:?\d\d$/.test(at) ? `${at}Z` : at);
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The words typed alone that press Show me (the daemon reads the same
 *  phrases inside a longer message: `harness/evidence.asks`). */
const SHOW_ME_ONLY = /^(?:please\s+)?show\s+me(?:\s+that)?\s+it\s+works(?:\s+please)?$|^(?:please\s+)?show\s+me(?:\s+the)?\s+before\s+and\s+after(?:\s+please)?$/;

export function isShowMeOnly(text: string): boolean {
  const flat = (text || "")
    .toLowerCase()
    .replace(/[.!?,;:"'’]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return flat.length > 0 && flat.length <= 60 && SHOW_ME_ONLY.test(flat);
}

/** Is Show me offered under a reply? Only under the NEWEST reply (the page
 *  is pictured as it is now: under an earlier reply it would show the later
 *  replies' changes as that reply's own), only while the reply has no after
 *  picture (the one it has shows the page as the reply left it, and is
 *  kept), never in an archived session. The daemon refuses the same
 *  (`routes/build_evidence.not_offered`). */
export function showMeOffered(rec: ReplyPictures, at: { newest: boolean; archived: boolean }): boolean {
  return at.newest && !at.archived && !rec.items.some((it) => it.id === "after");
}

/** The turn id of the reply "show me it works" typed alone presses: the
 *  reply that ENDS the conversation (the daemon's own hidden "carry on"
 *  questions aside), when it changed a screen and has no after picture yet;
 *  null otherwise (the words are then an ordinary message). */
export function showMeReply(
  messages: readonly { role?: string; turnId?: string; evidence?: unknown; continuation?: unknown }[],
): string | null {
  const shown = messages.filter((m) => m.continuation !== true);
  const last = shown[shown.length - 1];
  if (!last || last.role !== "assistant" || !last.turnId) return null;
  const rec = decodePictures(last.evidence);
  return rec && showMeOffered(rec, { newest: true, archived: false }) ? last.turnId : null;
}

// ---- one press path: the typed words press the row's own Show me ----
const presses = new Map<string, () => void>();

function pressKey(threadId: string, turnId: string): string {
  return `${threadId}\u0000${turnId}`;
}

/** A reply's Show me listens for the typed words (returns the unsubscribe). */
export function onShowMe(threadId: string, turnId: string, press: () => void): () => void {
  const key = pressKey(threadId, turnId);
  presses.set(key, press);
  return () => {
    if (presses.get(key) === press) presses.delete(key);
  };
}

/** Press that reply's Show me; false when it is not on screen. */
export function requestShowMe(threadId: string, turnId: string): boolean {
  const press = presses.get(pressKey(threadId, turnId));
  if (!press) return false;
  press();
  return true;
}
