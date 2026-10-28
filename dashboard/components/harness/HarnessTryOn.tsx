"use client";

/**
 * v1.397.0: the box's "Try on…" control (lib/buildAttempts, the daemon's
 * harness/attempts.py) — the second way to ask for attempts besides the words
 * "try this with A, B and C". Opt-in, per message: the picks ride the NEXT
 * message only (`try_on`), and the chip says so ("Try on 2 models") until it
 * is sent or cleared. At most three; under Local only the list offers only
 * models that stay on this network (the daemon refuses any other anyway).
 *
 * v1.400.7 (the user, with a screenshot of one long flat list of every model
 * of every provider): the list is GROUPED BY PROVIDER, the composer's model
 * menu's shape (components/chat/ModelMenu) — one row per provider, local
 * first, each marked where it runs, each opening a pop-out of its models with
 * a checkbox and "local" / "cloud" on every one; no model listed twice; the
 * user's own model server named once (lib/tryOnMenu). A typed word finds a
 * model across every provider; the picks are listed at the top, so a tick
 * inside a closed pop-out is still in sight.
 *
 * The list is PORTALED to the page and fixed above the chip, always inside
 * the window (`tryOnMenuPosition`): the session's columns clip what overflows
 * them, and on a phone the chip sits mid-row. A provider's pop-out floats
 * beside its row on the right (the chip sits at the box's left edge, so the
 * left has no room — components/chat/FitPanel `toward`); below 640 px it opens
 * under its row, inside the list. A disclosure (not a form): every button is
 * `type="button"`; Escape closes an open pop-out (the focus back on its
 * provider), then the list (the focus back on the chip); a press outside
 * closes it. The menu has no backdrop blur: a backdrop filter makes a box the
 * containing block of the FIXED pop-outs inside it, and would clip them.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { ChevronRight, Layers, X } from "lucide-react";
import { FitPanel } from "@/components/chat/FitPanel";
import { KIND_BADGE, MODEL_FILTER_MAX } from "@/components/chat/ModelMenu";
import { ModelRowChips } from "@/components/ModelRowBits";
import { composerChipClass, fitIconChip, fitShows, fitWords, iconOnlyTitle } from "@/lib/composerChips";
import {
  MAX_ATTEMPTS,
  sameRow,
  togglePick,
  tryOnChipWords,
  tryOnMenuPosition,
  type TryOnPick,
  type TryOnRow,
} from "@/lib/buildAttempts";
import {
  groupBadge,
  matchTryOn,
  pickWhere,
  pickedIn,
  placeWord,
  tryOnGroups,
  type TryOnGroup,
  type TryOnModel,
} from "@/lib/tryOnMenu";
import type { CatalogRow } from "@/lib/answeredModel";

type Place = ReturnType<typeof tryOnMenuPosition>;

/** The try-on chip's tooltip. */
const TRY_ON_TITLE =
  "Try this message on up to three models at once, each on its own branch — then keep the one you want";

/** Below this width a provider's models open under its row (the composer's
 *  model menu does the same: beside the list, a phone has no room). */
const PHONE_QUERY = "(max-width: 639.98px)";

/** A provider's pop-out: beside its row (FitPanel places it, fixed); under
 *  its row on a phone. */
const PANEL =
  "absolute z-30 w-64 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40 max-sm:static max-sm:mt-0.5 max-sm:w-full";

/** How far a pop-out stands off its row: past the list's padding (`p-2`) and
 *  border, and a gap — beside the list, never over its edge. */
const PANEL_GAP_PX = 8 + 1 + 4;

const isPhone = () => typeof window !== "undefined" && !!window.matchMedia?.(PHONE_QUERY)?.matches;

