"use client";

/**
 * v1.397.0: the box's "Try on…" control (lib/buildAttempts, the daemon's
 * harness/attempts.py) — the second way to ask for attempts besides the words
 * "try this with A, B and C". Opt-in, per message: the picks ride the NEXT
 * message only (`try_on`), and the chip says so ("Try on 2 models") until it
 * is sent or cleared. At most three; under Local only the list offers only
 * models that stay on this network (the daemon refuses any other anyway).
 *
 * The list is PORTALED to the page and fixed above the chip, always inside
 * the window (`tryOnMenuPosition`): the session's columns clip what overflows
 * them, and on a phone the chip sits mid-row. A disclosure (not a form):
 * every button is `type="button"`; Escape closes it and gives the focus back
 * to the chip; a press outside closes it.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Layers, X } from "lucide-react";
import { FIT_ICON_CHIP, FIT_WORDS, composerChipClass, iconOnlyTitle } from "@/lib/composerChips";
import { workerModelName } from "@/lib/buildHelpers";
import {
  MAX_ATTEMPTS,
  sameRow,
  togglePick,
  tryOnChipWords,
  tryOnChoices,
  tryOnMenuPosition,
  type TryOnPick,
  type TryOnRow,
} from "@/lib/buildAttempts";
import type { CatalogRow } from "@/lib/answeredModel";

type Place = ReturnType<typeof tryOnMenuPosition>;

/** The try-on chip's tooltip. */
const TRY_ON_TITLE =
  "Try this message on up to three models at once, each on its own branch — then keep the one you want";

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
  /** v1.400.0: the composer's toolbar (lib/toolbarFit) shows icons only; the
   *  chip's tooltip then leads with its words. Its shape follows the
   *  toolbar's `data-fit-icons` (CSS). */
  iconOnly?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  const chipRef = useRef<HTMLButtonElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const choices = tryOnChoices(models, localOnly);

  const measure = useCallback(() => {
    const chip = chipRef.current;
    if (!chip) return;
    const r = chip.getBoundingClientRect();
    const de = document.documentElement;
    setPlace(
      tryOnMenuPosition(
        { left: r.left, top: r.top },
        { width: de.clientWidth || window.innerWidth, height: de.clientHeight || window.innerHeight },
      ),
    );
  }, []);

  useLayoutEffect(() => {
    if (open) measure();
  }, [open, measure]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (t && (boxRef.current?.contains(t) || chipRef.current?.contains(t))) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, measure]);

  const close = () => {
    setOpen(false);
    chipRef.current?.focus();
  };
  const nameOf = (r: TryOnRow & CatalogRow) =>
    (typeof r.label === "string" && r.label.trim()) || workerModelName(r.provider, r.model, models) || r.model;

  const menu = open ? (
    <div
      ref={boxRef}
      data-testid="harness-try-on-menu"
      role="group"
      aria-label="Models to try this on"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          close();
        }
      }}
      style={{
        position: "fixed",
        left: place?.left ?? 16,
        bottom: place?.bottom ?? 96,
        width: place?.width ?? 320,
        maxHeight: place?.maxHeight,
      }}
      className="z-[60] flex flex-col overflow-hidden rounded-xl border border-white/10 bg-ink-900/95 p-2 shadow-xl shadow-black/30 backdrop-blur-xl"
    >
      <p className="shrink-0 px-1 pb-1 text-[12px] leading-5 text-zinc-400">
        Pick up to {MAX_ATTEMPTS} models. Each tries your next message on its own branch; you keep one.
        {localOnly ? " Local only: models on this PC or your network." : ""}
      </p>
      {choices.length ? (
        <ul className="min-h-0 overflow-y-auto">
          {choices.map((r) => {
            const picked = picks.some((p) => sameRow(p, r));
            const full = !picked && picks.length >= MAX_ATTEMPTS;
            const label = nameOf(r);
            return (
              <li key={`${r.provider}::${r.model}`}>
                <label
                  className={`flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-[13px] ${
                    full ? "opacity-50" : "hover:bg-white/[0.06]"
                  }`}
                  title={`${r.provider} · ${r.model}`}
                >
                  <input
                    type="checkbox"
                    data-testid="harness-try-on-row"
                    data-provider={r.provider}
                    data-model={r.model}
                    checked={picked}
                    disabled={full}
                    onChange={() => onChange(togglePick(picks, { provider: r.provider, model: r.model, label }))}
                  />
                  <span className="min-w-0 truncate text-zinc-200">{label}</span>
                </label>
              </li>
            );
          })}
        </ul>
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
        className={`${composerChipClass(picks.length > 0)} ${FIT_ICON_CHIP}`}
      >
        <Layers size={14} aria-hidden className="shrink-0" />
        <span className={FIT_WORDS}>{tryOnChipWords(picks)}</span>
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
