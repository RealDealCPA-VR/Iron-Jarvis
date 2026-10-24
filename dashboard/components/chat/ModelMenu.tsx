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
 *
 * THE MENU FITS THE WINDOW (2026-10-24; the user: "in chat the module, the
 * model selection is cut off"). It used to hang above the chip at its full
 * height inside the composer, so on a new chat (the composer in the middle
 * of the window) its top — the filter box and Recent — ran past the window's
 * top and was clipped by the chat's scrolling section before that. It is now
 * drawn on the page's top layer (a portal, `position: fixed` at the chip, as
 * the permission chip's menu is): above the chip when it fits there, else on
 * the side with more room (lib/modelMenuPlacement), its height capped to that
 * room. The filter box (and the local-only line) stay at its top and "Make
 * this my default" at its bottom; the rows between them scroll. It is placed
 * again when it opens, when the window changes size, when the catalog changes
 * (models load late) and follows the chip when the page scrolls. A provider's
 * model panel floats beside its row (components/chat/FitPanel `float`), since
 * the scrolling list would clip it. The IJ harness's phone sheet keeps its
 * own place along the window's bottom.
 */

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
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
import {
  MENU_WIDTH_PX,
  appTopLimit,
  menuViewport,
  modelMenuPlacement,
  samePlacement,
  type ModelMenuPlacement,
} from "@/lib/modelMenuPlacement";

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

/** The menu, at the chip on the page's top layer (its edges and its height
 *  come from lib/modelMenuPlacement). A column: the filter box on top, the
 *  rows scrolling under it, "Make this my default" at the bottom. */
const MODEL_MENU =
  "fixed z-50 flex w-60 max-w-[calc(100vw-1rem)] flex-col rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40";
/** The open menu's id, named by the chip (`aria-controls`): on the page's
 *  top layer the menu is no longer the element after the chip, so a screen
 *  reader is told where it is. One menu is open at a time. */
export const MODEL_MENU_ID = "ij-model-menu";
/** The rows between the filter box and the footer: the part that scrolls. */
const MODEL_MENU_LIST = "min-h-0 overflow-y-auto overscroll-contain";

/** The menu's edges and height for a placement. Before the first placement
 *  (one layout pass, before paint) it sits at the window's corner to be
 *  measured — never hidden: the filter box takes the focus in that same
 *  pass, and a hidden box cannot. The phone sheet: its classes place it. */
function menuStyle(place: ModelMenuPlacement | "sheet" | null): CSSProperties | undefined {
  if (place === "sheet") return undefined;
  if (!place) return { left: 0, top: 0 };
  return place.side === "above"
    ? { left: place.left, bottom: place.bottom, maxHeight: place.maxHeight }
    : { left: place.left, top: place.top, maxHeight: place.maxHeight };
}
/** v1.376.0 (IJ harness): below `sm` the menu is a sheet along the window's
 *  bottom edge that scrolls inside — with a company's ways and accounts (and
 *  a model panel opened under its row) it is taller than the room above the
 *  chip, and it ran past the window's top at 390 px (measured in a real
 *  browser, e2e/ij-harness.spec.ts). */
const MODEL_MENU_PHONE_SHEET =
  "max-sm:fixed max-sm:inset-x-2 max-sm:bottom-2 max-sm:mb-0 max-sm:w-auto max-sm:max-h-[70dvh] max-sm:overflow-y-auto";
/** The sheet's width: below it, the harness menu is a sheet. */
const PHONE_QUERY = "(max-width: 639.98px)";

/** A provider's model panel: beside its row. */
const MODEL_PANEL =
  "absolute right-full z-30 mr-1 w-56 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40";
/** v1.376.0 (IJ harness): below `sm` the panel opens UNDER its row, inside
 *  the menu — beside it, a phone's window has no room on the left (the
 *  menu sits by Send at the right edge), so an account's models could not
 *  be reached. The chat's menu does the same (review, 2026-10-24): beside
 *  the menu the panel was shifted back into the window, over the rows it
 *  belongs to. */
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
  // Each opening starts from an empty box, no open panel and the last picks.
  // Reset WHILE RENDERING the opening (React's "adjust state when a prop
  // changes"), not in an effect: the menu is measured before paint to choose
  // where it goes, and an effect's reset landed after that measurement — a
  // menu reopened on a typed word was placed for one row, then drew all of
  // them squeezed into the room on the wrong side.
  const [openedFor, setOpenedFor] = useState(false);
  if (open !== openedFor) {
    setOpenedFor(open);
    if (open) {
      setFilter("");
      setSub(null);
      setRecent(readRecentModels());
    }
  }
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
  // v1.376.0: on a phone the harness menu is a sheet that scrolls, and a
  // model panel opens under its row: bring the panel into view.
  const menuRef = useRef<HTMLDivElement>(null);
  /** Review (harness only): Escape closes the menu from ANY row (a phone's
   *  sheet is all rows) and hands focus back to the chip that opened it. */
  const chipRef = useRef<HTMLButtonElement>(null);
  const closeToChip = () => {
    onOpenChange(false);
    setSub(null);
    chipRef.current?.focus();
  };
  /** The menu is on the page's top layer — the END of the page in tab order —
   *  so Tab keeps the order it had when the menu sat right after the chip:
   *  Shift+Tab from its first control goes back to the chip, and Tab from its
   *  last moves on from the chip (to the next control in the box) instead of
   *  out of the page. The menu stays open either way, as it did. */
  const tabAcrossTheEdge = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      e.currentTarget.querySelectorAll<HTMLElement>("input:not([disabled]), button:not([disabled])"),
    );
    if (items.length === 0) return;
    const at = document.activeElement;
    if (e.shiftKey && at === items[0]) {
      e.preventDefault();
      chipRef.current?.focus();
    } else if (!e.shiftKey && at === items[items.length - 1]) {
      // No preventDefault: the browser's own Tab then moves on from the chip.
      chipRef.current?.focus();
    }
  };
  useEffect(() => {
    if (!sub || typeof window === "undefined") return;
    if (!window.matchMedia?.(PHONE_QUERY)?.matches) return;
    const panel = menuRef.current?.querySelector<HTMLElement>('[data-testid="model-sub"]');
    // The row AND its panel (the panel is capped to fit under its row in
    // what the list shows — components/chat/FitPanel), not the panel alone,
    // which scrolled the row it belongs to out of sight.
    const rowAndPanel = panel?.parentElement ?? panel;
    rowAndPanel?.scrollIntoView?.({ block: "nearest" });
  }, [sub]);
  useEffect(() => {
    if (!open) return;
    // The menu is portaled: it is not inside the chip's wrapper, so a press
    // in it is checked on its own.
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!popRef.current?.contains(t) && !menuRef.current?.contains(t)) {
        onOpenChange(false);
        setSub(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, onOpenChange]);

  // WHERE THE MENU GOES (lib/modelMenuPlacement). `null` = not placed yet
  // (measured at the window's corner for the one layout pass before paint);
  // "sheet" = the IJ harness's phone sheet, whose classes place it along the
  // window's bottom.
  const listRef = useRef<HTMLDivElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const [place, setPlace] = useState<ModelMenuPlacement | "sheet" | null>(null);
  const placeRef = useRef<ModelMenuPlacement | "sheet" | null>(null);
  /** The chip has been seen with a box this opening (it can vanish later:
   *  a question takes the composer's place). */
  const chipSeenRef = useRef(false);
  function placeMenu(redecide: boolean) {
    const chip = chipRef.current;
    const menu = menuRef.current;
    if (!chip || !menu || typeof window === "undefined") return;
    const prev = placeRef.current;
    let next: ModelMenuPlacement | "sheet";
    if (companies && window.matchMedia?.(PHONE_QUERY)?.matches) {
      next = "sheet";
    } else {
      const r = chip.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) chipSeenRef.current = true;
      const list = listRef.current;
      // Its whole height, rows and all, even while a cap holds it shorter.
      const natural = list
        ? menu.offsetHeight - list.clientHeight + list.scrollHeight
        : menu.scrollHeight + (menu.offsetHeight - menu.clientHeight);
      next = modelMenuPlacement(
        { top: r.top, bottom: r.bottom, left: r.left, right: r.right },
        menuViewport(),
        { width: menu.offsetWidth || MENU_WIDTH_PX, height: natural },
        { topLimit: appTopLimit(), keepSide: !redecide && prev && prev !== "sheet" ? prev.side : undefined },
      );
    }
    const same = next === "sheet" || prev === "sheet" ? next === prev : samePlacement(prev, next);
    if (same) return;
    placeRef.current = next;
    setPlace(next);
  }
  // On open, and when the catalog changes under an open menu (the models
  // load after it opened), the side is chosen again from the menu's real
  // height — before paint. A typed filter keeps the side it opened on: the
  // cap is the room on that side, whatever the rows.
  const listKey = `${models.length}:${companies?.models.length ?? -1}:${companies?.entries.length ?? -1}`;
  const hasLocalOnlyLine = !!localOnlyLine;
  useLayoutEffect(() => {
    if (!open) {
      placeRef.current = null;
      chipSeenRef.current = false;
      setPlace(null);
      return;
    }
    placeMenu(true);
    // `placeMenu` reads refs and this render's props.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, listKey, hasLocalOnlyLine]);
  // The filter box takes the focus once per opening (it used to be an
  // `autoFocus`), WITHOUT scrolling anything to it: the menu is on the top
  // layer, so nothing behind it needs to move. Inside the composer, the
  // focus scrolled the chat's clipped section to "show" a box above it.
  useLayoutEffect(() => {
    if (open) filterRef.current?.focus({ preventScroll: true });
  }, [open]);
  // The menu is fixed to the window: it follows the chip when the page
  // scrolls, is placed again when the window changes size, and closes when
  // the chip goes away under it.
  //
  // Review (2026-10-24): a scroll and a resize are not the only things that
  // move the chip. "Make this my default" ends the demo strip above the page,
  // a notice comes or goes above the composer — the chip moves, no event
  // fires, and a fixed menu stays where the chip WAS. So while the menu is
  // open the chip's box is read once a frame (one read; the menu is placed
  // again only when the box changed), which also sees the chip go away.
  useEffect(() => {
    if (!open) return;
    const follow = (e: Event) => {
      if (menuRef.current && e.target instanceof Node && menuRef.current.contains(e.target)) return;
      placeMenu(false);
    };
    const resized = () => placeMenu(true);
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", resized);
    let frame = 0;
    let last = "";
    const watch = () => {
      const chip = chipRef.current;
      if (chip) {
        const r = chip.getBoundingClientRect();
        const box = `${r.left},${r.top},${r.width},${r.height}`;
        if (box !== last) {
          last = box;
          if (r.width > 0 && r.height > 0) placeMenu(false);
          else if (chipSeenRef.current) {
            onOpenChange(false);
            setSub(null);
            return;
          }
        }
      }
      frame = window.requestAnimationFrame(watch);
    };
    if (typeof window.requestAnimationFrame === "function") frame = window.requestAnimationFrame(watch);
    return () => {
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", resized);
      if (frame) window.cancelAnimationFrame(frame);
    };
    // `placeMenu` and `onOpenChange` are read at the event; this runs once
    // per opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /** The ONE way a menu row picks: the caller's choice, the menu, the memory.
   *  v1.376.0: a row under an Iron-Proxy account names it (harness only). */
  function pick(v: string, account?: string) {
    if (account) onPick(v, account);
    else onPick(v);
    onOpenChange(false);
    setSub(null);
    if (v) setRecent(rememberRecentModel(v));
    // The IJ harness reads its keys (Esc stops a reply) by where the focus
    // is: the row picked was inside the harness until the menu moved to the
    // page's top layer, so the focus goes back to the chip, inside it.
    if (companies) chipRef.current?.focus();
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
       or opens it downward when a row sits high). It FLOATS
       (fixed at its row): the menu's rows scroll, and the
       scrolling list would clip a panel hanging out of it.
       The harness's phone sheet keeps it under its row. */
    <FitPanel
      testId="model-sub"
      className={`${MODEL_PANEL} ${MODEL_PANEL_PHONE_INLINE}`}
      float
      inlineQuery={PHONE_QUERY}
      refitKey={place}
    >
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
        ref={chipRef}
        type="button"
        onClick={() => {
          onOpenChange(!open);
          setSub(null);
        }}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="true"
        aria-controls={open ? MODEL_MENU_ID : undefined}
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
      {open &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={menuRef}
            id={MODEL_MENU_ID}
            data-testid="model-menu"
            data-side={place && place !== "sheet" ? place.side : undefined}
            className={companies ? `${MODEL_MENU} ${MODEL_MENU_PHONE_SHEET}` : MODEL_MENU}
            style={menuStyle(place)}
            onKeyDown={(e) => {
              // An IME composing a word owns its keys (its Escape drops the word).
              if (e.nativeEvent.isComposing || e.keyCode === 229) return;
              if (e.key === "Tab") {
                tabAcrossTheEdge(e);
                return;
              }
              if (companies && (e.ctrlKey || e.metaKey) && (e.key ?? "").toLowerCase() === "k") {
                // The IJ harness's Ctrl+K is the model menu (lib/harnessKeys),
                // never the app's palette: inside the harness the harness took
                // the key, and the menu is on the page's top layer now — so
                // the press stops here, from the box or from any row.
                e.preventDefault();
                e.stopPropagation();
                return;
              }
              if (!companies || e.key !== "Escape") return;
              e.preventDefault();
              e.stopPropagation();
              closeToChip();
            }}
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
                // Enter that finishes an IME word is the IME's, not a pick.
                if (e.nativeEvent.isComposing || e.keyCode === 229) return;
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
              ref={filterRef}
              className="mb-1 w-full rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/40"
            />
            {/* The rows: the part of the menu that scrolls (the filter box
                above and "Make this my default" below stay put). */}
            <div ref={listRef} data-testid="model-menu-list" className={MODEL_MENU_LIST}>
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
            </div>
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
          </div>,
          document.body,
        )}
    </div>
  );
}
