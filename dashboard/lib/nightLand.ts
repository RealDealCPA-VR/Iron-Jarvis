/**
 * MERGE ALL THAT PASSED (25-a, R3.5 — the daemon's harness/landing.py and
 * routes/night_land.py). Last night says "5 of 6 passed their tests. Merge
 * all 5"; the first press reads the PREVIEW (every passing branch in the
 * order it was queued, its commits and its files, the branch it goes into,
 * the command run after each merge, a clash said before anything is
 * pressed); the second press merges exactly that list (the preview's id) and
 * the merge's record says each step; Undo is offered the way the record's
 * plan says (D-25.1). Nothing is pushed (D-25.3).
 *
 * Words follow VOCABULARY.md: a branch is MERGED, two that change the same
 * lines CLASH. The decoders keep only what the screen shows.
 */

import { get, post } from "@/lib/api";

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, cap = 500): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

function int(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.trunc(v) : 0;
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/** A folder's head line, read with the night's summary (no git). */
export interface LandFolderCount {
  folder: string;
  /** The folder as the daemon keys it (matches a preview's landing). */
  folderKey: string;
  folderName: string;
  green: number;
  total: number;
  /** "5 of 6 passed their tests." */
  words: string;
  /** "Merge all 5" ("" = nothing to merge). */
  button: string;
}

export type MergeState = "running" | "moving" | "merged" | "stopped";

export interface MergeStep {
  taskId: string;
  title: string;
  branch: string;
  state: string;
  line: string;
  seconds: number | null;
}

export interface UndoPlan {
  how: "put_back" | "reverse" | "refused" | "";
  words: string;
  button: string;
}

/** One press of Merge all, as its record says. */
export interface MergeRecord {
  id: string;
  night: string;
  folder: string;
  base: string;
  into: string;
  state: MergeState;
  steps: MergeStep[];
  merged: number;
  command: string;
  words: string;
  undone: boolean;
  undoWords: string;
  /** Read with the record alone (GET /build/night/land/{id}). */
  undo: UndoPlan | null;
}

export interface NightLand {
  folders: LandFolderCount[];
  green: number;
  total: number;
  merges: MergeRecord[];
}

export interface LandFile {
  path: string;
  status: string;
  added: number | null;
  removed: number | null;
}

export interface LandStep {
  taskId: string;
  title: string;
  branch: string;
  ready: boolean;
  commits: { sha: string; subject: string }[];
  commitCount: number;
  files: LandFile[];
  fileCount: number;
  added: number;
  removed: number;
  clash: string[];
  /** "Merging would clash in a.py." */
  clashWords: string;
}

export interface LandLeft {
  taskId: string;
  title: string;
  why: string;
}

/** One folder's preview. */
export interface Landing {
  id: string;
  folder: string;
  folderKey: string;
  folderName: string;
  base: string;
  /** "main in D:\code\shop". */
  into: string;
  command: string;
  /** "Tests run after each: `npm test`". */
  commandWords: string;
  depsWords: string;
  steps: LandStep[];
  left: LandLeft[];
  merged: { taskId: string; title: string; into: string }[];
  ready: number;
  green: number;
  total: number;
  /** Why it cannot start now, in words ("" = it can). */
  blocked: string;
  words: string;
  button: string;
}

export interface LandPreview {
  night: string;
  landings: Landing[];
  merges: MergeRecord[];
  emergencyStop: boolean;
}

const MERGE_STATES: readonly MergeState[] = ["running", "moving", "merged", "stopped"];

function decodeUndo(raw: unknown): UndoPlan | null {
  const r = rec(raw);
  if (!r) return null;
  const how = str(r.how, 20);
  return {
    how: how === "put_back" || how === "reverse" || how === "refused" ? how : "",
    words: str(r.words, 600),
    button: str(r.button, 120),
  };
}

export function decodeMerge(raw: unknown): MergeRecord | null {
  const r = rec(raw);
  const id = str(r?.id, 80);
  if (!r || !id) return null;
  const state = str(r.state, 20) as MergeState;
  return {
    id,
    night: str(r.night, 20),
    folder: str(r.folder, 1000),
    base: str(r.base, 300),
    into: str(r.into, 1300),
    state: MERGE_STATES.includes(state) ? state : "stopped",
    steps: list(r.steps)
      .map(rec)
      .filter((s): s is Record<string, unknown> => s !== null)
      .map((s) => ({
        taskId: str(s.task_id, 120),
        title: str(s.title, 300) || "Night task",
        branch: str(s.branch, 300),
        state: str(s.state, 30),
        line: str(s.line, 600),
        seconds: typeof s.seconds === "number" && Number.isFinite(s.seconds) ? s.seconds : null,
      })),
    merged: int(r.merged),
    command: str(r.command, 600),
    words: str(r.words, 1200),
    undone: r.undone === true,
    undoWords: str(r.undo_words, 600),
    undo: decodeUndo(r.undo),
  };
}

export function decodeNightLand(raw: unknown): NightLand | null {
  const r = rec(raw);
  if (!r) return null;
  return {
    folders: list(r.folders)
      .map(rec)
      .filter((f): f is Record<string, unknown> => f !== null)
      .map((f) => ({
        folder: str(f.folder, 1000),
        folderKey: str(f.folder_key, 1000),
        folderName: str(f.folder_name, 300),
        green: int(f.green),
        total: int(f.total),
        words: str(f.words, 200),
        button: str(f.button, 60),
      })),
    green: int(r.green),
    total: int(r.total),
    merges: list(r.merges).map(decodeMerge).filter((m): m is MergeRecord => m !== null),
  };
}

function decodeStep(raw: unknown): LandStep | null {
  const s = rec(raw);
  const taskId = str(s?.task_id, 120);
  if (!s || !taskId) return null;
  return {
    taskId,
    title: str(s.title, 300) || "Night task",
    branch: str(s.branch, 300),
    ready: s.ready === true,
    commits: list(s.commits)
      .map(rec)
      .filter((c): c is Record<string, unknown> => c !== null)
      .map((c) => ({ sha: str(c.sha, 64), subject: str(c.subject, 300) })),
    commitCount: int(s.commit_count),
    files: list(s.files)
      .map(rec)
      .filter((f): f is Record<string, unknown> => f !== null)
      .map((f) => ({
        path: str(f.path, 1000),
        status: str(f.status, 20),
        added: typeof f.added === "number" ? f.added : null,
        removed: typeof f.removed === "number" ? f.removed : null,
      })),
    fileCount: int(s.file_count),
    added: int(s.added),
    removed: int(s.removed),
    clash: list(s.clash).map((x) => str(x, 1000)).filter(Boolean),
    clashWords: str(s.clash_words, 600),
  };
}

export function decodeLanding(raw: unknown): Landing | null {
  const r = rec(raw);
  if (!r) return null;
  return {
    id: str(r.id, 80),
    folder: str(r.folder, 1000),
    folderKey: str(r.folder_key, 1000),
    folderName: str(r.folder_name, 300),
    base: str(r.base, 300),
    into: str(r.into, 1300),
    command: str(r.command, 600),
    commandWords: str(r.command_words, 700),
    depsWords: str(r.deps_words, 400),
    steps: list(r.steps).map(decodeStep).filter((s): s is LandStep => s !== null),
    left: list(r.left)
      .map(rec)
      .filter((x): x is Record<string, unknown> => x !== null)
      .map((x) => ({ taskId: str(x.task_id, 120), title: str(x.title, 300) || "Night task", why: str(x.why, 600) })),
    merged: list(r.merged)
      .map(rec)
      .filter((x): x is Record<string, unknown> => x !== null)
      .map((x) => ({ taskId: str(x.task_id, 120), title: str(x.title, 300) || "Night task", into: str(x.into, 300) })),
    ready: int(r.ready),
    green: int(r.green),
    total: int(r.total),
    blocked: str(r.blocked, 700),
    words: str(r.words, 200),
    button: str(r.button, 60),
  };
}

export async function readPreview(night: string): Promise<LandPreview> {
  const r = rec(await get<unknown>(`/build/night/summaries/${encodeURIComponent(night)}/land`));
  return {
    night: str(r?.night, 20) || night,
    landings: list(r?.landings).map(decodeLanding).filter((l): l is Landing => l !== null),
    merges: list(r?.merges).map(decodeMerge).filter((m): m is MergeRecord => m !== null),
    emergencyStop: r?.emergency_stop === true,
  };
}

/** The second press: merge exactly the previewed list (its id). */
export async function startMerge(night: string, previewId: string): Promise<MergeRecord | null> {
  const r = rec(await post<unknown>(`/build/night/summaries/${encodeURIComponent(night)}/land`, { preview_id: previewId }));
  return decodeMerge(r?.merge);
}

export async function readMerge(id: string): Promise<MergeRecord | null> {
  return decodeMerge(await get<unknown>(`/build/night/land/${encodeURIComponent(id)}`));
}

export async function undoMerge(id: string, how: string): Promise<MergeRecord | null> {
  return decodeMerge(await post<unknown>(`/build/night/land/${encodeURIComponent(id)}/undo`, { how }));
}

/** How often a running merge is read again (only while it runs and the
 *  window is visible). */
export const MERGE_POLL_MS = 1_500;

export function mergeRunning(m: Pick<MergeRecord, "state"> | null | undefined): boolean {
  return !!m && (m.state === "running" || m.state === "moving");
}

/** A running merge in words: "Merging 2 of 5: 'Add CSV export'." or
 *  "Running `npm test` after merging 'Add CSV export' (2 of 5)." */
export function runningWords(m: MergeRecord): string {
  const n = m.steps.length;
  if (m.state === "moving") return `Moving ${m.base} to the merged work…`;
  const i = m.steps.findIndex((s) => s.state === "merging" || s.state === "testing");
  if (i < 0) return m.words || `Merging into ${m.base}…`;
  const s = m.steps[i];
  if (s.state === "testing" && m.command) {
    return `Running \`${m.command}\` after merging '${s.title}' (${i + 1} of ${n})…`;
  }
  return `Merging ${i + 1} of ${n}: '${s.title}'…`;
}

/** A file's lines in words: "+12 −3" ("" for a binary file). */
export function fileLines(f: Pick<LandFile, "added" | "removed">): string {
  if (f.added === null && f.removed === null) return "binary";
  return `+${f.added ?? 0} −${f.removed ?? 0}`;
}

/** "3 commits" / "1 commit". */
export function commitsWords(n: number): string {
  return `${n} commit${n === 1 ? "" : "s"}`;
}

/** What the night's merge looks like now, read ONCE when Last night opens
 *  (and again only when the night's numbers or its newest merge change, or
 *  on a press — never on the summary's own beat): it changes when its key does. */
export function lookKey(land: NightLand | null): string {
  if (!land) return "";
  const m = land.merges[0];
  return `${land.green}/${land.total}/${m ? `${m.id}:${m.state}:${m.undone ? 1 : 0}` : ""}`;
}

/** The preview's landing for a head line's folder (by the daemon's folder
 *  key, else the folder as written). */
export function landingFor(look: LandPreview | null, f: Pick<LandFolderCount, "folder" | "folderKey">): Landing | null {
  if (!look) return null;
  return (
    look.landings.find((l) => !!f.folderKey && l.folderKey === f.folderKey) ??
    look.landings.find((l) => l.folder === f.folder) ??
    null
  );
}

/** Each night task's clash, said on its own row before anything is pressed:
 *  task id -> "Merging would clash in a.py." */
export function clashByTask(look: LandPreview | null): Record<string, string> {
  const out: Record<string, string> = {};
  for (const l of look?.landings ?? []) {
    for (const s of l.steps) if (s.clashWords) out[s.taskId] = s.clashWords;
  }
  return out;
}

const STEP_END: Record<string, string> = {
  merged: "merged",
  tests_failed: "its tests failed",
  not_checked: "its tests did not run",
  clash: "it would clash",
  merge_failed: "Git could not merge it",
  // The base never moved (Emergency stop, closing, an error): on the merge's copy only.
  copy_only: "merged in the copy only",
};

/** How a step of a merge ended, in words ("merged", "its tests failed"). */
export function stepEndWords(state: string): string {
  return STEP_END[state] ?? "not merged";
}

/** A step's time: "12 s", "3 min 5 s". */
export function secondsWords(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m} min ${rest} s` : `${m} min`;
}
