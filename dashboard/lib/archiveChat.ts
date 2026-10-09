// Archive a chat, and stop it first when it is still working (v1.328.0, calm
// chat W3-3; the daemon side is B7).
//
// POST /chat/threads/{id}/archive answers 409 with `running` (plain words
// such as "a reply in progress") while the chat has work in flight, and
// nothing changes. The shared `post` helper keeps only `detail` from an error
// body, so this one request is made here, where the 409's list can be read:
// the page shows it in ArchiveChatDialog and, on confirm, sends {stop: true}.
// A stop that could not reach everything still archives, and says what is
// still finishing (`still_running` + `note`); that is never called stopped.
//
// Kept in its own module on purpose: tests that mock "@/lib/api" have fixed
// export lists, and the page tests can stub this one request on its own.

import { API_BASE, ApiError, ijToken, post } from "@/lib/api";

/** The archived chats, newest first (the daemon caps the list at 100). */
export const ARCHIVED_THREADS_PATH = "/chat/threads?archived=only";

export type ArchiveOutcome =
  /** Archived. `stillRunning` names what could not be stopped from here. */
  | { kind: "archived"; stillRunning: string[]; note: string | null }
  /** Not archived: the chat is still working (the daemon's 409). */
  | { kind: "busy"; running: string[]; detail: string };

/** The daemon's `detail` as one sentence (a list detail is joined). Not
 *  lib/api's flattenDetail: page tests mock "@/lib/api" with a fixed export
 *  list that does not carry it. */
function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join("; ");
  }
  return String(detail);
}

function words(v: unknown): string[] {
  return Array.isArray(v)
    ? v.filter((x): x is string => typeof x === "string" && x.trim() !== "")
    : [];
}

/** The sentence shown when work kept going after the archive. */
export function stillRunningNote(stillRunning: string[], note: string | null): string | null {
  if (stillRunning.length === 0) return null;
  const said = (note || "").trim();
  return said || `Archived. This will finish on its own: ${stillRunning.join(", ")}.`;
}

/**
 * Archive chat `id`. `stop` = stop what it is running first (the dialog's
 * "Stop and archive"). Resolves to "busy" on the 409 instead of throwing, so
 * the caller can ask; any other failure throws an ApiError (status 0 = the
 * daemon could not be reached), like every other request on the page.
 */
export async function archiveChat(id: string, stop = false): Promise<ArchiveOutcome> {
  const token = ijToken();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/chat/threads/${encodeURIComponent(id)}/archive`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(stop ? { stop: true } : {}),
    });
  } catch {
    throw new ApiError("daemon offline", 0);
  }
  let body: Record<string, unknown> = {};
  try {
    const parsed = (await res.json()) as unknown;
    if (parsed && typeof parsed === "object") body = parsed as Record<string, unknown>;
  } catch {
    /* no body */
  }
  if (res.status === 409) {
    const running = words(body.running);
    const detail = body.detail ? detailText(body.detail) : "This chat is still working.";
    return { kind: "busy", running, detail };
  }
  if (!res.ok) {
    const detail = body.detail
      ? detailText(body.detail)
      : `${res.status} ${res.statusText || ""}`.trim();
    throw new ApiError(detail, res.status);
  }
  const stillRunning = words(body.still_running);
  const note = typeof body.note === "string" ? body.note : null;
  return { kind: "archived", stillRunning, note };
}

/** Bring an archived chat back into the chat list. */
export async function unarchiveChat(id: string): Promise<void> {
  await post(`/chat/threads/${encodeURIComponent(id)}/unarchive`);
}
