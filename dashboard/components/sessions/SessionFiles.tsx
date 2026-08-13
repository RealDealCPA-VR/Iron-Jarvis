"use client";

// Session files handover (v1.168.0, P2) — a finished session HANDS OVER what
// it wrote, right on its detail page.
//
// `GET /sessions/{id}/result` has served ledger-proven `files_created` /
// `files_changed` since v1.149.0, and since v1.155.0 it also carries
// `documents` — the SAME files as absolute paths (the lists are
// workspace-relative because that reads well in a report; a client cannot
// preview or download a relative path). Until this panel the only place those
// files surfaced was the chat's result card — the session page, where the user
// actually lands from the kanban/bell, showed the ledger tables but never the
// files themselves.
//
// Rendering rules (same policy as TeamTree next door):
//  - NOTHING renders when the session produced no files — a "Files" box that
//    is empty on most sessions would be noise, not handover.
//  - The list is the ledger's, capped server-side at 50 per kind; when the
//    ledger holds more, the footer says so with the REAL totals
//    (`files_created_total`/`files_changed_total`) — a capped list that looks
//    complete is the silent-truncation lie this repo bans.
//  - Rows reuse ArtifactsRail (the app's one files component since v1.165.0):
//    preview, copy-path, open-in-native-app, download all behave exactly as
//    they do in chat.

import { useEffect, useState } from "react";
import { get, API_BASE, ijToken } from "@/lib/api";
import { ArtifactsRail } from "@/components/chat/ArtifactsRail";

/** The slice of `GET /sessions/{id}/result` this panel consumes (see
 *  `agents/outcome.py::session_result` for the full payload). */
export interface SessionResult {
  found: boolean;
  session_id?: string;
  /** Workspace-relative paths (absolute when the file sits OUTSIDE the
   *  workspace — that fallback is the honest answer, see outcome._rel). */
  files_created?: string[];
  files_changed?: string[];
  /** REAL extents — the lists above are capped at 50 entries each. */
  files_created_total?: number;
  files_changed_total?: number;
  /** ABSOLUTE paths for (created + changed)[:50], in that order (v1.155.0).
   *  Empty when the session has no workspace recorded. */
  documents?: string[];
  revertable?: number;
  reverted?: number;
  [k: string]: unknown;
}

export interface SessionFileRow {
  /** Absolute path when resolvable — what preview/open/download need. */
  path: string;
  /** The readable workspace-relative label the result reported. */
  rel: string;
  change: "created" | "changed";
}

/** Windows drive-letter, UNC, or posix root — anything preview can take as-is. */
function isAbsolutePath(p: string): boolean {
  return /^([a-zA-Z]:[\\/]|[\\/])/.test(p);
}

/** Join a workspace root and a relative path with the workspace's OWN
 *  separator flavor (the daemon runs on Windows on the user's install, posix
 *  in tests/CI — guessing "/" for a `C:\` root would build a mixed path). */
export function joinWorkspace(workspace: string, rel: string): string {
  const root = workspace.replace(/[\\/]+$/, "");
  if (!root) return rel;
  const sep = root.includes("\\") || /^[a-zA-Z]:$/.test(root) ? "\\" : "/";
  return `${root}${sep}${rel}`;
}

/**
 * Flatten a session result into displayable rows. `documents[i]` is the
 * absolute form of `(files_created + files_changed)[i]` — same order, same
 * 50-entry cap (agents/outcome.py builds it as `(created + changed)[:50]`
 * resolved against the workspace). Entries past that cap, or when the result
 * has no workspace (documents == []), fall back to joining against
 * `workspacePath`; a rel that is already absolute passes through untouched.
 * Pure — the tests drive it directly.
 */
