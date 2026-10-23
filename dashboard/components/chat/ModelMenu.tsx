"use client";

/**
 * The composer's MODEL chip and REASONING chip (v1.369.0, IJ harness Phase 6).
 *
 * Lifted out of the chat page, markup for markup, so Build's IJ harness
 * composer draws the SAME two chips instead of a copy: the model chip with its
 * menu (type to find, the last picks first, providers local first with where
 * each runs, a provider's models in a flyout, "Make this my default"), and the
 * reasoning level chip, drawn only for a model that offers levels.
 *
 * The page owns what a pick MEANS (its `choice`, its setup save, its default
 * note); this file owns the menu: its sub-menu, its filter and its recent
 * rows, and closing on an outside press. `open` is the page's state, because
 * the page reads it too (the jump pill steps aside while a composer menu is
 * open, and "Switch model" in Ctrl K opens it).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, CircleAlert } from "lucide-react";
import { FitPanel } from "@/components/chat/FitPanel";
// v1.376.0: the IJ harness's company groups and accounts — TYPES only, so
// the chat page's menu never loads lib/harnessModelMenu.
import type { CompanyMenu, MenuAccount, MenuMatch, MenuWay } from "@/lib/harnessModelMenu";
import { ModelRowChips, modelText, modelTitle } from "@/components/ModelRowBits";
import { composerChipClass } from "@/lib/composerChips";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { modelGroupName, providerDisplay } from "@/lib/onboarding";
import { matchModels, readRecentModels, rememberRecentModel } from "@/lib/recentModels";
import {
  reasoningChipLabel,
  reasoningDefaultOf,
  reasoningDefaultOption,
  reasoningTitle,
} from "@/lib/reasoningChip";
import type { ModelOption } from "@/lib/types";

/** v1.263.0: the levels the composer may offer — the daemon's vocabulary. */
export const REASONING_LEVELS = ["low", "medium", "high"];

/** v1.277.0: how many rows a typed model filter lists. */
export const MODEL_FILTER_MAX = 12;

/** A pick's value is "provider::model"; "" is the server's default. */
export function splitChoice(choice: string): { provider?: string; model?: string } {
  const i = choice.indexOf("::");
  if (i === -1) return {};
  const provider = choice.slice(0, i);
  const model = choice.slice(i + 2);
  return provider && model ? { provider, model } : {};
}

/** The catalog row a pick (or, for "", the default) runs on. */
export function reasoningRowFor(
  choice: string,
  models: readonly ModelOption[],
  defaultProviderId: string,
  defaultModelName: string,
): ModelOption | undefined {
  const { provider, model } = choice
    ? splitChoice(choice)
    : { provider: defaultProviderId, model: defaultModelName };
  if (!provider || !model) return undefined;
  return models.find((m) => m.provider === provider && m.model === model);
}

/** The levels that row offers, in the daemon's vocabulary. */
export function reasoningLevelsOf(row: ModelOption | undefined): string[] {
  return (row?.reasoning ?? []).filter((l) => REASONING_LEVELS.includes(l));
}

/** v1.330.0: the level the row runs at when nothing is sent ("" = unknown). */
export function reasoningDefaultOfRow(row: ModelOption | undefined): string {
  return reasoningDefaultOf(row);
}

export interface ProviderRow {
  id: string;
  label: string;
  kind: string;
  available: boolean;
  size: number;
}

/**
 * Providers for the composer's model menu, LOCAL FIRST (v1.148.0).
 *
 * The menu used to be whatever order the daemon happened to return, with no
 * indication of where anything ran — a 14B on your own box and a metered
 * frontier model read identically. Order is now: your own hardware, then
 * flat-rate subscription CLIs, then metered APIs, then anything currently
 * offline; within local, smallest model first, matching how the router now
 * escalates. `kind` comes from the daemon (one definition, shared with
 * /health), so the label can never disagree with the routing.
 */
