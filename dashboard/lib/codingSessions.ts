/**
 * A project's CODING SESSIONS (v1.414.0) — from both Code views: Chat › Code
 * (Iron Jarvis's own harness, `/chat?view=code&session=<id>`) and Build ›
 * Code (the user's real Claude Code / Codex, `/terminals?view=code&code=<id>`).
 *
 * The daemon finds them by FOLDER at read time (projects/coding_sessions:
 * the session's folder under the project's root, the most specific ACTIVE
 * project winning) and sends them on `GET /projects/{id}` and
 * `GET /projects/{id}/world` as `coding_sessions`. This module decodes them
 * (WHITELIST — an unknown view or a row with no id dies) and says each one's
 * state in words. The link is built HERE by each view's own producer
 * (`buildSessionHref`, `buildCodeHref`), never copied from the wire.
 */

import { buildCodeHref } from "./buildApp";
import { buildSessionHref } from "./buildLinks";

export type CodingView = "chat_code" | "build_code";
export type CodingState = "working" | "waiting" | "finished" | "idle";

export interface ProjectCodingSession {
  view: CodingView;
  id: string;
  title: string;
  /** Build › Code: the tool it drives ("Claude Code"); "" for Chat › Code. */
  tool: string;
  state: CodingState;
  /** On a finished reply: "completed" | "failed" | "cancelled". */
  outcome: string;
  updatedAt: string;
}

const STATES: readonly CodingState[] = ["working", "waiting", "finished", "idle"];

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** The rows of `coding_sessions`, whitelisted; [] for anything else. */
export function decodeCodingSessions(raw: unknown): ProjectCodingSession[] {
  if (!Array.isArray(raw)) return [];
  const out: ProjectCodingSession[] = [];
  for (const r of raw) {
    if (!r || typeof r !== "object" || Array.isArray(r)) continue;
    const o = r as Record<string, unknown>;
    const view = o.view === "chat_code" || o.view === "build_code" ? o.view : null;
    const id = str(o.id).trim();
    if (!view || !id) continue;
    const state = STATES.includes(o.state as CodingState) ? (o.state as CodingState) : "idle";
    out.push({
      view,
      id,
      title: str(o.title).trim(),
      tool: str(o.tool).trim(),
      state,
      outcome: str(o.outcome),
      updatedAt: str(o.updated_at),
    });
  }
  return out;
}

/** Where a coding session opens: its own view, by that view's producer. */
export function codingSessionHref(c: Pick<ProjectCodingSession, "view" | "id">): string {
  return c.view === "build_code" ? buildCodeHref(c.id) : buildSessionHref(c.id);
}

/** The state in a few words. */
export function codingStateWords(c: Pick<ProjectCodingSession, "state" | "outcome">): string {
  switch (c.state) {
    case "working":
      return "Working";
    case "waiting":
      return "Waiting for your OK";
    case "finished":
      if (c.outcome === "failed") return "Failed";
      if (c.outcome === "cancelled") return "Stopped";
      return "Finished";
    default:
      return "No reply yet";
  }
}
