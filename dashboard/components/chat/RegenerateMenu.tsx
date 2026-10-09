"use client";

/**
 * Try again — with the same model, or with another one (v1.325.0).
 *
 * A reply that missed is often a MODEL that missed: the local model was too
 * small for it, or the subscription model was the wrong one for the job. The
 * old button could only ask the same model again. This is a split button: the
 * icon still retries at once with the same model (one press, as before), and
 * the small chevron beside it opens a menu — "Try again" first, then "Try again
 * with…" over the model catalog: recent picks first, a filter box once the
 * catalog is long, the rest ordered the way the composer's model menu orders
 * providers (on this computer → included → metered). A model that is not
 * connected is listed but cannot be picked, and says why.
 *
 * THE PRIVACY LINE. When the reply was written by a model on THIS computer and
 * the row under the pointer or the keyboard would send the conversation
 * somewhere else, the menu says so in one plain sentence before the press:
 * "This sends the conversation to <name> — it leaves this computer." Picking
 * it is the user's explicit choice (the app never switches providers on its
 * own — v1.162.0); the line makes the choice an informed one.
 *
 * The component decides nothing about the conversation: it calls
 * `onRegenerate()` for the same model and `onRegenerate("provider::model")`
 * for another. Remembering the pick, or making it the chat's model, is the
 * page's call.
 */

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ChevronDown, RefreshCw } from "lucide-react";
import type { ModelOption } from "@/lib/types";
import { matchModels } from "@/lib/recentModels";
import { providerDisplay } from "@/lib/onboarding";
import { ModelRowChips, modelText } from "@/components/ModelRowBits";

export interface RegenerateMenuProps {
  /** The model catalog (GET /models, as the composer's menu has it). */
  models: ModelOption[];
  /** Recent picks, newest first, as `provider::model` (lib/recentModels). */
  recent: string[];
  /** The conversation's pick as `provider::model`; "" = the default model. */
  current: string;
  /** Who wrote the reply being retried. `kind` when the receipt knows it;
   *  otherwise it is read off the catalog by provider. */
  answeredBy?: { provider: string; kind?: "local" | "cli" | "api" };
  /** No argument = the same model again; a `provider::model` = that one. */
  onRegenerate: (choice?: string) => void;
  /** A turn is running: nothing can be retried yet. */
  disabled?: boolean;
  /** The quick button's accessible name. */
  quickLabel?: string;
}

/** Past this many models the menu grows a filter box. */
export const REGEN_FILTER_AT = 8;
/** Rows a typed filter shows. */
const FILTER_MAX = 12;

type Where = "local" | "cli" | "api";

/** Where a catalog row runs. A keyless API name served by a logged-in CLI is
 *  flat-rate ("cli"), the same rule the composer's menu applies (v1.230.0). */
export function whereItRuns(m: Pick<ModelOption, "kind" | "inherited_from">): Where | undefined {
  if (m.inherited_from) return "cli";
  return m.kind;
}

/** The provider's name in the user's words. */
export function providerName(m: Pick<ModelOption, "provider" | "name">): string {
  return (m.name || "").trim() || providerDisplay(m.provider) || m.provider;
}

/** True only when the reply is KNOWN to have come from this computer. */
export function answeredLocally(
  answeredBy: RegenerateMenuProps["answeredBy"],
  models: readonly ModelOption[],
): boolean {
  if (!answeredBy?.provider) return false;
  if (answeredBy.kind) return answeredBy.kind === "local";
  const row = models.find((m) => m.provider === answeredBy.provider);
  return row ? whereItRuns(row) === "local" : false;
}

/** The one sentence, or "" when the target stays on this computer. A row
 *  whose location is unknown is treated as leaving — never promise a model
 *  stays local without the catalog saying so. */
export function privacyLine(
  target: ModelOption | undefined,
  replyWasLocal: boolean,
): string {
  if (!replyWasLocal || !target) return "";
  if (whereItRuns(target) === "local") return "";
  return `This sends the conversation to ${providerName(target)} — it leaves this computer.`;
}

function choiceOf(m: Pick<ModelOption, "provider" | "model">): string {
  return `${m.provider}::${m.model}`;
}

function rowFor(models: readonly ModelOption[], choice: string): ModelOption | undefined {
  const i = choice.indexOf("::");
  if (i <= 0) return undefined;
  const provider = choice.slice(0, i);
  const model = choice.slice(i + 2);
  return models.find((m) => m.provider === provider && m.model === model);
}

const RANK: Record<string, number> = { local: 0, cli: 1, api: 2 };

/** Connected first, then on this computer → included → metered, then by
 *  provider name; the catalog's own order inside a provider. */