export function modelProvidersOf(models: readonly ModelOption[]): ProviderRow[] {
  const seen = new Map<string, ProviderRow>();
  for (const m of models) {
    // v1.230.0 (U5): a keyless API provider served through the logged-in
    // CLI (`inherited_from`) is flat-rate — it ranks and labels as a CLI
    // ("included"), never "metered". The daemon names the CLI; /health and
    // /connections carry the same answer.
    // v1.352.0 re-review (G21): an endpoint the daemon classes "local" (a
    // fleet node, a custom URL) but says is OFF the user's network (a cloud
    // API added on Connections) runs in the cloud: its badge says so, as
    // the reply's receipt does (`on_this_network`, the same predicate),
    // and it ranks with the cloud rows. Absent (an older daemon): the
    // class decides, as before.
    const kind = m.inherited_from
      ? "cli"
      : m.kind === "local" && m.on_this_network === false
        ? "endpoint"
        : (m.kind ?? "api");
    const size = typeof m.size_b === "number" ? m.size_b : Number.POSITIVE_INFINITY;
    const prev = seen.get(m.provider);
    if (!prev) {
      seen.set(m.provider, {
        id: m.provider,
        // v1.353.0 (G57): a plain name ("Claude Code", "Your model
        // server"), an endpoint by the label the user gave it — never a
        // raw provider id like "claude-cli" or "opencode-cli".
        label: modelGroupName(m),
        kind,
        available: m.available !== false,
        size,
      });
    } else {
      // A provider is "available" if ANY of its models is, and sorts by its
      // SMALLEST model — the rung the router would reach for first.
      prev.available = prev.available || m.available !== false;
      prev.size = Math.min(prev.size, size);
    }
  }
  const RANK: Record<string, number> = { local: 0, cli: 1, api: 2, endpoint: 2 };
  return [...seen.values()].sort((a, b) => {
    if (a.available !== b.available) return a.available ? -1 : 1; // offline last
    const ra = RANK[a.kind] ?? 3;
    const rb = RANK[b.kind] ?? 3;
    if (ra !== rb) return ra - rb;
    if (a.size !== b.size) return a.size - b.size; // smallest local rung first
    return a.label.localeCompare(b.label);
  });
}

/** Badge for a provider row: where it runs, and for a cloud model how it
 *  is paid for (v1.352.0, G21: "included" was a billing word for a model
 *  that runs in the cloud; the receipt under a reply says "local" /
 *  "cloud" in the same words). The title says the cost in full. */
export const KIND_BADGE: Record<string, { text: string; cls: string; title: string }> = {
  local: {
    text: "local",
    cls: "text-tone-success",
    title: "Runs on your own hardware: this PC or a machine on your network. No charge per message.",
  },
  cli: {
    text: "cloud · on your plan",
    cls: "text-tone-info",
    title: "Runs in the cloud through a subscription you already have. No charge per message; your plan's limits apply.",
  },
  api: {
    text: "cloud · pay per use",
    cls: "text-zinc-500",
    title: "Runs in the cloud on your API key. Each message is billed to that account.",
  },
  // v1.352.0 re-review: an endpoint you added that is not on your network.
  endpoint: {
    text: "cloud",
    cls: "text-zinc-500",
    title: "Runs on a server outside your network, at an address you added on Connections. That service sets any charge.",
  },
};

/** The words on the model chip: what it shows, the record behind it (the
 *  chip's inner title) and whether it is the default. */
export interface ModelTrigger {
  text: string;
  raw: string;
  isDefault: boolean;
  /** v1.376.0 (IJ harness): the Iron-Proxy account the session runs on,
   *  shown after the model ("Opus 4.8 · Work"). Absent = no account part. */
  account?: string;
}

/**
 * v1.300.0: the trigger says the picker's label ("Opus 5.5") for a pick
 * whose catalog row carries one; every other case is the plain label.
 * v1.314.0 (composer-footer-jargon): the DEFAULT gets the same lookup, read
 * as "Default: Claude Opus 4.8" — the raw id only when the catalog has no
 * label for it (nothing invented). A default on the scripted demo model is
 * named as that, never as the model id it pretends to be. The raw id the
 * turn will run on stays in `raw` (the trigger's inner title).
 * v1.329.0 (calm chat F7): the chip SHOWS only the name ("Opus 4.8");
 * "Default:" stays in the words a screen reader hears (an sr-only span,
 * `isDefault`) and in the title.
 */
