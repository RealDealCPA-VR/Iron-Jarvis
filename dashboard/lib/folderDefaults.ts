/**
 * v1.426.0 (Wave 6 lane B, E5): a FOLDER's defaults for new coding sessions
 * started in it — the daemon's `harness/folder_defaults` on the screen.
 *
 *   code  Chat › Code: provider + model, reasoning level, approval level.
 *   tool  Build › Code: the tool, its model, its effort, its mode.
 *
 * The start composers (components/harness/HarnessStartComposer,
 * components/buildCode/StartComposer) read them when a session starts in the
 * folder: a chip the user did not touch shows the folder's value (and the
 * create carries it); a chip the user picked wins; words under the chips say
 * which defaults are in use. "Don't ask" and Full access are never a folder's
 * default (the daemon refuses them in words).
 *
 * Every read goes through ONE in-memory cache per folder (a composer opening
 * again on a folder reads nothing); a save replaces the folder's entry. An
 * older Iron Jarvis (a 404) reads as "no defaults", quietly.
 */

import { get, put } from "@/lib/api";
import { PERMISSION_LEVELS, PERMISSION_MODES, type PermissionMode } from "@/lib/permissionLevels";

export interface CodeDefaults {
  provider?: string;
  model?: string;
  reasoning?: string;
  approval_mode?: PermissionMode;
}

export interface ToolDefaults {
  harness: string;
  model?: string;
  effort?: string;
  mode?: string;
}

export interface FolderDefaults {
  code: CodeDefaults | null;
  tool: ToolDefaults | null;
}

export const NO_DEFAULTS: FolderDefaults = { code: null, tool: null };

function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}
function word(v: unknown, max = 200): string {
  return typeof v === "string" ? v.trim().slice(0, max) : "";
}

/** The postures a folder may start a session with ("Don't ask" never). */
export const FOLDER_POSTURES: readonly PermissionMode[] = PERMISSION_MODES.filter((m) => m !== "yolo");

export function decodeFolderDefaults(raw: unknown): FolderDefaults {
  const d = obj(obj(raw)?.defaults);
  if (!d) return NO_DEFAULTS;
  const c = obj(d.code);
  let code: CodeDefaults | null = null;
  if (c) {
    const posture = word(c.approval_mode, 40) as PermissionMode;
    const out: CodeDefaults = {
      ...(word(c.provider) ? { provider: word(c.provider) } : {}),
      ...(word(c.provider) && word(c.model) ? { model: word(c.model) } : {}),
      ...(word(c.reasoning, 20) ? { reasoning: word(c.reasoning, 20) } : {}),
      ...(FOLDER_POSTURES.includes(posture) ? { approval_mode: posture } : {}),
    };
    code = Object.keys(out).length ? out : null;
  }
  const t = obj(d.tool);
  const tool: ToolDefaults | null =
    t && word(t.harness, 40) && word(t.mode, 40) !== "full"
      ? {
          harness: word(t.harness, 40),
          ...(word(t.model) ? { model: word(t.model) } : {}),
          ...(word(t.effort, 40) ? { effort: word(t.effort, 40) } : {}),
          ...(word(t.mode, 40) ? { mode: word(t.mode, 40) } : {}),
        }
      : null;
  return { code, tool };
}

// ── the one cache ───────────────────────────────────────────────────────────

const cache = new Map<string, FolderDefaults>();
const inFlight = new Map<string, Promise<FolderDefaults>>();

/** The daemon's key for a folder: separators folded, no trailing one, case
 *  folded (harness/sessions.folder_key). */