export function TryOnControl({
  models,
  localOnly,
  picks,
  onChange,
  disabled,
  iconOnly = false,
}: {
  models: readonly (TryOnRow & CatalogRow)[];
  localOnly: boolean;
  picks: readonly TryOnPick[];
  onChange: (next: TryOnPick[]) => void;
  disabled?: boolean;
  /** v1.400.0: the composer's toolbar (lib/toolbarFit) has taken its
   *  `tryon` step; the chip's tooltip then leads with its words. Its shape
   *  follows the toolbar's `data-fit-tryon` (CSS). */
  iconOnly?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  /** The provider whose pop-out is open. */
  const [sub, setSub] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const chipRef = useRef<HTMLButtonElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const filterRef = useRef<HTMLInputElement | null>(null);
  const groups = useMemo(() => tryOnGroups(models, localOnly), [models, localOnly]);
  const found = useMemo(() => matchTryOn(groups, filter, MODEL_FILTER_MAX), [groups, filter]);

  // Each opening starts with every pop-out closed and an empty box.
  const [openedFor, setOpenedFor] = useState(false);
  if (open !== openedFor) {
    setOpenedFor(open);
    if (open) {
      setSub(null);
      setFilter("");
    }
  }

  const measure = useCallback(() => {
    const chip = chipRef.current;
    if (!chip) return;
    const r = chip.getBoundingClientRect();
    const de = document.documentElement;
    const next = tryOnMenuPosition(
      { left: r.left, top: r.top },
      { width: de.clientWidth || window.innerWidth, height: de.clientHeight || window.innerHeight },
    );
    setPlace((prev) =>
      prev &&
      prev.left === next.left &&
      prev.bottom === next.bottom &&
      prev.width === next.width &&
      prev.maxHeight === next.maxHeight
        ? prev
        : next,
    );
  }, []);

  useLayoutEffect(() => {
    if (open) measure();
  }, [open, measure]);

  // The focus goes into the list once per opening (it is at the page's end):
  // the find box on a wide window, the first provider on a phone (a focused
  // box there pops the keyboard over the list). Never scrolls the page.
  useLayoutEffect(() => {
    if (!open) return;
    if (!isPhone()) filterRef.current?.focus({ preventScroll: true });
    else boxRef.current?.querySelector<HTMLElement>('[data-testid="harness-try-on-group"]')?.focus({ preventScroll: true });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (t && (boxRef.current?.contains(t) || chipRef.current?.contains(t))) return;
      setOpen(false);
    };
    // A scroll inside the list moves the rows, not the chip: the list stays.
    const onScroll = (e: Event) => {
      if (e.target instanceof Node && boxRef.current?.contains(e.target)) return;
      measure();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open, measure]);

  // On a phone a pop-out opens under its row: bring the row and its models
  // into view inside the list.
  useEffect(() => {
    if (!sub || !isPhone()) return;
    const row = Array.from(boxRef.current?.querySelectorAll<HTMLElement>("[data-group-row]") ?? []).find(
      (el) => el.dataset.groupRow === sub,
    );
    row?.scrollIntoView?.({ block: "nearest" });
  }, [sub]);

  const close = () => {
    setOpen(false);
    chipRef.current?.focus();
  };
  const groupButton = (id: string) =>
    Array.from(boxRef.current?.querySelectorAll<HTMLElement>('[data-testid="harness-try-on-group"]') ?? []).find(
      (el) => el.dataset.group === id,
    );

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    // An IME composing a word owns its keys (its Escape drops the word).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (sub) {
        const id = sub;
        setSub(null);
        groupButton(id)?.focus();
        return;
      }
      close();
      return;
    }
    if (e.key !== "Tab") return;
    // The list is on the page's top layer — the end of the page in tab order:
    // Shift+Tab from its first control goes back to the chip, Tab from its
    // last moves on from the chip (the model menu's rule).
    const items = Array.from(
      e.currentTarget.querySelectorAll<HTMLElement>("input:not([disabled]), button:not([disabled])"),
    );
    if (!items.length) return;
    const at = document.activeElement;
    if (e.shiftKey && at === items[0]) {
      e.preventDefault();
      chipRef.current?.focus();
    } else if (!e.shiftKey && at === items[items.length - 1]) {
      chipRef.current?.focus();
    }
  };

  const toggle = (m: TryOnModel) =>
    onChange(togglePick(picks, { provider: m.provider, model: m.model, label: m.label }));

  /** One model: its checkbox, its words, its chips, and where it runs. */
  const modelRow = (m: TryOnModel, where?: string) => {
    const picked = picks.some((p) => sameRow(p, m));
    const full = !picked && picks.length >= MAX_ATTEMPTS;
    const badge = KIND_BADGE[m.place] ?? KIND_BADGE.api;
    return (
      <label
        key={`${m.provider}::${m.model}`}
        className={`flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-[12px] ${
          full ? "cursor-not-allowed opacity-50" : "hover:bg-white/[0.06]"
        }`}
        title={full ? `You picked ${MAX_ATTEMPTS} already. Untick one to pick this.` : m.title}
      >
        <input
          type="checkbox"
          data-testid="harness-try-on-row"
          data-provider={m.provider}
          data-model={m.model}
          checked={picked}
          disabled={full}
          onChange={() => toggle(m)}
          className="shrink-0"
        />
        <span className={`min-w-0 truncate ${picked ? "text-accent-soft" : "text-zinc-200"}`}>{m.label}</span>
        <span className="inline-flex shrink-0 items-center gap-1">
          <ModelRowChips m={m.row} />
        </span>
        <span className="ml-auto flex min-w-0 shrink-0 items-center gap-1.5">
          {where ? <span className="max-w-[7rem] truncate text-[10px] text-zinc-500">{where}</span> : null}
          <span
            data-testid="harness-try-on-place"
            data-place={m.place}
            title={badge.title}
            className={`text-[10px] ${badge.cls}`}
          >
            {placeWord(m.place)}
          </span>
        </span>
      </label>
    );
  };

  /** A provider's models: the local ones first, under a heading each when it
   *  runs models in both places. */
  const groupModels = (g: TryOnGroup) => {
    if (g.place !== "mixed") return g.models.map((m) => modelRow(m));
    const local = g.models.filter((m) => m.place === "local");
    const cloud = g.models.filter((m) => m.place !== "local");
    const heading = (text: string) => (
      <p className="px-2 pb-0.5 pt-1 text-[10px] uppercase tracking-wide text-zinc-600">{text}</p>
    );
    return (
      <>
        {heading("On your network")}
        {local.map((m) => modelRow(m))}
        {heading("Passed on to the cloud")}
        {cloud.map((m) => modelRow(m))}
      </>
    );
  };

  const groupRow = (g: TryOnGroup) => {
    const badge = groupBadge(g.place);
    const n = pickedIn(g, picks);
    const expanded = sub === g.id;
    return (
      <div key={g.id} className="relative" data-group-row={g.id}>
        <button
          type="button"
          data-testid="harness-try-on-group"
          data-group={g.id}
          aria-expanded={expanded}
          onClick={() => setSub(expanded ? null : g.id)}
          className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 ${
            n > 0 ? "text-accent-soft" : "text-zinc-300"
          }`}
        >
          <span className="min-w-0 truncate">{g.label}</span>
          <span data-testid="harness-try-on-group-place" data-place={g.place} title={badge.title} className={`shrink-0 text-[10px] ${badge.cls}`}>
            {badge.text}
          </span>
          {n > 0 ? (
            <span data-testid="harness-try-on-group-picked" className="shrink-0 text-[10px] text-accent-soft">
              {n} picked
            </span>
          ) : null}
          <span className="ml-auto shrink-0 text-[10px] text-zinc-600">{g.models.length}</span>
          <ChevronRight
            size={12}
            aria-hidden
            className={`shrink-0 text-zinc-500 transition-transform ${expanded ? "max-sm:rotate-90" : ""}`}
          />
        </button>
        {expanded ? (
          <FitPanel
            testId="harness-try-on-panel"
            className={PANEL}
            float
            toward="right"
            gap={PANEL_GAP_PX}
            inlineQuery={PHONE_QUERY}
            refitKey={place}
          >
            <div role="group" aria-label={`${g.label} models`}>
              {groupModels(g)}
            </div>
          </FitPanel>
        ) : null}
      </div>
    );
  };

  const typed = filter.trim();
  const menu = open ? (
    <div
      ref={boxRef}
      data-testid="harness-try-on-menu"
      role="group"
      aria-label="Models to try this on"
      onKeyDown={onMenuKey}
      style={{
        position: "fixed",
        left: place?.left ?? 16,
        bottom: place?.bottom ?? 96,
        width: place?.width ?? 320,
        maxHeight: place?.maxHeight,
      }}
      className="z-[60] flex flex-col rounded-xl border border-white/10 bg-zinc-900 p-2 shadow-xl shadow-black/40"
    >
      <p className="shrink-0 px-1 pb-1 text-[12px] leading-5 text-zinc-400">
        Pick up to {MAX_ATTEMPTS} models. Each tries your next message on its own branch; you keep one.
        {localOnly ? " Local only: models on this PC or your network." : ""}
      </p>
      {picks.length ? (
        <ul data-testid="harness-try-on-picked" aria-label="Picked models" className="mb-1 flex shrink-0 flex-wrap gap-1 px-1">
          {picks.map((p) => {
            const where = pickWhere(groups, p);
            return (
              <li
                key={`${p.provider}::${p.model}`}
                className="inline-flex min-w-0 max-w-full items-center gap-1 rounded-full bg-white/[0.06] py-0.5 pl-2 pr-0.5 text-[11px] text-zinc-200"
                title={`${p.provider} · ${p.model}`}
              >
                <span className="min-w-0 truncate">
                  {p.label}
                  {where ? <span className="text-zinc-500"> · {where}</span> : null}
                </span>
                <button
                  type="button"
                  aria-label={`Do not try it on ${p.label}`}
                  title={`Do not try it on ${p.label}`}
                  onClick={() => onChange(picks.filter((q) => !sameRow(q, p)))}
                  className="inline-grid h-4 w-4 shrink-0 place-items-center rounded-full text-zinc-500 hover:bg-white/[0.1] hover:text-zinc-200"
                >
                  <X size={10} aria-hidden />
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
      {groups.length ? (
        <input
          ref={filterRef}
          data-testid="harness-try-on-filter"
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value);
            setSub(null);
          }}
          placeholder="Type to find a model…"
          aria-label="Find a model"
          className="mb-1 w-full shrink-0 rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/40"
        />
      ) : null}
      {groups.length ? (
        <div data-testid="harness-try-on-list" className="min-h-0 overflow-y-auto overscroll-contain">
          {typed ? (
            found.hits.length ? (
              <>
                {found.hits.map(({ group, model }) => modelRow(model, group.label))}
                {found.more > 0 ? (
                  <p className="px-2 py-1 text-[11px] text-zinc-500">
                    {found.more} more — type more to narrow it down.
                  </p>
                ) : null}
              </>
            ) : (
              <p className="px-2 py-1.5 text-[12px] text-zinc-500">No model matches.</p>
            )
          ) : (
            groups.map(groupRow)
          )}
        </div>
      ) : (
        <p data-testid="harness-try-on-empty" className="px-1 py-1 text-[12px] text-zinc-500">
          {localOnly ? "No model on this PC or your network is connected." : "No model is connected."}
        </p>
      )}
      <div className="flex shrink-0 justify-end gap-1 pt-1">
        {picks.length ? (
          <button
            type="button"
            onClick={() => onChange([])}
            className="rounded-full px-2.5 py-1 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200"
          >
            Clear
          </button>
        ) : null}
        <button
          type="button"
          data-testid="harness-try-on-done"
          onClick={close}
          className="rounded-full bg-white/[0.08] px-3 py-1 text-[12px] text-zinc-200 hover:bg-white/[0.14]"
        >
          Done
        </button>
      </div>
    </div>
  ) : null;

  return (
    <div className="flex items-center">
      <button
        ref={chipRef}
        type="button"
        data-testid="harness-try-on"
        aria-expanded={open}
        aria-haspopup="true"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        // v1.400.0: its words were its name; in a toolbar out of room they go.
        aria-label={tryOnChipWords(picks)}
        title={
          iconOnly
            ? iconOnlyTitle(tryOnChipWords(picks), TRY_ON_TITLE)
            : TRY_ON_TITLE
        }
        className={`${composerChipClass(picks.length > 0)}${picks.length > 0 ? "" : ` ${fitIconChip("tryon")}`}`}
      >
        <Layers size={14} aria-hidden className="shrink-0" />
        <span className={fitWords("tryon")}>{tryOnChipWords(picks)}</span>
        {/* Review: with models picked, a layers icon alone would tell it by
            hue; the chip keeps the count, as the chat's tools chip does. */}
        {picks.length > 0 ? <span className={`hidden ${fitShows("tryon")}`}>{picks.length}</span> : null}
      </button>
      {picks.length > 0 && !open ? (
        <button
          type="button"
          data-testid="harness-try-on-clear"
          aria-label="Do not try this on several models"
          title="Do not try this on several models"
          onClick={() => onChange([])}
          className="ml-0.5 inline-grid h-5 w-5 place-items-center rounded-full text-zinc-500 hover:bg-white/[0.08] hover:text-zinc-200"
        >
          <X size={11} aria-hidden />
        </button>
      ) : null}
      {menu && typeof document !== "undefined" ? createPortal(menu, document.body) : null}
    </div>
  );
}
