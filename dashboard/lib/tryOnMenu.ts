/**
 * v1.400.7: the "Try on…" list, GROUPED BY PROVIDER (the user, 2026-10-27,
 * with a screenshot of the v1.397.0 list: one long flat checkbox list of every
 * model from every provider — Opus 5.5 twice, "your model server" beside
 * "glm-5.3-flash", dozens of raw OpenRouter ids, and nothing saying which
 * provider serves a model or whether it runs on this network).
 *
 * The list now reads like the composer's model menu (components/chat/
 * ModelMenu): one row per provider, local first, each marked where it runs —
 * `KIND_BADGE`'s "local" / "cloud · on your plan" / "cloud · pay per use" /
 * "cloud", or "local + cloud" for a model server that passes some names on to
 * a cloud service — and each opening a pop-out of its models, every model
 * marked local or cloud too. Where a row runs is read from the daemon's own
 * `on_this_network` on THAT row (ModelMenu `rowKind`), never guessed from the
 * provider.
 *
 * NO MODEL TWICE:
 *  - a keyless Anthropic / OpenAI served through the signed-in CLI is that
 *    CLI (lib/harnessModelMenu `foldKeylessApis`, the harness menu's own
 *    rule): a model the CLI lists is listed once, under the CLI; one it does
 *    not list is listed there too;
 *  - the user's own model server is reached two ways — its config slot
 *    ("custom" / "ollama") and the fleet node the registry derives from that
 *    slot (`fleet-custom` / `fleet-ollama`, the same address): it is ONE
 *    group, named once (the name the user gave the machine, else "Your model
 *    server"), and a model both list, or a name the node reports as another
 *    name of the same deployment (`aliases`), is one entry;
 *  - the "fleet" alias ("whatever my own servers run") is not a second model
 *    beside the server's real one: it is left out wherever that server lists
 *    a model that runs on this network.
 *
 * WHAT A PICK SENDS: the daemon refuses a pick that is not a GET /models row
 * (harness/attempts.py `resolve`), so every entry carries the CATALOG pair
 * it came from — a model folded under Claude Code still sends
 * `anthropic::claude-opus-4-8`; a model on the merged server sends the config
 * slot's pair when the slot lists it, else the node's.
 *
 * Pure: computed from the GET /models rows and Local only. The picks, their
 * labels, the three-model limit and the `try_on` body are lib/buildAttempts'.
 */

import { KIND_BADGE, PROVIDER_RANK, rowKind } from "@/components/chat/ModelMenu";
import { tryOnChoices, type TryOnPick, type TryOnRow } from "@/lib/buildAttempts";
import { foldKeylessApis, wayLabel } from "@/lib/harnessModelMenu";
import { isOwnServerSlot, isServerAlias, machineName, modelRowName, providerHeading } from "@/lib/onboarding";
import type { ModelOption } from "@/lib/types";

/** Where a model runs: a KIND_BADGE key. */
export type TryOnPlace = "local" | "cli" | "api" | "endpoint";

/** One model in a provider's pop-out. */
export interface TryOnModel {
  /** The CATALOG pair a pick sends (a GET /models row). */
  provider: string;
  model: string;
  /** The words the row (and the chip, for one pick) says. */
  label: string;
  /** The record behind the words: "provider · model". */
  title: string;
  place: TryOnPlace;
  /** The row as listed (its chips: long docs, uses credits). */
  row: ModelOption;
}

/** One provider row. */
export interface TryOnGroup {
  /** Its key: the provider, or the config slot for a server reached two ways. */
  id: string;
  label: string;
  /** Where its models run: KIND_BADGE's key; "mixed" = some on your own
   *  hardware, some in the cloud; "cloud" = all in the cloud, paid for in
   *  more than one way. */
  place: TryOnPlace | "mixed" | "cloud";
  models: TryOnModel[];
}

/** A provider whose models run in more than one place. */
export const MIXED_BADGE = {
  text: "local + cloud",
  cls: "text-zinc-400",
  title:
    "Some of its models run on your own hardware and some are passed on to a cloud service. Each model says which.",
};

/** A provider whose models all run in the cloud, paid for in more than one way. */
export const CLOUD_BADGE = {
  text: "cloud",
  cls: "text-zinc-500",
  title: "Runs in the cloud. Each model says how it is paid for.",
};