export function modelTriggerOf(
  choice: string,
  models: readonly ModelOption[],
  defaultProviderId: string,
  defaultModelName: string,
): ModelTrigger {
  let label: string;
  if (!choice) {
    label = defaultModelName ? `default · ${defaultModelName}` : "default model";
  } else {
    const { model } = splitChoice(choice);
    label = model || choice.replace("::", " · ");
  }
  let text: string;
  if (!choice) {
    if (!defaultModelName) text = label;
    else if (defaultProviderId === "mock") text = providerDisplay("mock");
    else {
      const row = models.find((m) => m.provider === defaultProviderId && m.model === defaultModelName);
      // v1.359.0 (lane 9B): the menu row's own words (modelText), so the
      // chip never says "subscription" for the row that read "Your Claude
      // plan"; a default the catalog does not list is named the same way.
      text = modelText(row ?? { provider: defaultProviderId, model: defaultModelName }, models);
    }
  } else {
    const { provider, model } = splitChoice(choice);
    const row = models.find((m) => m.provider === provider && m.model === model);
    // v1.329.0: no label → the name people say ("Opus 4.8"), derived only
    // from what the id spells; an id it cannot read stays as it is.
    // v1.359.0 integrate (lane 9B review): a pick /models does not list (yet:
    // a restored thread while the catalog loads, or an endpoint since
    // removed) reads in the menu's words too, never "subscription".
    if (row) text = modelText(row, models);
    else text = provider && model ? modelText({ provider, model }, models) : friendlyModelName(label);
  }
  const raw = !choice
    ? defaultModelName
      ? [defaultProviderId, defaultModelName].filter(Boolean).join(" · ")
      : ""
    : choice.replace("::", " · ");
  return { text, raw, isDefault: !choice && !!defaultModelName };
}

/**
 * The reasoning level chip (v1.263.0), ONLY for a model that offers one (the
 * daemon's catalog says which; the caller draws nothing otherwise).
 * v1.330.0: the chip's WORDS are this span; the native select lies invisibly
 * over it (same box), so its menu can say "Default (Medium)" while the chip
 * says "Medium". The select keeps its name, value, keyboard and phone picker.
 */
export function ReasoningChip({
  levels,
  value,
  def,
  disabled = false,
  onChange,
}: {
  /** The levels the model offers (never empty: the caller checks). */
  levels: readonly string[];
  /** The picked level ("" = send nothing; the model's default). */
  value: string;
  /** The model's own default level ("" = unknown). */
  def: string;
  disabled?: boolean;
  onChange: (level: string) => void;
}) {
  const picked = levels.includes(value) ? value : "";
  return (
    <span
      className={`relative inline-flex h-[30px] shrink-0 items-center rounded-lg pl-2 pr-6 text-[13px] text-zinc-400 transition-colors has-[:focus-visible]:ring-1 has-[:focus-visible]:ring-accent/50 ${
        disabled ? "opacity-40" : "hover:bg-white/[0.06] hover:text-zinc-200"
      }`}
    >
      <span aria-hidden data-testid="reasoning-chip-words" className="max-w-[10rem] truncate whitespace-nowrap">
        {reasoningChipLabel(picked, def)}
      </span>
      <select
        aria-label="Reasoning level"
        data-testid="reasoning-level"
        value={picked}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        title={reasoningTitle(def)}
        className="absolute inset-0 h-full w-full cursor-pointer appearance-none rounded-lg border-0 bg-transparent text-[13px] opacity-0 focus-visible:outline-none disabled:cursor-default"
      >
        {/* v1.326.0: short words for a chip beside the model ("High"); the
            select's name says what they measure. v1.330.0: the "send
            nothing" row names the model's own default when the daemon knows
            it ("Default (Medium)"; it leads with "Default" so it never reads
            as a twin of the explicit "Medium" row). */}
        <option value="" className="bg-ink-900 text-zinc-200">
          {reasoningDefaultOption(def)}
        </option>
        {levels.map((lvl) => (
          <option key={lvl} value={lvl} className="bg-ink-900 text-zinc-200">
            {lvl.charAt(0).toUpperCase() + lvl.slice(1)}
          </option>
        ))}
      </select>
      <ChevronDown size={12} aria-hidden className="pointer-events-none absolute right-2 text-zinc-500" />
    </span>
  );
}

