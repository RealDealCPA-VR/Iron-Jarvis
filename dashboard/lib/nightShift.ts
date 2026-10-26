/**
 * NIGHT SHIFT — Coding sessions › Tonight (v1.398.0, phase N1 of
 * docs/NIGHT-SHIFT-PLAN.md; the daemon's routes/night.py and harness/night.py).
 *
 * The user queues coding tasks; inside the night window each starts by
 * itself as a NEW coding session on its own branch. This is the screen's
 * reading of `/build/night` (decoders that WHITELIST, the calls, the words).
 * Kept OUTSIDE lib/api.ts (~71 test files mock it wholesale).
 */

import { del, get, patch, post } from "@/lib/api";
import { outcomeWords, statusWords, type StatusWords } from "@/lib/statusWords";

export type NightState = "queued" | "running" | "done" | "failed" | "needs_you" | "stopped";

export const NIGHT_STATES: readonly NightState[] = ["queued", "running", "done", "failed", "needs_you", "stopped"];

export interface NightPick {
  provider: string;
  model: string;
}

export interface NightNote {
  kind: string;
  text: string;
  at: string | null;
}

export interface NightTask {
  id: string;
  folder: string;
  words: string;
  provider: string;
  model: string;
  team: boolean;
  tryOn: NightPick[];
  state: NightState;
  threadId: string;
  branch: string;
  notes: NightNote[];
  startedAt: string | null;
  endedAt: string | null;
}

export interface NightWindow {
  start: string;
  end: string;
  open: boolean;
  /** The window in one plain sentence ("Starts at 11:00 PM tonight."). */
  words: string;
}

export interface NightList {
  tasks: NightTask[];
  window: NightWindow;
  atOnce: number;
  taskMinutes: number;
  cardWaitMinutes: number;
  emergencyStop: boolean;
}

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, max = 20_000): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function decodePick(raw: unknown): NightPick | null {
  const r = rec(raw);
  const provider = str(r?.provider, 120).trim();
  return provider ? { provider, model: str(r?.model, 300).trim() } : null;
}

function decodeNote(raw: unknown): NightNote | null {
  const r = rec(raw);
  const text = str(r?.text, 400).trim();
  if (!text) return null;
  return { kind: str(r?.kind, 40), text, at: typeof r?.at === "string" ? r.at : null };
}

export function decodeTask(raw: unknown): NightTask | null {
  const r = rec(raw);
  const id = str(r?.id, 120);
  if (!r || !id) return null;
  const state = (NIGHT_STATES as readonly string[]).includes(str(r.state, 20)) ? (r.state as NightState) : "queued";
  return {
    id,
    folder: str(r.folder, 1000),
    words: str(r.words),
    provider: str(r.provider, 120),
    model: str(r.model, 300),
    team: r.team === true,
    tryOn: (Array.isArray(r.try_on) ? (r.try_on as unknown[]) : []).map(decodePick).filter((p): p is NightPick => p !== null),
    state,
    threadId: str(r.thread_id, 120),
    branch: str(r.branch, 300),
    notes: (Array.isArray(r.notes) ? (r.notes as unknown[]) : []).map(decodeNote).filter((n): n is NightNote => n !== null),
    startedAt: typeof r.started_at === "string" ? r.started_at : null,
    endedAt: typeof r.ended_at === "string" ? r.ended_at : null,
  };
}

export function decodeList(raw: unknown): NightList {
  const r = rec(raw);
  const w = rec(r?.window);
  const limits = rec(r?.limits);
  const rows: unknown[] = Array.isArray(r?.tasks) ? (r?.tasks as unknown[]) : [];
  return {
    tasks: rows.map(decodeTask).filter((t): t is NightTask => t !== null),
    window: {
      start: str(w?.start, 5),
      end: str(w?.end, 5),
      open: w?.open === true,
      words: str(w?.words, 300),
    },
    atOnce: num(limits?.at_once, 2),
    taskMinutes: num(limits?.task_minutes, 90),
    cardWaitMinutes: num(limits?.card_wait_minutes, 15),
    emergencyStop: r?.emergency_stop === true,
  };
}

// ------------------------------------------------------------------ calls

export async function listNight(): Promise<NightList> {
  return decodeList(await get<unknown>("/build/night"));
}

export interface NightDraft {
  folder: string;
  words: string;
  provider?: string;
  model?: string;
  team?: boolean;
  tryOn?: NightPick[];
}

function draftBody(d: NightDraft): Record<string, unknown> {
  return {
    folder: d.folder,
    words: d.words,
    provider: d.provider ?? "",
    model: d.model ?? "",
    team: !!d.team,
    try_on: (d.tryOn ?? []).map((p) => ({ provider: p.provider, model: p.model })),
  };
}

export async function addNight(d: NightDraft): Promise<NightTask | null> {
  return decodeTask(await post<unknown>("/build/night", draftBody(d)));
}

export async function editNight(id: string, d: NightDraft): Promise<NightTask | null> {
  return decodeTask(await patch<unknown>(`/build/night/${encodeURIComponent(id)}`, draftBody(d)));
}

export function removeNight(id: string): Promise<unknown> {
  return del(`/build/night/${encodeURIComponent(id)}`);
}

export function moveNight(id: string, direction: "up" | "down"): Promise<unknown> {
  return post(`/build/night/${encodeURIComponent(id)}/move`, { direction });
}

export function cancelNight(id: string): Promise<unknown> {
  return post(`/build/night/${encodeURIComponent(id)}/cancel`, {});
}

// ------------------------------------------------------------------ words

/** The row's state in the ONE status table's words (lib/statusWords). */
export function nightStateWords(state: NightState): StatusWords {
  if (state === "needs_you") return outcomeWords("needs_you") ?? statusWords("blocked");
  return statusWords(state);
}

/** The view's one line under "Tonight": the window, then the limits. */
export function nightLimitsLine(list: NightList): string {
  const each = list.taskMinutes === 1 ? "1 minute" : `${list.taskMinutes} minutes`;
  const atOnce = list.atOnce === 1 ? "One at a time" : `Up to ${list.atOnce} at a time`;
  return `${atOnce}, never two in one folder, ${each} each. Each runs on its own branch.`;
}

/** What Emergency stop means for the list, said once above it. */
export const EMERGENCY_LINE =
  "Emergency stop is on, so nothing here starts. Turn it off on the Autonomy page to let tonight's tasks run.";

/** How a task runs, in words ("Your default model" / a model / the team /
 *  "Try on 2 models"). `nameOf` names a model pick the way the model menu does. */
export function howItRunsWords(t: Pick<NightTask, "provider" | "model" | "team" | "tryOn">,
                               nameOf: (p: NightPick) => string): string {
  if (t.tryOn.length) return `Try on ${t.tryOn.map(nameOf).join(", ")}`;
  if (t.team) return "Your model team";
  if (t.provider) return nameOf({ provider: t.provider, model: t.model });
  return "Your default model";
}

/** The last part of a folder path (the row's place). */
export function folderName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** The first line of the words (the row's title). */
export function taskTitle(words: string): string {
  const first = words.split(/\r?\n/).map((l) => l.trim()).find(Boolean) ?? "";
  return first.length > 120 ? `${first.slice(0, 119)}…` : first;
}

/** How often the list is read while the window is visible. */
export const NIGHT_POLL_MS = 10_000;
