"use client";

/**
 * v1.475.0: ONE chip for a Build › Code session's coding tool, model and
 * effort ("Claude Code · Opus 5.5 · High"), opening ONE panel: the tools down
 * the left, the chosen tool's models in the middle (with a search box), and
 * the chosen model's own effort levels on the right. Picking a model moves
 * straight to its levels, so tool → model → effort is one short trip with the
 * mouse or the keyboard (the idea is t3code's model picker; built our way, in
 * the app's calm look).
 *
 * - A tool that cannot start stays in the list, greyed, with the reason and
 *   (`toolExtra`) the app's one Sign in press under it.
 * - In a running session the tool is fixed (`lockTool`): the panel shows only
 *   that tool's models and levels.
 * - Only the levels the MODEL takes are drawn (`effortsFor`); a "Default" row
 *   first sends nothing.
 * - A model the tool no longer lists stays named, marked "not in the list"
 *   (never swapped silently); a kept effort the model no longer lists too.
 * - `onOpen` lets the page read the tools' lists again (the page decides how
 *   often).
 * - Ctrl+Shift+M opens it (`shortcut`), arrows move, Enter picks, Esc closes.
 *
 * Test ids, from `testId` (the prefix): the chip `chipTestId` (else
 * `<p>-tool`), the panel `<p>-tool-menu`, a tool `<p>-tool-<id>` (+
 * `<p>-tool-why-<id>`), the search `<p>-model-search`, a model
 * `<p>-model-<id | default>`, an effort `<p>-effort-<level | default>`, the
 * effort column `<p>-efforts`. The chip carries `data-tool`, `data-model` and
 * `data-effort` (what is chosen).
 */

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Code2, Search } from "lucide-react";
import { effortsFor, modelGroups, toolUnavailable, type CodingTool } from "@/lib/buildApp";
import {
  MENU_EDGE_PX,
  appTopLimit,
  menuViewport,
  modelMenuPlacement,
  samePlacement,
  type ModelMenuPlacement,
} from "@/lib/modelMenuPlacement";

export interface PickerChoice {
  tool: string;
  model: string;
  effort: string;
}

export type PickerField = "tool" | "model" | "effort";

export const PICKER_SHORTCUT_WORDS = "Ctrl+Shift+M";

/** An effort level as a word ("high" → "High"). */
export function effortWord(effort: string): string {
  return effort ? effort.charAt(0).toUpperCase() + effort.slice(1) : "";
}

/** Claude Code's own short model names ("haiku"): the ONE listed model of
 *  that family, when exactly one is listed; else null (the name is shown as
 *  it is). Claude Code resolves the short name to that model itself. */
export function aliasTarget(tool: CodingTool | null, model: string): string | null {
  if (!tool || tool.id !== "claude-code" || !/^(opus|sonnet|haiku|fable)$/i.test(model)) return null;
  const fam = `claude-${model.toLowerCase()}-`;
  const hits = tool.models.filter((m) => m.id.toLowerCase().startsWith(fam));
  return hits.length === 1 ? hits[0].id : null;
}

/** A model as the chip and the list name it ("" = the tool's own default). */
export function modelName(tool: CodingTool | null, model: string, keptLabel?: string): string {
  if (!tool) return "";
  if (!model) return tool.defaultName ? `Default (${tool.defaultName})` : "Default model";
  const id = aliasTarget(tool, model) ?? model;
  return tool.models.find((m) => m.id === id)?.label || keptLabel || model;
}

/** The chip's words: tool · model · effort (the effort only when one is set). */
export function pickerWords(tool: CodingTool | null, value: PickerChoice, keptLabel?: string): string {
  if (!tool) return "Choose a coding tool";
  const parts = [tool.label, modelName(tool, value.model, keptLabel)];
  if (value.effort) parts.push(effortWord(value.effort));
  return parts.join(" · ");
}

/** Does a search match a model (its name, its id or its group)? */
export function matchesModel(m: { id: string; label: string; group?: string }, q: string): boolean {
  const s = q.trim().toLowerCase();
  if (!s) return true;
  return [m.label, m.id, m.group ?? ""].some((x) => x.toLowerCase().includes(s));
}

