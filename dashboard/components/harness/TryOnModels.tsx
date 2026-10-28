"use client";

/**
 * v1.400.8: the "try it on several models" list, GROUPED BY PROVIDER — ONE
 * component for both places that ask for it: the coding session box's
 * "Try on…" menu (HarnessTryOn, v1.400.7) and the Tonight form's "Try it on
 * several models" (TonightList, whose flat list of every model of every
 * provider the user asked to match the menu).
 *
 * What it draws (the groups are lib/tryOnMenu's): the picks on top, each with
 * a remove button; a box that finds a model across every provider; then one
 * row per provider, local first, each marked where it runs, each opening its
 * models with a checkbox and "local" / "cloud" on every one. At most
 * MAX_ATTEMPTS picks; a tick past that is disabled and says why.
 *
 * Two layouts, one list:
 *  - "pop-out" (the menu): a provider's models float beside its row on the
 *    right (components/chat/FitPanel); below 640 px they open under it;
 *  - "inline" (a form in a scrolling column): they always open under the row,
 *    the menu's phone shape — a pop-out fixed to the window would have to
 *    follow every scroll of the column it sits in.
 *
 * Keys: Escape closes an open provider (the focus back on its row) and stops
 * there; with none open it is the caller's. A caller with keys of its own
 * outside the list (the menu's Done) passes `openGroup` / `onOpenGroup`, so
 * its Escape can close an open provider first. Enter in the find box never
 * submits a form around it. Every button is `type="button"` (a form may
 * contain it — the v1.370.1 lesson).
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ChevronRight, X } from "lucide-react";
import { FitPanel } from "@/components/chat/FitPanel";
import { KIND_BADGE, MODEL_FILTER_MAX } from "@/components/chat/ModelMenu";
import { ModelRowChips } from "@/components/ModelRowBits";
import { MAX_ATTEMPTS, sameRow, togglePick, type TryOnPick } from "@/lib/buildAttempts";
import {
  groupBadge,
  matchTryOn,
  pickWhere,
  pickedIn,
  placeWord,
  type TryOnGroup,
  type TryOnModel,
} from "@/lib/tryOnMenu";

/** Below this width a provider's models open under its row (the composer's
 *  model menu does the same: beside the list, a phone has no room). */
export const PHONE_QUERY = "(max-width: 639.98px)";

/** A provider's pop-out: beside its row (FitPanel places it, fixed); under
 *  its row on a phone. */
const PANEL =
  "absolute z-30 w-64 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40 max-sm:static max-sm:mt-0.5 max-sm:w-full";

/** A provider's models opened in place, under its row (the inline layout). */
const INLINE_PANEL = "mt-0.5 rounded-lg border border-white/[0.06] bg-black/10 p-1";

/** How far a pop-out stands off its row: past the list's padding (`p-2`) and
 *  border, and a gap — beside the list, never over its edge. */
const PANEL_GAP_PX = 8 + 1 + 4;

export const isPhone = () => typeof window !== "undefined" && !!window.matchMedia?.(PHONE_QUERY)?.matches;

export function TryOnModels({
  groups,
  picks,
  onChange,
  localOnly,
  layout,
  refitKey,
  autoFocus,
  className = "",
  listClassName = "",
  openGroup,
  onOpenGroup,
}: {
  groups: readonly TryOnGroup[];
  picks: readonly TryOnPick[];
  onChange: (next: TryOnPick[]) => void;
  localOnly: boolean;
  layout: "pop-out" | "inline";
  /** Re-places an open pop-out when its list moves (the menu's place). */
  refitKey?: unknown;
  /** Where the focus goes when the list mounts (the menu opening). */
  autoFocus?: "filter" | "group";
  className?: string;
  listClassName?: string;
  /** The provider whose models are open, when the caller keeps it. */
  openGroup?: string | null;
  onOpenGroup?: (id: string | null) => void;
}) {
  /** The provider whose models are open. */
  const [ownGroup, setOwnGroup] = useState<string | null>(null);
  const sub = openGroup !== undefined ? openGroup : ownGroup;
  const setSub = onOpenGroup ?? setOwnGroup;
  const [filter, setFilter] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const filterRef = useRef<HTMLInputElement | null>(null);
  const found = useMemo(() => matchTryOn(groups, filter, MODEL_FILTER_MAX), [groups, filter]);
  const inline = layout === "inline";

  useLayoutEffect(() => {
    if (autoFocus === "filter") filterRef.current?.focus({ preventScroll: true });
    else if (autoFocus === "group")
      rootRef.current?.querySelector<HTMLElement>('[data-testid="harness-try-on-group"]')?.focus({ preventScroll: true });
    // Once, when the list mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Models opened under their row: bring the row and its models into view
  // inside the list.
  useEffect(() => {
    if (!sub || !(inline || isPhone())) return;
    const row = Array.from(rootRef.current?.querySelectorAll<HTMLElement>("[data-group-row]") ?? []).find(
      (el) => el.dataset.groupRow === sub,
    );
    row?.scrollIntoView?.({ block: "nearest" });
  }, [sub, inline]);

  const groupButton = (id: string) =>
    Array.from(rootRef.current?.querySelectorAll<HTMLElement>('[data-testid="harness-try-on-group"]') ?? []).find(
      (el) => el.dataset.group === id,
    );

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    // An IME composing a word owns its keys (its Escape drops the word).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key !== "Escape" || !sub) return;
    e.preventDefault();
    e.stopPropagation();
    const id = sub;
    setSub(null);
    groupButton(id)?.focus();
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
    const models = (
      <div role="group" aria-label={`${g.label} models`}>
        {groupModels(g)}
      </div>
    );
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
            className={`shrink-0 text-zinc-500 transition-transform ${
              expanded ? (inline ? "rotate-90" : "max-sm:rotate-90") : ""
            }`}
          />
        </button>
        {expanded ? (
          inline ? (
            <div data-testid="harness-try-on-panel" className={INLINE_PANEL}>
              {models}
            </div>
          ) : (
            <FitPanel
              testId="harness-try-on-panel"
              className={PANEL}
              float
              toward="right"
              gap={PANEL_GAP_PX}
              inlineQuery={PHONE_QUERY}
              refitKey={refitKey}
            >
              {models}
            </FitPanel>
          )
        ) : null}
      </div>
    );
  };

  const typed = filter.trim();
  return (
    <div ref={rootRef} onKeyDown={onKey} className={className}>
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
          onKeyDown={(e) => {
            // A form around the list (Tonight) must not be sent from here.
            if (e.key === "Enter") e.preventDefault();
          }}
          placeholder="Type to find a model…"
          aria-label="Find a model"
          className="mb-1 w-full shrink-0 rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/40"
        />
      ) : null}
      {groups.length ? (
        <div data-testid="harness-try-on-list" className={`min-h-0 overflow-y-auto overscroll-contain ${listClassName}`}>
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
    </div>
  );
}