/** The badge a provider row wears. */
export function groupBadge(place: TryOnGroup["place"]): { text: string; cls: string; title: string } {
  if (place === "mixed") return MIXED_BADGE;
  if (place === "cloud") return CLOUD_BADGE;
  return KIND_BADGE[place] ?? KIND_BADGE.api;
}

/** The ONE word a model row says: "local", or "cloud" (its badge's title says
 *  how a cloud model is paid for). */
export function placeWord(place: TryOnPlace): "local" | "cloud" {
  return place === "local" ? "local" : "cloud";
}

/** The two config slots that are also listed as a fleet node of their own. */
const SLOTS = ["custom", "ollama"] as const;

const valueOf = (provider: string, model: string): string => `${provider}::${model}`;

/** Where a row runs: the daemon's own `on_this_network` first (true = it
 *  stays on this network: local, whatever its class says), then the model
 *  menu's rule (`rowKind`: false = cloud, and how it is paid for). */
function placeOf(m: ModelOption): TryOnPlace {
  if (m.on_this_network === true) return "local";
  const k = rowKind(m);
  return k === "local" || k === "cli" || k === "endpoint" ? k : "api";
}

/** The other names a fleet node reports for one deployment (GET /models
 *  `aliases`, fleet/picker.py). */
function aliasesOf(m: ModelOption): string[] {
  const raw = (m as { aliases?: unknown }).aliases;
  return Array.isArray(raw) ? raw.filter((a): a is string => typeof a === "string" && !!a) : [];
}

/** The group a provider belongs to: a config slot's fleet twin joins the slot
 *  when both are listed. */
function groupKey(provider: string, providers: ReadonlySet<string>): string {
  for (const slot of SLOTS) {
    if (provider === `fleet-${slot}` && providers.has(slot)) return slot;
  }
  return provider;
}

/** A group's name: the user's own server by the name they gave the machine
 *  (else "Your model server"); an API reached with a key as "Anthropic API";
 *  every other provider by the model menu's own group name. */
function groupLabel(key: string, first: ModelOption, catalog: readonly ModelOption[]): string {
  if (isOwnServerSlot(key)) {
    const slot = key.startsWith("fleet-") ? key.slice("fleet-".length) : key;
    if ((SLOTS as readonly string[]).includes(slot)) {
      // The slot's fleet twin carries the machine's name once the user named
      // it; the registry's default label ("Custom endpoint") is not a name.
      return machineName(`fleet-${slot}`, { catalog }) || providerHeading(slot) || wayLabel(first.provider, first);
    }
    return machineName(key, { catalog }) || providerHeading("custom");
  }
  return wayLabel(first.provider, first);
}

/**
 * The provider groups the "Try on…" list draws, best place first (your own
 * hardware, your plans, then metered or outside servers — the model menu's
 * order). Only connected models, never the demo model; under Local only, only
 * models that stay on this network (`tryOnChoices`).
 */