/** v1.376.0: an account's state in the IJ harness's menu — its words, and a
 *  second cue beyond colour: a mark for a state that asks the user to act,
 *  a small meter for a percent the vendor sent. */
function AccountState({ state }: { state: MenuAccount["state"] }) {
  if (!state.text) return null;
  return (
    <span
      data-testid="model-account-state"
      data-kind={state.kind}
      className={`flex min-w-0 items-center gap-1 text-[11px] ${state.warn ? "text-tone-warn" : "text-zinc-500"}`}
    >
      {state.warn ? <CircleAlert size={11} aria-hidden data-testid="model-account-warn" className="shrink-0" /> : null}
      {typeof state.percent === "number" ? (
        <span aria-hidden data-testid="model-account-meter" className="h-1 w-6 shrink-0 overflow-hidden rounded-full bg-white/[0.08]">
          <span
            className={`block h-full rounded-full ${state.warn ? "bg-tone-warn" : "bg-accent"}`}
            style={{ width: `${Math.max(2, Math.min(100, state.percent))}%` }}
          />
        </span>
      ) : null}
      {/* Wraps rather than cuts: "resets 4:12 PM" is the part that matters. */}
      <span className="min-w-0 break-words">{state.text}</span>
    </span>
  );
}

/** A provider's model panel: beside its row. */
const MODEL_PANEL =
  "absolute right-full z-30 mr-1 w-56 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40";
/** v1.376.0 (IJ harness): below `sm` the panel opens UNDER its row, inside
 *  the menu — beside it, a phone's window has no room on the left (the
 *  menu sits by Send at the right edge), so an account's models could not
 *  be reached. */
const MODEL_PANEL_PHONE_INLINE = "max-sm:static max-sm:mr-0 max-sm:mt-0.5 max-sm:w-full";

/** Why a harness filter / Recent row cannot be picked ("" = it can). */
function matchBlocked(m: MenuMatch): string {
  if (m.row.available === false) return `${m.group} isn't connected`;
  if (m.account && !m.account.pickable) return `${m.account.name}: ${m.account.state.text}. ${m.account.reason}`;
  return "";
}