/** Is this key the picker's shortcut (Ctrl/Cmd + Shift + M)? */
export function isPickerShortcut(e: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }): boolean {
  return (e.ctrlKey || e.metaKey) && e.shiftKey && !e.altKey && e.key.toLowerCase() === "m";
}

interface ModelRow {
  id: string;
  label: string;
  group?: string;
  kept?: boolean;
}

/** The model rows of a tool: its own default first (when offered), then its
 *  models by group, then a kept model it no longer lists. */
export function modelRows(
  tool: CodingTool | null,
  opts: { defaultRow: boolean; defaultLabel: string; kept?: string; keptLabel?: string },
): ModelRow[] {
  if (!tool) return [];
  const out: ModelRow[] = [];
  const listsDefault = tool.models.some((m) => m.id === "");
  if (opts.defaultRow && !listsDefault) out.push({ id: "", label: opts.defaultLabel });
  for (const { group, models } of modelGroups(tool.models)) {
    for (const m of models) {
      if (m.id === "" && !opts.defaultRow) continue;
      out.push({ id: m.id, label: m.id === "" ? opts.defaultLabel : m.label, group: group || undefined });
    }
  }
  if (opts.kept && !tool.models.some((m) => m.id === opts.kept)) {
    out.push({ id: opts.kept, label: opts.keptLabel || opts.kept, kept: true });
  }
  return out;
}

/** The effort rows of a model: "Default" first (when offered), then the
 *  model's own levels, then a kept level it no longer lists. */
export function effortRows(tool: CodingTool | null, model: string, opts: { defaultRow: boolean; kept?: string }): string[] {
  const levels = effortsFor(tool, model);
  if (!levels.length && !opts.kept) return [];
  const out = opts.defaultRow ? [""] : [];
  out.push(...levels);
  if (opts.kept && !levels.includes(opts.kept)) out.push(opts.kept);
  return out;
}

const CHIP =
  "inline-flex h-7 max-w-[22rem] items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50 data-[invalid=true]:text-tone-danger";
const ROW =
  "flex w-full items-start gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] outline-none transition-colors enabled:hover:bg-white/[0.06] focus-visible:bg-white/[0.08] disabled:cursor-not-allowed disabled:opacity-50";

/** Where the panel goes (the chat's model menu's rule, lib/modelMenuPlacement):
 *  drawn on the page's top layer, fixed to its chip — above it when it fits
 *  there or there is more room above, else below — its height capped to that
 *  room (it scrolls inside), its LEFT edge at the chip's (kept inside the
 *  window). Read again on a resize or scroll while open. */
export function pickerPlacement(
  anchor: { top: number; bottom: number; left: number; right: number },
  viewport: { width: number; height: number },
  panel: { width: number; height: number },
  topLimit = 0,
): ModelMenuPlacement {
  const p = modelMenuPlacement(anchor, viewport, panel, { topLimit });
  const width = Math.min(panel.width, viewport.width - 2 * MENU_EDGE_PX);
  const left = Math.round(Math.max(MENU_EDGE_PX, Math.min(anchor.left, viewport.width - MENU_EDGE_PX - width)));
  return { ...p, left };
}

function panelStyle(place: ModelMenuPlacement | null): CSSProperties {
  if (!place) return { left: 0, top: 0, visibility: "hidden" };
  return place.side === "above"
    ? { left: place.left, bottom: place.bottom, maxHeight: place.maxHeight }
    : { left: place.left, top: place.top, maxHeight: place.maxHeight };
}

