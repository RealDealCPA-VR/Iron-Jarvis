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
 * v1.400.8: the list itself (picks, find box, provider rows, models) is
 * TryOnModels, shared with the Tonight form; this file is the menu around it.
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
import { Layers, X } from "lucide-react";
import { TryOnModels, isPhone } from "@/components/harness/TryOnModels";
import { composerChipClass, fitIconChip, fitShows, fitWords, iconOnlyTitle } from "@/lib/composerChips";
import {
  MAX_ATTEMPTS,
  tryOnChipWords,
  tryOnMenuPosition,
  type TryOnPick,
  type TryOnRow,
} from "@/lib/buildAttempts";
import { tryOnGroups } from "@/lib/tryOnMenu";
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
  /** v1.400.0: the composer's toolbar (lib/toolbarFit) has taken its
   *  `tryon` step; the chip's tooltip then leads with its words. Its shape
   *  follows the toolbar's `data-fit-tryon` (CSS). */
  iconOnly?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  /** The provider whose pop-out is open (kept here: the menu's own buttons'
   *  Escape closes it first). */
  const [sub, setSub] = useState<string | null>(null);
  const chipRef = useRef<HTMLButtonElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const groups = useMemo(() => tryOnGroups(models, localOnly), [models, localOnly]);

  // Each opening starts with every pop-out closed (and, TryOnModels mounting
  // afresh, an empty find box).
  const [openedFor, setOpenedFor] = useState(false);
  if (open !== openedFor) {
    setOpenedFor(open);
    if (open) setSub(null);
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

  const close = () => {
    setOpen(false);
    chipRef.current?.focus();
  };
  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    // An IME composing a word owns its keys (its Escape drops the word).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (sub) {
        const id = sub;
        setSub(null);
        Array.from(boxRef.current?.querySelectorAll<HTMLElement>('[data-testid="harness-try-on-group"]') ?? [])
          .find((el) => el.dataset.group === id)
          ?.focus();
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
      <TryOnModels
        groups={groups}
        picks={picks}
        onChange={onChange}
        localOnly={localOnly}
        layout="pop-out"
        openGroup={sub}
        onOpenGroup={setSub}
        refitKey={place}
        // The focus goes into the list once per opening (it is at the page's
        // end): the find box on a wide window, the first provider on a phone
        // (a focused box there pops the keyboard over the list).
        autoFocus={isPhone() ? "group" : "filter"}
        className="flex min-h-0 flex-col"
      />
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
