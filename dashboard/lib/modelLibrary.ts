/**
 * The model library (next leaps 43-a): Model servers › Models, and a model
 * download started from chat (its card under the reply).
 *
 * The wire, from `src/iron_jarvis/daemon/routes/fleet.py`:
 *   GET  /fleet/library?node=&q=          the list, marked against this PC
 *   POST /fleet/nodes/{id}/models/size    the exact size BEFORE it starts
 *   POST /fleet/nodes/{id}/models/download   start it (409 in words when refused)
 *   GET  /fleet/downloads                 the newest downloads
 *   POST /fleet/downloads/{id}/cancel     Cancel
 * and the events `model.download.progress` / `model.download.ended`.
 *
 * Every read is decoded defensively: what a server or Hugging Face named a
 * model is text, never markup. Its own file (not lib/api.ts, which ~70 test
 * files mock).
 */

import { get, post } from "@/lib/api";

export const LIBRARY_VIEW = "models";
export const SERVERS_VIEW = "servers";
export type FleetView = typeof LIBRARY_VIEW | typeof SERVERS_VIEW;
/** Where the library lives (one address per place). */
export const LIBRARY_HREF = "/fleet?view=models";

export const DOWNLOAD_EVENT_TYPES = ["model.download.progress", "model.download.ended"] as const;

export type FitMark = "fits" | "tight" | "too_big" | "unknown";

export interface LibraryModel {
  id: string;
  name: string;
  good_at: string;
  model: string;
  source: string;
  size_bytes: number | null;
  size_words: string;
  memory_words: string;
  fit: FitMark;
  fit_words: string;
  installed: boolean;
  can_download: boolean;
  tools: boolean | null;
  vision: boolean | null;
}

export interface LibraryServer {
  id: string;
  label: string;
  kind: string;
  this_pc: boolean;
  downloads: boolean;
  download_why: string;
}

export interface LibraryView {
  servers: LibraryServer[];
  server: LibraryServer | null;
  summary: string;
  models: LibraryModel[];
  search: { words: string; models: LibraryModel[]; why: string };
  checked: string;
  error?: string;
}

export interface DownloadSize {
  model: string;
  name: string;
  source: string;
  size_words: string;
  size_from: "measured" | "about" | "";
  free_after_words: string;
  refusal: string;
  can_start: boolean;
  partial_words: string;
}

export type DownloadState = "downloading" | "done" | "stopped" | "failed";

export interface Download {
  id: string;
  node_id: string;
  model: string;
  name: string;
  bytes_total: number | null;
  bytes_done: number;
  percent: number | null;
  state: DownloadState;
  error: string;
  /** The server it downloads into, by its name ("" = not known). */
  server: string;
  /** Do this server's models reach the model menu (a server the menu lists,
   *  or Ollama on this PC once its address is saved)? Only then does a
   *  finished row say so. */
  in_menu: boolean;
  /** When it is not in the menu, the one step that puts it there, in the
   *  daemon's words ("" = none known). */
  menu_step: string;
}

const str = (v: unknown, max = 300): string => (typeof v === "string" ? v.slice(0, max) : "");
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
const FITS: readonly FitMark[] = ["fits", "tight", "too_big", "unknown"];
const STATES: readonly DownloadState[] = ["downloading", "done", "stopped", "failed"];

function decodeModel(raw: unknown): LibraryModel | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const model = str(r.model, 200);
  if (!model) return null;
  const fit = FITS.includes(r.fit as FitMark) ? (r.fit as FitMark) : "unknown";
  return {
    id: str(r.id, 200) || model,
    name: str(r.name, 120) || model,
    good_at: str(r.good_at, 200),
    model,
    source: str(r.source, 40),
    size_bytes: num(r.size_bytes),
    size_words: str(r.size_words, 60),
    memory_words: str(r.memory_words, 60),
    fit,
    fit_words: str(r.fit_words, 60) || "Fit not known",
    installed: r.installed === true,
    can_download: r.can_download === true,
    tools: bool(r.tools),
    vision: bool(r.vision),
  };
}

function decodeServer(raw: unknown): LibraryServer | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id, 120);
  if (!id) return null;
  return {
    id,
    label: str(r.label, 80) || id,
    kind: str(r.kind, 40),
    this_pc: r.this_pc === true,
    downloads: r.downloads === true,
    download_why: str(r.download_why, 400),
  };
}

export function decodeLibrary(raw: unknown): LibraryView {
  const r = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const list = (v: unknown) => (Array.isArray(v) ? v.slice(0, 60).map(decodeModel).filter((m): m is LibraryModel => !!m) : []);
  const search = r.search && typeof r.search === "object" ? (r.search as Record<string, unknown>) : {};
  return {
    servers: Array.isArray(r.servers) ? r.servers.map(decodeServer).filter((s): s is LibraryServer => !!s) : [],
    server: decodeServer(r.server),
    summary: str(r.summary, 400),
    models: list(r.models),
    search: { words: str(search.words, 80), models: list(search.models), why: str(search.why, 200) },
    checked: str(r.checked, 20),
    ...(str(r.error) ? { error: str(r.error) } : {}),
  };
}