/** The model chip and its menu. */
export function ModelMenu({
  open,
  onOpenChange,
  choice,
  models,
  trigger,
  onPick,
  disabled = false,
  disabledTitle = "Start a new chat to switch models",
  localOnlyLine,
  onMakeDefault,
  defaultNote = "",
  companies,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The current pick ("provider::model", "" = the default). */
  choice: string;
  /** The catalog (lib/useModels — the whole list, unavailable rows greyed). */
  models: ModelOption[];
  trigger: ModelTrigger;
  /** A row was picked ("" = the default model). The menu closes itself.
   *  v1.376.0: `account` — the Iron-Proxy account the row was picked under
   *  (the IJ harness's menu only; the chat's picks never carry one). */
  onPick: (value: string, account?: string) => void;
  disabled?: boolean;
  disabledTitle?: string;
  /** v1.332.0: the one line a "local only" conversation adds to the menu. */
  localOnlyLine?: string | null;
  /** "Make this my default": the caller saves it (absent = not offered). */
  onMakeDefault?: () => void;
  /** What the last "Make this my default" said. */
  defaultNote?: string;
  /** v1.376.0 (IJ harness only): one group per COMPANY, the ways to reach
   *  it, and the Iron-Proxy accounts (lib/harnessModelMenu). Absent = the
   *  chat's menu, one row per provider, exactly as before. */
  companies?: CompanyMenu;
}) {
  const popRef = useRef<HTMLDivElement>(null);
  const [sub, setSub] = useState<string | null>(null);
  // v1.277.0: FIND A MODEL BY TYPING, AND THE LAST PICKS FIRST. The tree below
  // is 12 providers deep on the daily driver; a typed word lists every model
  // whose id, provider or name contains it (Enter picks the first), and an
  // empty box opens on the last three picks. Both are read when the menu
  // opens, never per keystroke of the composer (S-05).
  const [filter, setFilter] = useState("");
  const [recent, setRecent] = useState<string[]>([]);
  useEffect(() => {
    if (!open) return;
    setFilter("");
    setSub(null);
    setRecent(readRecentModels());
  }, [open]);
  const matches = useMemo(() => matchModels(models, filter, MODEL_FILTER_MAX), [models, filter]);
  const providers = useMemo(() => modelProvidersOf(models), [models]);
  // v1.376.0 (IJ harness): the rows listed (a keyless API folded into the
  // CLI that serves it), the pick as that list spells it, and the filter and
  // Recent rows, each on the account it runs on. The chat: its own catalog
  // and pick, untouched.
  const listed = companies ? companies.models : models;
  const chosen = companies ? companies.choice : choice;
  const companyMatches = useMemo(
    () => (companies ? companies.match(filter, MODEL_FILTER_MAX) : []),
    [companies, filter],
  );
  const companyRecent = useMemo(() => (companies ? companies.recent(recent) : []), [companies, recent]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!popRef.current?.contains(e.target as Node)) {
        onOpenChange(false);
        setSub(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, onOpenChange]);

  /** The ONE way a menu row picks: the caller's choice, the menu, the memory.
   *  v1.376.0: a row under an Iron-Proxy account names it (harness only). */
  function pick(v: string, account?: string) {
    if (account) onPick(v, account);
    else onPick(v);
    onOpenChange(false);
    setSub(null);
    if (v) setRecent(rememberRecentModel(v));
  }

  /** Where a provider runs, and how it is paid for (a provider row and, in
   *  the harness, a way to reach a company). */
  const kindBadge = (kind: string, available: boolean) => (
    <span
      data-testid="model-kind-badge"
      title={available ? (KIND_BADGE[kind] ?? KIND_BADGE.api).title : undefined}
      className={`shrink-0 text-[10px] ${(KIND_BADGE[kind] ?? KIND_BADGE.api).cls}`}
    >
      {available ? (KIND_BADGE[kind] ?? KIND_BADGE.api).text : "offline"}
    </span>
  );

  /** A provider's models in its side panel. `account` (harness only): the
   *  Iron-Proxy account they are listed under — a pick switches to it, and a
   *  row is marked only under the account the session runs on. */
  const modelRows = (provider: string, account?: MenuAccount) =>
    listed
      .filter((m) => m.provider === provider)
      .map((m) => {
        const v = `${m.provider}::${m.model}`;
        const on = chosen === v && (!account || account.current);
        return (
          <button
            key={v}
            type="button"
            onClick={() => pick(v, account?.id)}
            className={`flex w-full items-center rounded-lg px-2.5 py-1.5 text-left font-mono text-[12px] transition-colors hover:bg-white/[0.06] ${
              on ? "text-accent-soft" : "text-zinc-300"
            }`}
          >
            <span className="min-w-0 truncate" title={modelTitle(m, models)}>
              {modelText(m, models)}
            </span>
            <span className="ml-1.5 inline-flex shrink-0 items-center gap-1">
              <ModelRowChips m={m} />
            </span>
            {on && <Check size={11} className="ml-auto shrink-0" />}
          </button>
        );
      });

  /** A model's side panel, beside the row that opened it. */
  const modelPanel = (provider: string, account?: MenuAccount) => (
    /* Anchored to the BOTTOM so a long catalog (OpenRouter)
       grows UPWARD over the chat area instead of being
       clipped at the card edge — and v1.350.1: never past
       the WINDOW's top (FitPanel caps it to the room above,
       or opens it downward when a row sits high). */
    <FitPanel testId="model-sub" className={companies ? `${MODEL_PANEL} ${MODEL_PANEL_PHONE_INLINE}` : MODEL_PANEL}>
      {modelRows(provider, account)}
    </FitPanel>
  );

  /** One provider's row and its panel (the chat's tree; in the harness every
   *  provider that is not a company's). */
  const providerRow = (p: ProviderRow) => (
    <div key={p.id} className="relative">
      <button
        type="button"
        onClick={() => setSub(sub === p.id ? null : p.id)}
        disabled={!p.available}
        title={p.available ? undefined : `${p.label} isn't connected. Set it up on Connections.`}
        aria-expanded={sub === p.id}
        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent ${
          splitChoice(chosen).provider === p.id ? "text-accent-soft" : "text-zinc-300"
        }`}
      >
        <span className="min-w-0 truncate">{p.label}</span>
        {/* Where it runs — the whole point of the reorder: a list
            you can act on without knowing which of your
            providers costs money. */}
        {kindBadge(p.kind, p.available)}
        <ChevronRight size={12} className="ml-auto shrink-0 text-zinc-500" />
      </button>
      {sub === p.id && modelPanel(p.id)}
    </div>
  );

  /** v1.376.0: a harness filter / Recent row — the model, and where it runs
   *  (the way to the company, and the account when it has one). */
  const matchRow = (m: MenuMatch, testId: string) => {
    const blocked = matchBlocked(m);
    const on = chosen === m.value && (!m.account || m.account.current);
    return (
      <button
        key={`${testId}-${m.value}-${m.account?.id ?? ""}`}
        type="button"
        data-testid={testId}
        data-account={m.account?.id}
        onClick={() => pick(m.value, m.account?.id)}
        disabled={!!blocked}
        title={blocked || undefined}
        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left font-mono text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45 ${
          on ? "text-accent-soft" : "text-zinc-300"
        }`}
      >
        <span className="min-w-0 truncate" title={modelTitle(m.row, models)}>
          {modelText(m.row, models)}
        </span>
        <ModelRowChips m={m.row} />
        <span className="ml-auto flex min-w-0 shrink items-center gap-1 font-sans text-[10px] text-zinc-500">
          <span className="min-w-0 truncate">{m.group}</span>
          {m.account?.state.warn ? (
            <CircleAlert size={10} aria-label={m.account.state.text} className="shrink-0 text-tone-warn" />
          ) : null}
        </span>
      </button>
    );
  };

  /** v1.376.0: a way to reach a company with no accounts — one row. */
  const wayRow = (w: MenuWay) => (
    <div key={w.provider} className="relative">
      <button
        type="button"
        data-testid={`model-way-${w.provider}`}
        onClick={() => setSub(sub === w.provider ? null : w.provider)}
        disabled={!w.available}
        title={w.available ? undefined : `${w.label} isn't connected. Set it up on Connections.`}
        aria-expanded={sub === w.provider}
        className={`flex w-full items-center gap-2 rounded-lg py-1.5 pl-5 pr-2.5 text-left text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent ${
          splitChoice(chosen).provider === w.provider ? "text-accent-soft" : "text-zinc-300"
        }`}
      >
        <span className="min-w-0 truncate">{w.label}</span>
        {kindBadge(w.kind, w.available)}
        <ChevronRight size={12} className="ml-auto shrink-0 text-zinc-500" />
      </button>
      {sub === w.provider && modelPanel(w.provider)}
    </div>
  );

  /** v1.376.0: a way reached through Iron-Proxy accounts — its name and how
   *  it is paid for, then ONE entry per account: name, plan, state in words,
   *  the session's own marked; one that cannot serve is shown, not picked. */
  const accountWay = (w: MenuWay, accounts: MenuAccount[]) => (
    <div key={w.provider} data-testid={`model-way-${w.provider}`}>
      <p className="flex items-center gap-2 py-1 pl-5 pr-2.5 text-[12px] text-zinc-400">
        <span className="min-w-0 truncate">{w.label}</span>
        {kindBadge(w.kind, w.available)}
      </p>
      {w.note ? <p className="pb-1 pl-5 pr-2.5 text-[11px] text-tone-warn">{w.note}</p> : null}
      {accounts.map((a) => {
        const key = `${w.provider}@${a.id}`;
        const blocked = !w.available
          ? `${w.label} isn't connected. Set it up on Connections.`
          : a.pickable
            ? ""
            : `${a.state.text}. ${a.reason}`;
        return (
          <div key={key} className="relative">
            <button
              type="button"
              data-testid={`model-account-${a.id}`}
              data-current={a.current ? "true" : undefined}
              onClick={() => setSub(sub === key ? null : key)}
              disabled={!!blocked}
              title={blocked || undefined}
              aria-expanded={sub === key}
              className={`flex w-full items-center gap-2 rounded-lg py-1.5 pl-7 pr-2.5 text-left text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent ${
                a.current ? "text-accent-soft" : "text-zinc-300"
              }`}
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="flex min-w-0 items-center gap-1">
                  <span data-testid="model-account-name" className="min-w-0 truncate">
                    {a.name}
                  </span>
                  {a.plan ? <span className="shrink-0 text-[11px] text-zinc-500">· {a.plan}</span> : null}
                  {a.current ? (
                    <>
                      <Check size={11} aria-hidden data-testid="model-account-current" className="shrink-0" />
                      <span className="sr-only"> (this session&apos;s account)</span>
                    </>
                  ) : null}
                </span>
                <AccountState state={a.state} />
              </span>
              <ChevronRight size={12} className="ml-auto shrink-0 text-zinc-500" />
            </button>
            {sub === key && modelPanel(w.provider, a)}
          </div>
        );
      })}
    </div>
  );

  return (
    <div ref={popRef} className="relative">
      <button
        type="button"
        onClick={() => {
          onOpenChange(!open);
          setSub(null);
        }}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="true"
        title={disabled ? disabledTitle : "Switch model"}
        className={`${composerChipClass(false)} max-w-[11rem] sm:max-w-[16rem]`}
      >
        <span
          data-testid="model-chip-words"
          className="min-w-0 truncate"
          title={trigger.raw ? (trigger.isDefault ? `The default model: ${trigger.raw}` : trigger.raw) : undefined}
        >
          {trigger.isDefault && <span className="sr-only">Default: </span>}
          {trigger.text}
        </span>
        {/* v1.376.0 (IJ harness): the session's account after the model
            ("Opus 4.8 · Work"), cut on its own so a long model name never
            hides it; the chip keeps its width, so Send stays in the box. */}
        {trigger.account ? (
          <span data-testid="model-chip-account" className="min-w-0 max-w-[6rem] shrink-0 truncate" title={trigger.account}>
            {" · "}
            {trigger.account}
          </span>
        ) : null}
        <ChevronDown size={12} className="shrink-0 opacity-70" />
      </button>
      {open && (
        <div
          data-testid="model-menu"
          className="absolute bottom-full right-0 z-20 mb-1.5 w-60 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40"
        >
          {/* v1.332.0: the one line a "local only" chat adds here. */}
          {localOnlyLine && (
            <p data-testid="model-menu-local-only" className="px-2.5 pb-1 pt-0.5 text-[12px] text-zinc-500">
              {localOnlyLine}
            </p>
          )}
          {/* v1.277.0: type to find a model across every provider. */}
          <input
            data-testid="model-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            onKeyDown={(e) => {
              if (companies && e.key === "Enter") {
                // v1.376.0: the harness's first row, on its account (a row
                // that cannot be picked is not picked by Enter either).
                const hit = companyMatches[0];
                if (hit) {
                  e.preventDefault();
                  if (!matchBlocked(hit)) pick(hit.value, hit.account?.id);
                }
              } else if (e.key === "Enter" && matches[0]) {
                e.preventDefault();
                pick(`${matches[0].provider}::${matches[0].model}`);
              } else if (e.key === "Escape") {
                onOpenChange(false);
                setSub(null);
              }
            }}
            placeholder="Type to find a model…"
            aria-label="Find a model"
            autoFocus
            className="mb-1 w-full rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/40"
          />
          {filter.trim() && companies ? (
            companyMatches.length === 0 ? (
              <p className="px-2.5 py-1.5 text-[12px] text-zinc-500">No model matches.</p>
            ) : (
              companyMatches.map((m) => matchRow(m, "model-match"))
            )
          ) : filter.trim() ? (
            matches.length === 0 ? (
              <p className="px-2.5 py-1.5 text-[12px] text-zinc-500">No model matches.</p>
            ) : (
              matches.map((m) => {
                const v = `${m.provider}::${m.model}`;
                return (
                  <button
                    key={`match-${v}`}
                    type="button"
                    data-testid="model-match"
                    onClick={() => pick(v)}
                    disabled={m.available === false}
                    title={m.available === false ? `${modelGroupName(m)} isn't connected` : undefined}
                    className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left font-mono text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45 ${
                      choice === v ? "text-accent-soft" : "text-zinc-300"
                    }`}
                  >
                    <span className="min-w-0 truncate" title={modelTitle(m, models)}>
                      {modelText(m, models)}
                    </span>
                    <ModelRowChips m={m} />
                    <span className="ml-auto shrink-0 font-sans text-[10px] text-zinc-500">{modelGroupName(m)}</span>
                  </button>
                );
              })
            )
          ) : companies ? (
            /* v1.376.0 (IJ harness): Recent on its accounts, the default
               model, then one group per COMPANY — the ways to reach it, and
               its Iron-Proxy accounts — and every other provider as in the
               chat. */
            <>
              {companyRecent.length > 0 && (
                <div data-testid="model-recent" className="mb-1 border-b border-white/[0.06] pb-1">
                  <p className="px-2.5 pb-0.5 text-[10px] uppercase tracking-wide text-zinc-600">Recent</p>
                  {companyRecent.map((m) => matchRow(m, "model-recent-row"))}
                </div>
              )}
              <button
                type="button"
                onClick={() => pick("")}
                className={`flex w-full items-center rounded-lg px-2.5 py-1.5 text-left text-[12px] transition-colors hover:bg-white/[0.06] ${
                  !chosen ? "text-accent-soft" : "text-zinc-300"
                }`}
              >
                default model
              </button>
              {companies.entries.map((entry) =>
                entry.type === "provider" ? (
                  providerRow(entry.row)
                ) : (
                  <div
                    key={`company-${entry.id}`}
                    role="group"
                    aria-label={entry.label}
                    data-testid="model-company"
                    data-company={entry.id}
                  >
                    <p aria-hidden className="px-2.5 pb-0.5 pt-1.5 text-[11px] font-medium text-zinc-400">
                      {entry.label}
                    </p>
                    {entry.ways.map((w) => (w.accounts ? accountWay(w, w.accounts) : wayRow(w)))}
                  </div>
                ),
              )}
            </>
          ) : (
            <>
              {recent.length > 0 && (
                <div data-testid="model-recent" className="mb-1 border-b border-white/[0.06] pb-1">
                  <p className="px-2.5 pb-0.5 text-[10px] uppercase tracking-wide text-zinc-600">Recent</p>
                  {recent.map((v) => {
                    const { provider, model } = splitChoice(v);
                    const row = models.find((m) => m.provider === provider && m.model === model);
                    if (!row) return null;
                    return (
                      <button
                        key={`recent-${v}`}
                        type="button"
                        onClick={() => pick(v)}
                        disabled={row.available === false}
                        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left font-mono text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45 ${
                          choice === v ? "text-accent-soft" : "text-zinc-300"
                        }`}
                      >
                        <span className="min-w-0 truncate" title={modelTitle(row, models)}>
                          {modelText(row, models)}
                        </span>
                        <ModelRowChips m={row} />
                        <span className="ml-auto shrink-0 font-sans text-[10px] text-zinc-500">{modelGroupName(row)}</span>
                      </button>
                    );
                  })}
                </div>
              )}
              <button
                type="button"
                onClick={() => pick("")}
                className={`flex w-full items-center rounded-lg px-2.5 py-1.5 text-left text-[12px] transition-colors hover:bg-white/[0.06] ${
                  !choice ? "text-accent-soft" : "text-zinc-300"
                }`}
              >
                default model
              </button>
              {providers.map((p) => providerRow(p))}
            </>
          )}
          {onMakeDefault && splitChoice(choice).provider && (
            <div className="mt-1 border-t border-white/[0.06] pt-1">
              <button
                type="button"
                data-testid="model-make-default"
                onClick={() => onMakeDefault()}
                className="w-full rounded-lg px-2.5 py-1.5 text-left text-[12px] text-zinc-300 hover:bg-white/[0.06]"
              >
                Make this my default
              </button>
              {defaultNote && (
                <p role="status" className="px-2.5 pb-1 text-[11px] text-zinc-500">
                  {defaultNote}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
