/**
 * v1.464.0: starter skill bundles and skills from GitHub (the Templates page).
 *
 * Decoders and words only (no imports), so the component and its tests read
 * one place. The daemon's answers are whitelisted here: a field it did not
 * send is never invented.
 */

export type BundleSkillState = "not_loaded" | "loaded" | "edited" | "own" | "update";

export interface BundleSkill {
  name: string;
  description: string;
  state: BundleSkillState;
}

export interface SkillBundle {
  id: string;
  name: string;
  blurb: string;
  skills: BundleSkill[];
  loaded: number;
}

export interface LoadResult {
  loaded: string[];
  skipped: { name: string; reason: string }[];
}

export interface RemoveResult {
  removed: string[];
  kept: { name: string; reason: string }[];
}

export interface GithubSkill {
  path: string;
  name: string;
  description: string;
  canLoad: boolean;
  why: string;
  otherFiles: number;
}

export interface GithubPreview {
  repo: string;
  commit: string;
  skills: GithubSkill[];
  more: number;
  warning: string;
}

const STATES: readonly BundleSkillState[] = ["not_loaded", "loaded", "edited", "own", "update"];

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

export function decodeBundles(raw: unknown): SkillBundle[] {
  return list(obj(raw).bundles)
    .map((b) => {
      const o = obj(b);
      const skills = list(o.skills)
        .map((s) => {
          const so = obj(s);
          const state = STATES.includes(so.state as BundleSkillState)
            ? (so.state as BundleSkillState)
            : "not_loaded";
          return { name: str(so.name), description: str(so.description), state };
        })
        .filter((s) => s.name);
      return {
        id: str(o.id),
        name: str(o.name) || str(o.id),
        blurb: str(o.blurb),
        skills,
        loaded: skills.filter((s) => s.state === "loaded" || s.state === "edited" || s.state === "update").length,
      };
    })
    .filter((b) => b.id && b.skills.length > 0);
}

const named = (v: unknown): { name: string; reason: string }[] =>
  list(v)
    .map((x) => ({ name: str(obj(x).name), reason: str(obj(x).reason) }))
    .filter((x) => x.name);

const names = (v: unknown): string[] => list(v).filter((x): x is string => typeof x === "string");

export function decodeLoad(raw: unknown): LoadResult {
  const o = obj(raw);
  return { loaded: names(o.loaded), skipped: named(o.skipped) };
}

export function decodeRemove(raw: unknown): RemoveResult {
  const o = obj(raw);
  return { removed: names(o.removed), kept: named(o.kept) };
}

export function decodeGithubPreview(raw: unknown): GithubPreview | null {
  const o = obj(raw);
  const commit = str(o.commit);
  if (!/^[0-9a-f]{40}$/.test(commit)) return null;
  return {
    repo: str(o.repo),
    commit,
    more: typeof o.more === "number" && o.more > 0 ? o.more : 0,
    warning: str(o.warning),
    skills: list(o.skills)
      .map((s) => {
        const so = obj(s);
        return {
          path: str(so.path),
          name: str(so.name),
          description: str(so.description),
          canLoad: so.can_load === true,
          why: str(so.why),
          otherFiles: typeof so.other_files === "number" && so.other_files > 0 ? so.other_files : 0,
        };
      })
      .filter((s) => s.path && s.name),
  };
}

/** What a skill's row says beside its name ("" = nothing to say). */
export function stateWords(state: BundleSkillState): string {
  switch (state) {
    case "loaded":
      return "Loaded";
    case "edited":
      return "Loaded, and you changed it";
    case "own":
      return "You have your own skill with this name";
    case "update":
      return "Loaded. A newer copy is here";
    default:
      return "";
  }
}

/** A skill the Load press can act on by default. */
export const canTick = (s: BundleSkill): boolean => s.state === "not_loaded" || s.state === "update";

const count = (n: number, one: string): string => `${n} ${one}${n === 1 ? "" : "s"}`;

export function bundleSummary(b: SkillBundle): string {
  const total = count(b.skills.length, "skill");
  if (b.loaded === 0) return total;
  if (b.loaded >= b.skills.length) return `${total}, all loaded`;
  return `${total}, ${b.loaded} loaded`;
}

const reasons = (rows: { name: string; reason: string }[]): string =>
  rows.map((r) => `${r.name} (${r.reason})`).join("; ");

/** The one line said after a Load press. */
export function loadWords(r: LoadResult): string {
  const parts: string[] = [];
  if (r.loaded.length > 0) {
    parts.push(`Loaded ${count(r.loaded.length, "skill")}. Type / in Chat to use one.`);
  }
  if (r.skipped.length > 0) parts.push(`Not loaded: ${reasons(r.skipped)}.`);
  return parts.join(" ") || "Nothing was loaded.";
}

/** The one line said after a Remove press. */
export function removeWords(r: RemoveResult): string {
  const parts: string[] = [];
  if (r.removed.length > 0) {
    parts.push(`Removed ${count(r.removed.length, "skill")}. They were moved to Iron Jarvis's trash folder, not deleted.`);
  }
  if (r.kept.length > 0) parts.push(`Kept: ${reasons(r.kept)}.`);
  return parts.join(" ") || "Nothing was removed.";
}

export const GITHUB_HEADING = "Load skills from GitHub";
export const GITHUB_PLACEHOLDER = "https://github.com/owner/repository";
export const GITHUB_ARMED =
  "Press again to load. You have read these skills and trust where they come from.";

export function githubLoadLabel(n: number, armed: boolean): string {
  if (armed) return `Yes, load ${count(n, "skill")}`;
  return `Load ${count(n, "skill")}`;
}

export function otherFilesWords(n: number): string {
  return n > 0 ? `${count(n, "other file")} in its folder will not be downloaded` : "";
}
