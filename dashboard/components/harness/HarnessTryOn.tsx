"use client";

/**
 * v1.397.0: the box's "Try on…" control (lib/buildAttempts, the daemon's
 * harness/attempts.py) — the second way to ask for attempts besides the words
 * "try this with A, B and C". Opt-in, per message: the picks ride the NEXT
 * message only (`try_on`), and the chip says so ("Try on 2 models") until it
 * is sent or cleared. At most three; under Local only the list offers only
 * models that stay on this network (the daemon refuses any other anyway).
 *
 * A disclosure (not a form): every button is `type="button"`, Escape closes
 * it and gives the focus back to the chip.
 */

import { useEffect, useRef, useState } from "react";
import { Layers, X } from "lucide-react";
import { composerChipClass } from "@/lib/composerChips";
import { workerModelName } from "@/lib/buildHelpers";
import {
  MAX_ATTEMPTS,
  sameRow,
  togglePick,
  tryOnChipWords,
  tryOnChoices,
  type TryOnPick,
  type TryOnRow,
} from "@/lib/buildAttempts";
import type { CatalogRow } from "@/lib/answeredModel";

export function TryOnControl({
  models,
  localOnly,
  picks,
  onChange,
  disabled,
}: {
  models: readonly (TryOnRow & CatalogRow)[];
  localOnly: boolean;
  picks: readonly TryOnPick[];
  onChange: (next: TryOnPick[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const chipRef = useRef<HTMLButtonElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const choices = tryOnChoices(models, localOnly);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (t && (boxRef.current?.contains(t) || chipRef.current?.contains(t))) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const nameOf = (r: TryOnRow & CatalogRow) =>
    (typeof r.label === "string" && r.label.trim()) || workerModelName(r.provider, r.model, models) || r.model;

  return (
    <div className="relative">
      <button
        ref={chipRef}
        type="button"
        data-testid="harness-try-on"
        aria-expanded={open}
        aria-haspopup="true"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        title="Try this message on up to three models at once, each on its own branch — then keep the one you want"
        className={composerChipClass(picks.length > 0)}
      >
        <Layers size={14} aria-hidden className="shrink-0" />
        <span>{tryOnChipWords(picks)}</span>
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
      {open ? (
        <div
          ref={boxRef}
          data-testid="harness-try-on-menu"
          role="group"
          aria-label="Models to try this on"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              setOpen(false);
              chipRef.current?.focus();
            }
          }}
          className="absolute bottom-full left-0 z-30 mb-2 w-[min(320px,calc(100vw-32px))] rounded-xl border border-white/[0.08] bg-ink-900 p-2 shadow-xl"
        >
          <p className="px-1 pb-1 text-[12px] leading-5 text-zinc-400">
            Pick up to {MAX_ATTEMPTS} models. Each tries your next message on its own branch; you keep one.
            {localOnly ? " Local only: models on this PC or your network." : ""}
          </p>
          {choices.length ? (
            <ul className="max-h-[240px] overflow-y-auto">
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
                        className="accent-current"
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
          <div className="flex justify-end gap-1 pt-1">
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
              onClick={() => {
                setOpen(false);
                chipRef.current?.focus();
              }}
              className="rounded-full bg-white/[0.08] px-3 py-1 text-[12px] text-zinc-200 hover:bg-white/[0.14]"
            >
              Done
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