export function folderDefaultsKey(folder: string): string {
  return folder.trim().replace(/[\\/]+/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** What is cached for `folder` (undefined: not read yet). */
export function cachedFolderDefaults(folder: string): FolderDefaults | undefined {
  return cache.get(folderDefaultsKey(folder));
}

/** Read `folder`'s defaults (once; joined while a read is out). Never throws:
 *  a refusal or an older Iron Jarvis reads as none. */
export function readFolderDefaults(folder: string): Promise<FolderDefaults> {
  const key = folderDefaultsKey(folder);
  if (!key) return Promise.resolve(NO_DEFAULTS);
  const hit = cache.get(key);
  if (hit) return Promise.resolve(hit);
  const out = inFlight.get(key);
  if (out) return out;
  const p = get<unknown>(`/build/folders/defaults?folder=${encodeURIComponent(folder)}`)
    .then((raw) => {
      const got = decodeFolderDefaults(raw);
      cache.set(key, got);
      return got;
    })
    .catch(() => NO_DEFAULTS)
    .finally(() => inFlight.delete(key));
  inFlight.set(key, p);
  return p;
}

/** Save (null: clear) one view's defaults for `folder`; resolves to the
 *  folder's defaults after it. Rejects with the daemon's sentence. */
export async function saveFolderDefaults(
  folder: string,
  view: "code" | "tool",
  value: CodeDefaults | ToolDefaults | null,
): Promise<FolderDefaults> {
  const raw = await put<unknown>("/build/folders/defaults", { folder, view, defaults: value });
  const got = decodeFolderDefaults(raw);
  cache.set(folderDefaultsKey(folder), got);
  return got;
}

/** Tests only: forget every cached folder. */
export function resetFolderDefaultsCache(): void {
  cache.clear();
  inFlight.clear();
}

// ── words ───────────────────────────────────────────────────────────────────

/** Chat › Code's defaults in words: "Opus 5.5 · reasoning high · Accept
 *  edits". `modelName` names a provider/model pair the way the menu does. */
export function codeDefaultsWords(d: CodeDefaults | null, modelName: (provider: string, model: string) => string): string {
  if (!d) return "";
  const parts: string[] = [];
  if (d.provider) parts.push(modelName(d.provider, d.model ?? ""));
  if (d.reasoning) parts.push(`reasoning ${d.reasoning === "xhigh" ? "extra" : d.reasoning}`);
  if (d.approval_mode) parts.push(PERMISSION_LEVELS[d.approval_mode].label);
  return parts.join(" · ");
}

/** Build › Code's defaults in words: "Codex · gpt-6-astra · medium effort ·
 *  Ask first". */
export function toolDefaultsWords(
  d: ToolDefaults | null,
  names: { tool: (id: string) => string; model: (tool: string, id: string) => string; mode: (tool: string, id: string) => string },
): string {
  if (!d) return "";
  const parts = [names.tool(d.harness)];
  if (d.model) parts.push(names.model(d.harness, d.model));
  if (d.effort) parts.push(`${d.effort} effort`);
  if (d.mode) parts.push(names.mode(d.harness, d.mode));
  return parts.join(" · ");
}

/** The sentence under a start composer's chips. */
export function usingDefaultsLine(folderName: string, words: string): string {
  return `New sessions in ${folderName} start with: ${words}.`;
}

export const NO_FOLDER_DEFAULTS_LINE = "New sessions here start with the app's own choices.";
export const SAVE_DEFAULTS_LABEL = "Use for new sessions in this folder";

/** Do two Chat › Code defaults say the same? */
export function sameCodeDefaults(a: CodeDefaults | null, b: CodeDefaults | null): boolean {
  const n = (d: CodeDefaults | null) => JSON.stringify([d?.provider ?? "", d?.model ?? "", d?.reasoning ?? "", d?.approval_mode ?? ""]);
  return n(a) === n(b);
}

/** Do two Build › Code defaults say the same? */
export function sameToolDefaults(a: ToolDefaults | null, b: ToolDefaults | null): boolean {
  const n = (d: ToolDefaults | null) => JSON.stringify([d?.harness ?? "", d?.model ?? "", d?.effort ?? "", d?.mode ?? ""]);
  return n(a) === n(b);
}

/** A folder's display name from its path (its last part). */
export function folderBaseName(folder: string): string {
  const parts = folder.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || folder;
}