export function decodeSize(raw: unknown): DownloadSize {
  const r = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const from = r.size_from === "measured" || r.size_from === "about" ? r.size_from : "";
  return {
    model: str(r.model, 200),
    name: str(r.name, 120) || str(r.model, 200),
    source: str(r.source, 40),
    size_words: str(r.size_words, 60),
    size_from: from,
    free_after_words: str(r.free_after_words, 120),
    refusal: str(r.refusal, 400),
    can_start: r.can_start === true && !str(r.refusal),
    partial_words: str(r.partial_words, 200),
  };
}

export function decodeDownload(raw: unknown): Download | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id, 80);
  if (!id) return null;
  const state = STATES.includes(r.state as DownloadState) ? (r.state as DownloadState) : "downloading";
  const total = num(r.bytes_total);
  const done = num(r.bytes_done) ?? 0;
  let percent = num(r.percent);
  if (percent === null && total) percent = Math.min(100, Math.floor((done * 100) / total));
  if (state === "done") percent = 100;
  return {
    id,
    node_id: str(r.node_id, 120),
    model: str(r.model, 200),
    name: str(r.name, 120) || str(r.model, 200),
    bytes_total: total,
    bytes_done: done,
    percent: percent === null ? null : Math.max(0, Math.min(100, Math.round(percent))),
    state,
    error: str(r.error, 400),
    server: str(r.server, 80),
    in_menu: r.in_menu === true,
    menu_step: r.in_menu === true ? "" : str(r.menu_step, 200),
  };
}

/** Fold one download event into a list (newest first), by id. */
export function foldDownload(list: Download[], payload: unknown): Download[] {
  const next = decodeDownload(payload);
  if (!next) return list;
  const at = list.findIndex((d) => d.id === next.id);
  if (at < 0) return [next, ...list].slice(0, 20);
  // An event that does not say the row's name or where the model lands keeps
  // what was read (decodeDownload falls back to the model id for a missing
  // name, so the name is taken only when the event carries one).
  const sent = payload as Record<string, unknown>;
  const merged = {
    ...list[at],
    ...next,
    name: str(sent.name, 120) ? next.name : list[at].name,
    server: next.server || list[at].server,
    in_menu: next.in_menu || list[at].in_menu,
    menu_step: next.in_menu || list[at].in_menu ? "" : next.menu_step || list[at].menu_step,
  };
  // A late progress event never takes an ended row back to downloading.
  if (list[at].state !== "downloading" && next.state === "downloading") return list;
  const out = list.slice();
  out[at] = merged;
  return out;
}

/** "2.0 GB" / "274 MB" (decimal, as the daemon writes sizes). */
export function bytesWords(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return "";
  if (n >= 1e9) {
    const gb = n / 1e9;
    return gb < 10 ? `${gb.toFixed(1)} GB` : `${Math.round(gb)} GB`;
  }
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

export function progressWords(d: Download): string {
  const done = bytesWords(d.bytes_done) || "0 MB";
  const total = bytesWords(d.bytes_total);
  if (d.state === "done") {
    // "In the model menu" only when it is (Trust: never claim a change it
    // did not make); else only where the model is, and the step that puts it
    // in the menu when the daemon named one.
    const where = d.in_menu
      ? " It is in the model menu now."
      : `${d.server ? ` It is on ${d.server} now.` : ""}${d.menu_step ? ` ${d.menu_step}` : ""}`;
    return `${total ? `Downloaded, ${total}.` : "Downloaded."}${where}`;
  }
  if (d.state !== "downloading") return d.error || (d.state === "stopped" ? "Stopped." : "The download did not finish.");
  if (!total) return `${done} so far`;
  return `${done} of ${total}${d.percent !== null ? ` · ${d.percent}%` : ""}`;
}

/** The mark's tone for the calm badge. */
export function fitTone(mark: FitMark): "green" | "amber" | "red" | "slate" {
  return mark === "fits" ? "green" : mark === "tight" ? "amber" : mark === "too_big" ? "red" : "slate";
}

/** The view an address asks for (`?view=models`), else null. */
export function viewFromSearch(search: string): FleetView | null {
  try {
    const v = new URLSearchParams(search).get("view");
    return v === LIBRARY_VIEW || v === SERVERS_VIEW ? v : null;
  } catch {
    return null;
  }
}

export async function readLibrary(node: string, q = ""): Promise<LibraryView> {
  const params = new URLSearchParams();
  if (node) params.set("node", node);
  if (q.trim()) params.set("q", q.trim());
  const qs = params.toString();
  return decodeLibrary(await get<unknown>(`/fleet/library${qs ? `?${qs}` : ""}`));
}

export async function readSize(node: string, model: string): Promise<DownloadSize> {
  return decodeSize(await post<unknown>(`/fleet/nodes/${encodeURIComponent(node)}/models/size`, { model }));
}

export async function startDownload(node: string, model: string): Promise<Download | null> {
  const res = await post<{ download?: unknown }>(`/fleet/nodes/${encodeURIComponent(node)}/models/download`, { model });
  return decodeDownload(res?.download);
}

export async function readDownloads(): Promise<Download[]> {
  const res = await get<{ downloads?: unknown }>("/fleet/downloads");
  return Array.isArray(res?.downloads) ? res.downloads.map(decodeDownload).filter((d): d is Download => !!d) : [];
}

export async function cancelDownload(id: string): Promise<Download | null> {
  const res = await post<{ download?: unknown }>(`/fleet/downloads/${encodeURIComponent(id)}/cancel`, {});
  return decodeDownload(res?.download);
}