export function sessionFileRows(
  result: SessionResult,
  workspacePath = "",
): SessionFileRow[] {
  const created = result.files_created ?? [];
  const changed = result.files_changed ?? [];
  const docs = result.documents ?? [];
  const rows: SessionFileRow[] = [];
  [...created, ...changed].forEach((rel, i) => {
    if (!rel || typeof rel !== "string") return;
    const doc = docs[i];
    const path =
      typeof doc === "string" && doc
        ? doc
        : isAbsolutePath(rel)
          ? rel
          : workspacePath
            ? joinWorkspace(workspacePath, rel)
            : rel;
    rows.push({
      path,
      rel,
      change: i < created.length ? "created" : "changed",
    });
  });
  return rows;
}

/**
 * The honest footer under the rail: real totals, provenance, and — when the
 * server-side cap clipped the list — how much of it the rows actually show.
 * `shown` is the rendered row count. The truncation clause is a COUNT
 * ("showing N of M"), never an ordering claim: the caps are per kind, so with
 * 120 created and 4 changed the rows are the first 50 created plus all 4
 * changed — NOT the first 54 of the combined list.
 */
export function handoverNote(result: SessionResult, shown: number): string {
  const created =
    result.files_created_total ?? (result.files_created ?? []).length;
  const changed =
    result.files_changed_total ?? (result.files_changed ?? []).length;
  const parts: string[] = [];
  if (created > 0) parts.push(`${created} created`);
  if (changed > 0) parts.push(`${changed} changed`);
  const reverted = result.reverted ?? 0;
  if (reverted > 0)
    parts.push(`${reverted} action${reverted === 1 ? "" : "s"} reverted`);
  let note = `${parts.join(" · ")} — from the session's tool ledger`;
  const total = created + changed;
  if (shown < total) note += ` · showing ${shown} of ${total}`;
  return note;
}

/** The download URL the rail's anchor uses — same shape as chat's
 *  (`&download=1` forces attachment; the anchor's own `download` attribute is
 *  ignored cross-origin :8788 → :8787, so the server flag is what makes this a
 *  real download). */
export function fileDownloadHref(path: string): string {
  const tok = ijToken();
  return `${API_BASE}/documents/file?path=${encodeURIComponent(path)}${
    tok ? `&token=${encodeURIComponent(tok)}` : ""
  }&download=1`;
}

/**
 * The panel. Fetches once on mount and re-polls (~8s) while the session is
 * active — files appear as the agent writes them, and the `active` flip at
 * completion triggers one final load so the finished list is never stale.
 * `reloadNonce` is the panel's reload channel for a FINISHED session: the
 * page bumps it on ledger events (tool.executed and friends), so an undo
 * fired from the Time-travel feed on the same page refetches the result
 * instead of leaving stale created/changed counts and rows offering a file
 * the undo may have just deleted.
 * Renders NOTHING when there are no files or the endpoint is unreachable (an
 * optional panel that can't load should be absent, not an error box — the
 * TeamTree rule).
 */
export function SessionFiles({
  sessionId,
  workspacePath = "",
  active,
  onPreview,
  reloadNonce = 0,
}: {
  sessionId: string;
  workspacePath?: string;
  active: boolean;
  onPreview: (path: string) => void;
  /** Bump to force a refetch (page-side ledger events, e.g. an undo). */
  reloadNonce?: number;
}) {
  const [result, setResult] = useState<SessionResult | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await get<SessionResult>(`/sessions/${sessionId}/result`);
        if (alive) setResult(r);
      } catch {
        /* optional panel — absent beats wrong */
      }
    };
    void load();
    if (!active) {
      return () => {
        alive = false;
      };
    }
    const timer = setInterval(() => void load(), 8000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [sessionId, active, reloadNonce]);

  if (!result?.found) return null;
  const rows = sessionFileRows(result, workspacePath);
  if (rows.length === 0) return null;

  return (
    <div data-testid="session-files">
      <ArtifactsRail
        items={rows.map((r) => ({ path: r.path }))}
        onPreview={onPreview}
        downloadHref={fileDownloadHref}
      />
      <p className="mt-1.5 px-2.5 text-[10.5px] text-zinc-600">
        {handoverNote(result, rows.length)}
      </p>
    </div>
  );
}