export function tryOnGroups(rows: readonly TryOnRow[], localOnly: boolean): TryOnGroup[] {
  // GET /models rows: the fields the menu reads beyond TryOnRow's are optional.
  const catalog = rows as unknown as readonly ModelOption[];
  const choices = tryOnChoices(rows, localOnly) as unknown as ModelOption[];
  const { listed, origin } = foldKeylessApis(choices);
  const providers = new Set(listed.map((m) => m.provider));

  type Draft = { key: string; label: string; entries: TryOnModel[]; ids: Map<string, number>; size: number };
  const drafts = new Map<string, Draft>();
  const order: string[] = [];
  // The config slot's rows first, so a model both list sends the slot's pair
  // (the address the user typed on Connections), then its fleet twin's.
  const slotFirst = [...listed].sort((a, b) => {
    const ta = a.provider.startsWith("fleet-") && groupKey(a.provider, providers) !== a.provider ? 1 : 0;
    const tb = b.provider.startsWith("fleet-") && groupKey(b.provider, providers) !== b.provider ? 1 : 0;
    return ta - tb;
  });
  // A deployment's other names, by group: a slot row named like one is the
  // same model.
  const otherNames = new Map<string, Map<string, string>>();
  for (const m of listed) {
    const key = groupKey(m.provider, providers);
    if (key === m.provider) continue;
    const names = otherNames.get(key) ?? new Map<string, string>();
    for (const a of aliasesOf(m)) names.set(a, m.model);
    otherNames.set(key, names);
  }
  for (const m of slotFirst) {
    const key = groupKey(m.provider, providers);
    let d = drafts.get(key);
    if (!d) {
      d = { key, label: "", entries: [], ids: new Map(), size: Number.POSITIVE_INFINITY };
      drafts.set(key, d);
      order.push(key);
    }
    const same = otherNames.get(key)?.get(m.model);
    if (d.ids.has(m.model) || (same !== undefined && same !== m.model)) continue;
    const sent = origin[valueOf(m.provider, m.model)] ?? valueOf(m.provider, m.model);
    const i = sent.indexOf("::");
    const entry: TryOnModel = {
      provider: sent.slice(0, i),
      model: sent.slice(i + 2),
      label: modelRowName(m, catalog),
      title: sent.replace("::", " · "),
      place: placeOf(m),
      row: m,
    };
    d.ids.set(m.model, d.entries.length);
    d.entries.push(entry);
    if (typeof m.size_b === "number") d.size = Math.min(d.size, m.size_b);
  }
  const groups: (TryOnGroup & { size: number; rank: number; at: number })[] = [];
  for (const [at, key] of order.entries()) {
    const d = drafts.get(key)!;
    let entries = d.entries;
    // The server alias is no second model beside the server's own real one.
    if (isOwnServerSlot(key) && entries.some((e) => !isServerAlias(e.model) && e.place === "local")) {
      entries = entries.filter((e) => !isServerAlias(e.model));
    }
    if (!entries.length) continue;
    // Local first inside a group that runs in both places (stable).
    const ranked = [...entries].sort((a, b) => (PROVIDER_RANK[a.place] ?? 3) - (PROVIDER_RANK[b.place] ?? 3));
    const places = new Set(ranked.map((e) => e.place));
    const place: TryOnGroup["place"] =
      places.size === 1 ? ranked[0].place : places.has("local") ? "mixed" : "cloud";
    groups.push({
      id: key,
      label: groupLabel(key, ranked[0].row, catalog),
      place,
      models: ranked,
      size: d.size,
      rank: PROVIDER_RANK[ranked[0].place] ?? 3,
      at,
    });
  }
  groups.sort((a, b) => a.rank - b.rank || a.size - b.size || a.label.localeCompare(b.label) || a.at - b.at);
  return groups.map(({ id, label, place, models }) => ({ id, label, place, models }));
}

/** How many of a group's models are picked. */
export function pickedIn(group: TryOnGroup, picks: readonly TryOnPick[]): number {
  return group.models.filter((m) => picks.some((p) => p.provider === m.provider && p.model === m.model)).length;
}

/** A typed word's matches, across every group: the model's words, its id,
 *  its provider's name. At most `max`; `more` counts the rest. */
export function matchTryOn(
  groups: readonly TryOnGroup[],
  query: string,
  max: number,
): { hits: { group: TryOnGroup; model: TryOnModel }[]; more: number } {
  const q = query.trim().toLowerCase();
  const hits: { group: TryOnGroup; model: TryOnModel }[] = [];
  if (!q) return { hits, more: 0 };
  let more = 0;
  for (const group of groups) {
    for (const model of group.models) {
      const hay = `${model.label} ${model.model} ${model.provider} ${group.label}`.toLowerCase();
      if (!hay.includes(q)) continue;
      if (hits.length < max) hits.push({ group, model });
      else more += 1;
    }
  }
  return { hits, more };
}

/** The entry a pick is (its provider row and its model), or null when no
 *  group lists it — a pick saved before the list changed. */
export function pickEntry(
  groups: readonly TryOnGroup[],
  pick: { provider: string; model: string },
): { group: TryOnGroup; model: TryOnModel } | null {
  for (const group of groups) {
    const model = group.models.find((m) => m.provider === pick.provider && m.model === pick.model);
    if (model) return { group, model };
  }
  return null;
}

/** Where a pick runs, for the picked list ("Opus 5.5 · Claude Code"). */
export function pickWhere(groups: readonly TryOnGroup[], pick: TryOnPick): string {
  return pickEntry(groups, pick)?.group.label ?? "";
}