function ordered(models: readonly ModelOption[]): ModelOption[] {
  return models
    .map((m, i) => ({ m, i }))
    .sort((a, b) => {
      const av = a.m.available !== false;
      const bv = b.m.available !== false;
      if (av !== bv) return av ? -1 : 1;
      const ra = RANK[whereItRuns(a.m) ?? "api"] ?? 3;
      const rb = RANK[whereItRuns(b.m) ?? "api"] ?? 3;
      if (ra !== rb) return ra - rb;
      const pa = providerName(a.m);
      const pb = providerName(b.m);
      if (pa !== pb) return pa.localeCompare(pb);
      return a.i - b.i;
    })
    .map((x) => x.m);
}

interface Item {
  key: string;
  /** undefined = the same model again. */
  choice?: string;
  row?: ModelOption;
  disabled: boolean;
  reason?: string;
  section: "same" | "recent" | "all" | "match";
}

function modelItem(m: ModelOption, section: Item["section"]): Item {
  const off = m.available === false;
  return {
    key: `${section}-${choiceOf(m)}`,
    choice: choiceOf(m),
    row: m,
    disabled: off,
    reason: off ? `${providerName(m)} isn't connected — set it up on Connections` : undefined,
    section,
  };
}

export function RegenerateMenu({
  models,
  recent,
  current,
  answeredBy,
  onRegenerate,
  disabled = false,
  quickLabel = "Try again",
}: RegenerateMenuProps) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  const uid = useId();
  const menuId = `${uid}-menu`;
  const noteId = `${uid}-privacy`;
  const itemId = (i: number) => `${uid}-item-${i}`;

  const showFilter = models.length > REGEN_FILTER_AT;
  const currentRow = rowFor(models, current);
  const replyWasLocal = answeredLocally(answeredBy, models);

  const items = useMemo<Item[]>(() => {
    const out: Item[] = [{ key: "same", row: currentRow, disabled: false, section: "same" }];
    // The current pick is the "Try again" row; listing it again below would
    // be two rows that do the same thing.
    const others = models.filter((m) => !current || choiceOf(m) !== current);
    const q = filter.trim();
    if (q) {
      for (const m of matchModels(others, q, FILTER_MAX)) out.push(modelItem(m, "match"));
      return out;
    }
    const seen = new Set<string>();
    for (const v of recent) {
      const m = rowFor(others, v);
      if (!m || seen.has(v)) continue;
      seen.add(v);
      out.push(modelItem(m, "recent"));
    }
    for (const m of ordered(others)) {
      if (seen.has(choiceOf(m))) continue;
      out.push(modelItem(m, "all"));
    }
    return out;
  }, [models, recent, current, currentRow, filter]);

  const activeIdx = Math.min(active, items.length - 1);
  const activeItem = items[activeIdx];
  const note = activeItem ? privacyLine(activeItem.row, replyWasLocal) : "";

  function close(refocus: boolean) {
    setOpen(false);
    setFilter("");
    setActive(0);
    if (refocus) moreRef.current?.focus();
  }

  // A turn starting under an open menu closes it: nothing can be retried now.
  useEffect(() => {
    if (disabled && open) close(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled]);

  // Focus goes where the keys are read: the filter box when there is one,
  // else the menu itself.
  useEffect(() => {
    if (!open) return;
    (showFilter ? filterRef.current : menuRef.current)?.focus();
  }, [open, showFilter]);

  // A press anywhere else closes the menu.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) close(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Typing moves the keyboard to the first model it finds (Enter picks it,
  // as in the composer's model menu); an empty box goes back to "Try again".
  useEffect(() => {
    if (!filter.trim()) {
      setActive(0);
      return;
    }
    const first = items.findIndex((it, i) => i > 0 && !it.disabled);
    setActive(first > 0 ? first : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  // Keep the keyboard's row in view in a long list.
  useEffect(() => {
    if (!open) return;
    const el = document.getElementById(itemId(activeIdx));
    el?.scrollIntoView?.({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeIdx]);

  function pick(item: Item | undefined) {
    if (!item || item.disabled || disabled) return;
    close(true);
    if (item.choice) onRegenerate(item.choice);
    else onRegenerate();
  }

  /** Next selectable row from `from` in `dir`, wrapping; disabled rows are
   *  passed over (they say why on hover, but cannot be chosen). */
  function step(from: number, dir: 1 | -1): number {
    const n = items.length;
    for (let k = 1; k <= n; k++) {
      const i = (from + dir * k + n) % n;
      if (!items[i].disabled) return i;
    }
    return from;
  }

  function onKeyDown(e: KeyboardEvent) {
    // Home/End inside the filter box move its caret, as in any text box.
    const inBox = e.target === filterRef.current;
    if ((e.key === "Home" || e.key === "End") && inBox) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(step(activeIdx, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(step(activeIdx, -1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(step(0, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      // A typed name that found nothing must not quietly retry the same model.
      if (filter.trim() && items.length === 1) return;
      pick(activeItem);
    } else if (e.key === "Escape") {
      e.preventDefault();
      // Never let Esc reach the page (it would stop a turn or close a panel).
      e.stopPropagation();
      close(true);
    } else if (e.key === "Tab") {
      close(false);
    }
  }

  const sameName = currentRow ? modelText(currentRow) : "default model";
  // Calm chat W1-4: 28px tall ghosts, like every control in the row under a
  // reply (the "with another model" chevron stays a narrow 16px).
  const btn =
    "grid h-7 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";

  const hasRecent = items.some((it) => it.section === "recent");

  function renderItem(item: Item, i: number) {
    const on = i === activeIdx;
    const showNote = on && note;
    return (
      <button
        key={item.key}
        type="button"
        id={itemId(i)}
        role="menuitem"
        tabIndex={-1}
        data-testid="regen-item"
        data-choice={item.choice ?? ""}
        data-active={on ? "true" : undefined}
        aria-disabled={item.disabled || undefined}
        aria-describedby={showNote ? noteId : undefined}
        title={item.reason}
        onMouseEnter={() => setActive(i)}
        onFocus={() => setActive(i)}
        onClick={() => pick(item)}
        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
          item.disabled ? "cursor-not-allowed opacity-45" : ""
        } ${on && !item.disabled ? "bg-white/[0.06] text-accent-soft" : "text-zinc-300"}`}
      >
        {item.section === "same" ? (
          <>
            <RefreshCw size={11} className="shrink-0 text-zinc-500" aria-hidden="true" />
            <span className="text-[12px]">Try again</span>
            <span className="ml-auto min-w-0 truncate text-[10px] text-zinc-500">
              same model · {sameName}
            </span>
          </>
        ) : item.row ? (
          <>
            <span
              className="min-w-0 truncate font-mono text-[12px]"
              title={item.row.label ? item.row.model : undefined}
            >
              {modelText(item.row)}
            </span>
            <ModelRowChips m={item.row} />
            <span className="ml-auto shrink-0 text-[10px] text-zinc-500">
              {item.disabled ? "not connected" : providerName(item.row)}
            </span>
          </>
        ) : null}
      </button>
    );
  }

  return (
    <div ref={wrapRef} className="relative inline-flex items-center" data-testid="regenerate-menu">
      <button
        type="button"
        onClick={() => onRegenerate()}
        disabled={disabled}
        title={quickLabel}
        aria-label={quickLabel}
        className={`${btn} w-7`}
      >
        <RefreshCw size={12} />
      </button>
      <button
        ref={moreRef}
        type="button"
        data-testid="regen-more"
        onClick={() => (open ? close(false) : setOpen(true))}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title="Try again with another model"
        aria-label="Try again with another model"
        className={`${btn} w-4`}
      >
        <ChevronDown size={10} />
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label="Try again"
          tabIndex={-1}
          aria-activedescendant={showFilter ? undefined : itemId(activeIdx)}
          onKeyDown={onKeyDown}
          data-testid="regen-menu"
          className="absolute bottom-full left-0 z-30 mb-1.5 w-64 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40 outline-none"
        >
          {items.slice(0, 1).map((item) => renderItem(item, 0))}
          <p
            role="presentation"
            className="mt-1 border-t border-white/[0.06] px-2.5 pb-1 pt-1.5 text-[10px] uppercase tracking-wide text-zinc-600"
          >
            Try again with…
          </p>
          {showFilter && (
            <input
              ref={filterRef}
              data-testid="regen-filter"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Type to find a model…"
              aria-label="Find a model"
              aria-controls={menuId}
              aria-activedescendant={itemId(activeIdx)}
              className="mb-1 w-full rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/40"
            />
          )}
          <div className="max-h-64 overflow-y-auto">
            {items.slice(1).map((item, k) => {
              const i = k + 1;
              const prev = items[i - 1].section;
              const sub =
                hasRecent && item.section !== prev
                  ? item.section === "recent"
                    ? "Recent"
                    : item.section === "all"
                      ? "All models"
                      : null
                  : null;
              return (
                <div key={item.key} role="presentation">
                  {sub && (
                    <p role="presentation" className="px-2.5 pb-0.5 pt-1 text-[10px] text-zinc-600">
                      {sub}
                    </p>
                  )}
                  {renderItem(item, i)}
                </div>
              );
            })}
            {items.length === 1 && (
              <p className="px-2.5 py-1.5 text-[12px] text-zinc-500">
                {filter.trim() ? "No model matches." : "No other model is set up."}
              </p>
            )}
          </div>
          {note && (
            <p
              id={noteId}
              role="note"
              data-testid="regen-privacy"
              className="mt-1 border-t border-white/[0.06] px-2.5 pb-1 pt-1.5 text-[11px] leading-snug text-tone-warn"
            >
              {note}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
