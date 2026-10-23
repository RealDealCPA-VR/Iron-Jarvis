/**
 * The AGENT'S BROWSER in a Build session (v1.376.0, IJ harness part 2,
 * Phase 5): the plain half of the Browser pane's "The agent is on …" block
 * (components/harness/BrowserPreview). The daemon keeps what the agent's own
 * invisible Edge last saw — where it was and a picture of the page —
 * (`GET /build/sessions/{id}/browser`, harness/page_browser); this file
 * decodes that answer and says it in words.
 *
 * Kept outside lib/api.ts (many tests mock that module wholesale).
 */

import { API_BASE, ijToken } from "@/lib/api";

/** The tools whose finished step means the agent's browser may show
 *  something new (the pane reads the view again). harness/page_act +
 *  harness/page_shot. */
export const PAGE_TOOLS: ReadonlySet<string> = new Set([
  "page_open",
  "page_click",
  "page_type",
  "page_read",
  "page_screenshot",
]);

export interface AgentBrowserView {
  /** The browser is open right now (false: it was closed — see `closedReason`). */
  open: boolean;
  /** Where it is: host, port and path ("localhost:5173/cart"); "" for a
   *  blank page or a page that did not load. */
  where: string;
  /** The raw address kind, for the words when `where` is empty. */
  blank: boolean;
  title: string;
  /** When the view was taken (ISO). */
  at: string;
  /** A picture is waiting at …/browser/shot. */
  shot: boolean;
  shotAt: string;
  /** Navigations the address rules stopped on the last action, in words. */
  note: string;
  closedReason: string;
}

function str(v: unknown, max = 600): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

/** The daemon's answer, whitelisted; null for "never opened a page" or a
 *  shape this window does not know. */
export function decodeAgentBrowserView(raw: unknown): AgentBrowserView | null {
  if (!raw || typeof raw !== "object") return null;
  const v = (raw as { view?: unknown }).view;
  if (!v || typeof v !== "object") return null;
  const r = v as Record<string, unknown>;
  return {
    open: r.open === true,
    where: str(r.where, 160),
    blank: r.blank === true,
    title: str(r.title, 200),
    at: str(r.at, 40),
    shot: r.shot === true,
    shotAt: str(r.shot_at, 40),
    note: str(r.note, 600),
    closedReason: str(r.closed_reason, 40),
  };
}

/** Where the agent is, as a place: "localhost:5173/cart", else "a blank
 *  page" / "a page that did not load". */
export function agentPlace(v: AgentBrowserView): string {
  if (v.where) return v.where;
  return v.blank ? "a blank page" : "a page that did not load";
}

/** The line at the top of the block. */
export function agentHeadline(v: AgentBrowserView): { lead: string; place: string } {
  return { lead: v.open ? "The agent is on" : "The agent was on", place: agentPlace(v) };
}

/** Why the agent's browser closed, in words ("" when it is open). */
export function closedWords(v: AgentBrowserView): string {
  if (v.open) return "";
  switch (v.closedReason) {
    case "stopped":
      return "Its browser closed when you pressed Stop.";
    case "idle":
      return "Its browser closed after a while without use.";
    case "shutdown":
      return "Its browser closed when Iron Jarvis stopped.";
    case "timed out":
      return "Its browser stopped answering, so it was closed.";
    default:
      return "Its browser is closed.";
  }
}

/** "9:41" — when the view was taken, in this PC's time. */
export function viewTime(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** The picture's address; `?at=` changes with each new picture so the
 *  browser fetches it again, `?token=` because an <img> cannot send an
 *  Authorization header (lib/mediaLightbox's rule). */
export function agentShotSrc(threadId: string, shotAt: string): string {
  const token = ijToken();
  const params = new URLSearchParams();
  if (shotAt) params.set("at", shotAt);
  if (token) params.set("token", token);
  const q = params.toString();
  return `${API_BASE}/build/sessions/${encodeURIComponent(threadId)}/browser/shot${q ? `?${q}` : ""}`;
}

/** How many of a turn's steps were the agent's browser and are finished —
 *  the pane reads the view again whenever this grows. */
export function finishedPageSteps(tools: ReadonlyArray<{ name: string; status: string }>): number {
  let n = 0;
  for (const t of tools) if (t.status === "done" && PAGE_TOOLS.has(t.name)) n += 1;
  return n;
}