export function ModelPicker({
  tools,
  value,
  onChange,
  lockTool = false,
  disabled = false,
  testId,
  chipTestId,
  defaultModelRow = true,
  defaultModelLabel,
  defaultModelHeld,
  keptModelLabel,
  defaultEffortRow = true,
  defaultEffortLabel = "Default",
  defaultEffortHeld,
  effortHeld,
  onOpen,
  shortcut = false,
  toolExtra,
  closeAfterTool,
  title,
  invalid,
  emptyLabel,
}: {
  tools: readonly CodingTool[] | null;
  value: PickerChoice;
  /** One field at a time: the tool (the page resets model and effort), the
   *  model, or the effort. */
  onChange: (field: PickerField, next: string) => void;
  /** A running session: its tool is fixed. */
  lockTool?: boolean;
  disabled?: boolean;
  testId: string;
  chipTestId?: string;
  /** Offer the tool's own default as a model row. */
  defaultModelRow?: boolean;
  defaultModelLabel?: (tool: CodingTool) => string;
  /** The default model row cannot be picked now: the reason. */
  defaultModelHeld?: string;
  /** The name of a chosen model the tool's list does not have. */
  keptModelLabel?: string;
  defaultEffortRow?: boolean;
  defaultEffortLabel?: string;
  /** The default effort row cannot be picked now: the reason. */
  defaultEffortHeld?: string;
  /** The effort cannot be changed at all now: the reason. */
  effortHeld?: string;
  onOpen?: () => void;
  shortcut?: boolean;
  /** Under an unavailable tool's row: the page's Sign in press, if any. */
  toolExtra?: (tool: CodingTool) => ReactNode;
  /** After a tool is picked, close the panel (the page has its own next
   *  question, such as which account). */
  closeAfterTool?: (tool: CodingTool) => boolean;
  title?: string;
  invalid?: boolean;
  /** The chip's words while no tool is chosen (still looking, none ready). */
  emptyLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [browse, setBrowse] = useState(value.tool);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const chipRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const effortRef = useRef<HTMLDivElement | null>(null);
  const railRef = useRef<HTMLDivElement | null>(null);
  const id = useId();
  const [place, setPlace] = useState<ModelMenuPlacement | null>(null);

  const tool = useMemo(() => tools?.find((t) => t.id === value.tool) ?? null, [tools, value.tool]);
  const shownId = lockTool ? value.tool : browse || value.tool;
  const shown = useMemo(() => tools?.find((t) => t.id === shownId) ?? null, [tools, shownId]);
  const isCurrent = !!shown && shown.id === value.tool;
  // v1.475.0 review: a model picked here but not yet applied by the page (a
  // session's change goes to the daemon first). Its rows show and its levels
  // wait until it is the chosen model; then they can be picked.
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => {
    setPending(null);
  }, [value.model, value.tool]);
  const waitingForModel = pending !== null && pending !== value.model;
  // The model as the list knows it (Claude Code's short name → its row).
  const current = aliasTarget(tool, waitingForModel ? pending : value.model) ?? (waitingForModel ? pending : value.model);

  const allRows = useMemo(
    () =>
      modelRows(shown, {
        defaultRow: defaultModelRow,
        defaultLabel: shown ? (defaultModelLabel ? defaultModelLabel(shown) : modelName(shown, "")) : "",
        kept: isCurrent ? current : undefined,
        keptLabel: keptModelLabel,
      }),
    [shown, defaultModelRow, defaultModelLabel, isCurrent, current, keptModelLabel],
  );
  const rows = useMemo(() => allRows.filter((m) => matchesModel(m, query)), [allRows, query]);
  const levels = useMemo(
    () => (isCurrent ? effortRows(shown, current, { defaultRow: defaultEffortRow, kept: value.effort || undefined }) : []),
    [isCurrent, shown, current, value.effort, defaultEffortRow],
  );

  const openPanel = useCallback(() => {
    setBrowse(value.tool);
    setQuery("");
    setOpen(true);
    onOpen?.();
  }, [value.tool, onOpen]);

  const close = useCallback((focusChip = true) => {
    setOpen(false);
    setPending(null);
    if (focusChip) chipRef.current?.focus({ preventScroll: true });
  }, []);

  // The highlighted model: the chosen one when it is in view, else the first.
  useEffect(() => {
    if (!open) return;
    const i = rows.findIndex((r) => isCurrent && r.id === current);
    setActive(i >= 0 ? i : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, shownId, query]);

  // The search box takes the cursor once the panel is placed (it is hidden
  // for its one measuring pass, and a hidden box cannot take focus).
  const placed = place !== null;
  useEffect(() => {
    if (open && placed) searchRef.current?.focus({ preventScroll: true });
  }, [open, placed]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (boxRef.current?.contains(t) || chipRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Placed before paint on open, and again on a resize or a scroll.
  useLayoutEffect(() => {
    if (!open) {
      setPlace(null);
      return;
    }
    const placeIt = () => {
      const chipEl = chipRef.current;
      const box = boxRef.current;
      if (!chipEl || !box) return;
      const r = chipEl.getBoundingClientRect();
      const next = pickerPlacement(
        { top: r.top, bottom: r.bottom, left: r.left, right: r.right },
        menuViewport(),
        { width: box.offsetWidth, height: box.scrollHeight },
        appTopLimit(),
      );
      setPlace((prev) => (samePlacement(prev, next) ? prev : next));
    };
    placeIt();
    window.addEventListener("resize", placeIt);
    window.addEventListener("scroll", placeIt, true);
    return () => {
      window.removeEventListener("resize", placeIt);
      window.removeEventListener("scroll", placeIt, true);
    };
  }, [open]);

  useEffect(() => {
    if (!shortcut || disabled || !tools?.length) return;
    const onKey = (e: KeyboardEvent) => {
      if (!isPickerShortcut(e)) return;
      e.preventDefault();
      if (open) close();
      else openPanel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcut, disabled, tools, open, openPanel, close]);

  function defaultHeld(r: ModelRow): boolean {
    return r.id === "" && !!defaultModelHeld && value.model !== "";
  }

  function pickModel(m: string) {
    if (shown && shown.id !== value.tool) onChange("tool", shown.id);
    if (m !== current || !isCurrent) {
      if (lockTool && m !== value.model) setPending(m);
      onChange("model", m);
    }
    const next = shown ? effortRows(shown, m, { defaultRow: defaultEffortRow }) : [];
    if (next.length && !effortHeld) {
      requestAnimationFrame(() => effortRef.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus());
    } else {
      close();
    }
  }

  function pickTool(t: CodingTool) {
    if (toolUnavailable(t)) return;
    setBrowse(t.id);
    setQuery("");
    if (t.id !== value.tool) onChange("tool", t.id);
    if (closeAfterTool?.(t)) {
      setOpen(false);
      return;
    }
    requestAnimationFrame(() => searchRef.current?.focus({ preventScroll: true }));
  }

  function onListKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(rows.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      const r = rows[active];
      if (r && !defaultHeld(r)) pickModel(r.id);
    } else if (e.key === "ArrowLeft" && !lockTool && !query) {
      e.preventDefault();
      const rail = railRef.current;
      (rail?.querySelector<HTMLButtonElement>("button[aria-checked='true']") ?? rail?.querySelector<HTMLButtonElement>("button:not([disabled])"))?.focus();
    } else if (e.key === "ArrowRight" && levels.length && e.currentTarget.selectionStart === query.length) {
      e.preventDefault();
      effortRef.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
    }
  }

  function onColumnKey(e: React.KeyboardEvent, where: "rail" | "effort") {
    const box = where === "rail" ? railRef.current : effortRef.current;
    if (!box) return;
    const list = Array.from(box.querySelectorAll<HTMLButtonElement>("button:not([disabled])"));
    const at = list.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      list[Math.max(0, Math.min(list.length - 1, at + (e.key === "ArrowDown" ? 1 : -1)))]?.focus();
    } else if ((where === "rail" && e.key === "ArrowRight") || (where === "effort" && e.key === "ArrowLeft")) {
      e.preventDefault();
      searchRef.current?.focus();
    }
  }

  const chipWords = !tool
    ? (emptyLabel ?? pickerWords(null, value))
    : !value.model && defaultModelLabel
      ? [tool.label, defaultModelLabel(tool), ...(value.effort ? [effortWord(value.effort)] : [])].join(" · ")
      : pickerWords(tool, value, keptModelLabel);
  const notListed = !!tool && !!current && !tool.models.some((m) => m.id === current);
  const chipTitle = title ?? `${chipWords}${notListed ? ` (not in ${tool?.label}'s list)` : ""} · ${PICKER_SHORTCUT_WORDS}`;

  return (
    <div
      className="relative"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.preventDefault();
          e.stopPropagation();
          close();
        }
      }}
    >
      <button
        ref={chipRef}
        type="button"
        data-testid={chipTestId ?? `${testId}-tool`}
        data-tool={value.tool}
        data-model={value.model}
        data-effort={value.effort}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? `${id}-panel` : undefined}
        data-invalid={invalid ? "true" : undefined}
        aria-label={`Coding tool, model and effort: ${chipWords}`}
        disabled={disabled || !tools || tools.length === 0}
        onClick={() => (open ? close(false) : openPanel())}
        title={chipTitle}
        className={CHIP}
      >
        <Code2 size={13} aria-hidden="true" className="shrink-0" />
        <span className="truncate">{chipWords}</span>
        <ChevronDown size={12} aria-hidden="true" className="shrink-0" />
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
        <div
          ref={boxRef}
          id={`${id}-panel`}
          role="dialog"
          aria-label="Coding tool, model and effort"
          data-testid={`${testId}-tool-menu`}
          data-side={place?.side}
          style={panelStyle(place)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              close();
            } else if (e.key === "Tab") {
              // The panel lives at the end of the page: Tab leaves it and
              // goes back to the chip (arrows move inside it).
              e.preventDefault();
              close();
            }
          }}
          className={`fixed z-50 overflow-y-auto overscroll-contain ${lockTool ? "w-[26rem]" : "w-[36rem]"} max-w-[calc(100vw-1rem)] rounded-xl bg-ink-800 p-1 shadow-card`}
        >
          <div className={`grid gap-1 ${lockTool ? "sm:grid-cols-[minmax(0,1fr)_8.5rem]" : "sm:grid-cols-[10rem_minmax(0,1fr)_8.5rem]"}`}>
            {lockTool ? null : (
              <div
                ref={railRef}
                role="radiogroup"
                aria-label="Coding tool"
                data-testid={`${testId}-tools`}
                onKeyDown={(e) => onColumnKey(e, "rail")}
                className="flex flex-wrap gap-0.5 sm:max-h-80 sm:flex-col sm:flex-nowrap sm:overflow-y-auto"
              >
                {(tools ?? []).map((t) => {
                  const why = toolUnavailable(t);
                  return (
                    <div key={t.id} className="min-w-0">
                      <button
                        type="button"
                        role="radio"
                        aria-checked={t.id === value.tool}
                        data-testid={`${testId}-tool-${t.id}`}
                        data-available={why ? "false" : "true"}
                        disabled={!!why}
                        title={why ?? t.label}
                        onClick={() => pickTool(t)}
                        className={`${ROW} ${t.id === shownId ? "bg-white/[0.06] text-zinc-100" : "text-zinc-300"}`}
                      >
                        <span className="mt-0.5 w-3.5 shrink-0">{t.id === value.tool ? <Check size={13} aria-hidden="true" /> : null}</span>
                        <span className="min-w-0">
                          <span className="block truncate">{t.label}</span>
                          {why ? (
                            <span data-testid={`${testId}-tool-why-${t.id}`} className="block text-[12px] text-zinc-500">
                              {why}
                            </span>
                          ) : null}
                        </span>
                      </button>
                      {why && toolExtra ? toolExtra(t) : null}
                    </div>
                  );
                })}
              </div>
            )}
            <div className="min-w-0">
              <label className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] text-zinc-500">
                <Search size={13} aria-hidden="true" className="shrink-0" />
                <input
                  ref={searchRef}
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={onListKey}
                  data-testid={`${testId}-model-search`}
                  role="combobox"
                  aria-expanded="true"
                  aria-label={`Search ${shown?.label ?? ""} models`}
                  aria-controls={`${id}-models`}
                  aria-activedescendant={rows[active] ? `${id}-m-${active}` : undefined}
                  placeholder={`Search ${shown?.label ?? ""} models`}
                  className="min-w-0 flex-1 bg-transparent text-[13px] text-zinc-200 outline-none placeholder:text-zinc-600"
                />
              </label>
              <div
                id={`${id}-models`}
                role="listbox"
                aria-label={`${shown?.label ?? ""} models`}
                data-testid={`${testId}-models`}
                className="max-h-72 overflow-y-auto"
              >
                {rows.length === 0 ? (
                  <p className="px-2.5 py-2 text-[12px] text-zinc-500">
                    {query ? "No model matches." : `${shown?.label ?? "This tool"} has listed no models yet.`}
                  </p>
                ) : null}
                {rows.map((r, i) => {
                  const chosen = isCurrent && r.id === current;
                  const held = defaultHeld(r);
                  const groupHead = r.group && (i === 0 || rows[i - 1].group !== r.group);
                  return (
                    <div key={`${r.group ?? ""}:${r.id || "default"}`}>
                      {groupHead ? <p className="px-2.5 pb-0.5 pt-1.5 text-[11px] text-zinc-500">{r.group}</p> : null}
                      <button
                        type="button"
                        id={`${id}-m-${i}`}
                        role="option"
                        aria-selected={chosen}
                        data-testid={`${testId}-model-${r.id || "default"}`}
                        data-group={r.group}
                        data-active={i === active ? "true" : undefined}
                        disabled={held}
                        title={held ? defaultModelHeld : r.kept ? `Not in ${shown?.label}'s list` : undefined}
                        tabIndex={-1}
                        onMouseEnter={() => setActive(i)}
                        onClick={() => pickModel(r.id)}
                        className={`${ROW} ${i === active ? "bg-white/[0.06]" : ""} ${chosen ? "text-zinc-100" : "text-zinc-300"}`}
                      >
                        <span className="mt-0.5 w-3.5 shrink-0">{chosen ? <Check size={13} aria-hidden="true" /> : null}</span>
                        <span className="min-w-0 truncate">{r.label}</span>
                        {r.kept ? <span className="ml-auto shrink-0 text-[11px] text-zinc-500">not in the list</span> : null}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
            <div
              ref={effortRef}
              role="radiogroup"
              aria-label="Effort"
              data-testid={`${testId}-efforts`}
              onKeyDown={(e) => onColumnKey(e, "effort")}
              className="min-w-0"
            >
              <p className="px-2.5 pb-0.5 pt-1.5 text-[11px] text-zinc-500">Effort</p>
              {levels.length === 0 ? (
                <p data-testid={`${testId}-no-efforts`} className="px-2.5 py-1 text-[12px] text-zinc-500">
                  {isCurrent ? "This model has no effort levels." : "Pick a model first."}
                </p>
              ) : (
                levels.map((x) => {
                  const held = waitingForModel || !!effortHeld || (x === "" && !!defaultEffortHeld && value.effort !== "");
                  return (
                    <button
                      key={x || "default"}
                      type="button"
                      role="radio"
                      aria-checked={value.effort === x}
                      data-testid={`${testId}-effort-${x || "default"}`}
                      disabled={held}
                      title={waitingForModel ? "Changing the model…" : effortHeld || (held ? defaultEffortHeld : undefined)}
                      onClick={() => {
                        if (x !== value.effort) onChange("effort", x);
                        close();
                      }}
                      className={`${ROW} ${value.effort === x ? "text-zinc-100" : "text-zinc-300"}`}
                    >
                      <span className="mt-0.5 w-3.5 shrink-0">{value.effort === x ? <Check size={13} aria-hidden="true" /> : null}</span>
                      <span>{x ? effortWord(x) : defaultEffortLabel}</span>
                    </button>
                  );
                })
              )}
            </div>
          </div>
          <p className="px-2.5 pb-1 pt-1.5 text-[11px] text-zinc-600">
            {PICKER_SHORTCUT_WORDS} opens this · arrows move · Enter picks · Esc or Tab closes
          </p>
        </div>,
          document.body,
        )
        : null}
    </div>
  );
}
