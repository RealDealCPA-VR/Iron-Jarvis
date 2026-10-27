"use client";

/**
 * ONE ROW for a composer's toolbar, whatever model is picked (v1.400.0).
 *
 * The user's report: "it looks clean until the model with the longer name is
 * selected and then things start to look a bit off with spacing". The chat's
 * toolbar was `flex-wrap` with a `shrink-0` right group (reasoning, model,
 * mic, Send), so when the two groups together were a little wider than the
 * card — "Qwen3.8-27B (RTX)" picked, which also brings a "Reasoning" chip —
 * the whole right group dropped to a second line, leaving a hole on the right
 * of the first line and another on the left of the second.
 *
 * The toolbar now stays one row and gives up space in this order:
 *
 *   0. FULL  — every chip with its words. The MODEL chip's name truncates
 *              first (flexbox: it is the one item that shrinks), down to a
 *              minimum that keeps about nine characters of it readable
 *              (`MODEL_LABEL_MIN_PX`; never wider than the name needs).
 *   1. ICONS — the left chips drop their words and keep their icons (their
 *              aria-labels, tooltips and every non-colour cue stay).
 *   2. TIGHT — the reasoning chip shortens its words.
 *   3. WRAP  — the last resort, only for a box too narrow for even that (a
 *              narrow IJ harness column): the old wrapping row, most compact.
 *
 * WHICH stage is decided by MEASURING, never by guessing a breakpoint: at the
 * current stage the row either fits (the right-most control ends inside the
 * box) or overflows by a measured amount. An overflow records what that stage
 * NEEDS (`available + overflow`) and steps to the next stage; a stage is only
 * left for the one before it once the box has `FIT_SLACK_PX` more room than
 * that stage needed (hysteresis), so a box resized by a few pixels back and
 * forth — or a scrollbar that comes and goes with the row — never flickers.
 *
 * The stage is applied as ATTRIBUTES on the toolbar (`data-fit-row`,
 * `data-fit-icons`, `data-fit-short`), which the chips read through
 * `group-data-[…]/fit:` classes. The whole measure → decide → apply loop runs
 * synchronously, in one go, outside React: the page never re-renders for it
 * (v1.250.0: it must not re-render per keystroke, and typing changes nothing
 * the observers watch). React is told the settled stage afterwards (state,
 * set only when it changes) for the one thing CSS cannot do: the icon-only
 * chips' tooltips, which then lead with the words the chip no longer shows.
 *
 * What it watches: a change of the controls' WORDS (MutationObserver: a new
 * model, the reasoning chip coming or going, the tools chip's count, before
 * the next paint) starts again from FULL, because what each stage needs was
 * measured with the old words; so does the app's font arriving. The
 * toolbar's size and each control's size (ResizeObserver: a window resize, a
 * chip whose weight changes) re-measure at the current stage on the next
 * frame. Menus drawn inside the toolbar carry `data-fit-ignore` and change no
 * words: opening one changes nothing. Below `sm` (a phone) it does nothing:
 * the phone keeps its own icon-only chips and its wrapping row (v1.315.0).
 */

import { useCallback, useRef, useState } from "react";

export const FIT_FULL = 0;
export const FIT_ICONS = 1;
export const FIT_TIGHT = 2;
export const FIT_WRAP = 3;
export type FitStage = 0 | 1 | 2 | 3;

/** How much more room than a stage needed before the toolbar goes back to
 *  it. Wider than a classic Windows scrollbar (17 px), the one width that can
 *  come and go with the row itself. */
export const FIT_SLACK_PX = 24;
/** Sub-pixel layout is not an overflow. */
export const FIT_EPSILON_PX = 0.5;
/** The model chip's name never truncates below this (about nine characters at
 *  13 px); a shorter name keeps its own width. */
export const MODEL_LABEL_MIN_PX = 64;
/** The width under which the toolbar is left alone: Tailwind's `sm`. */
export const FIT_MIN_VIEWPORT_PX = 640;

export interface FitMeasure {
  /** The toolbar's inner width (inside its padding). */
  available: number;
  /** How far its right-most control ends past that width (≤ 0: it fits). */
  overflow: number;
}

export interface FitDecision {
  stage: FitStage;
  /** What each stage needed when it was last seen overflowing. */
  needed: (number | undefined)[];
}

/**
 * One decision, pure. An overflow at a one-row stage records what it needs
 * and moves on to the next stage; a fitting stage goes back one stage only
 * when the box has `FIT_SLACK_PX` more room than that stage needed.
 */
export function nextFitStage(
  stage: FitStage,
  m: FitMeasure,
  needed: readonly (number | undefined)[],
  slack: number = FIT_SLACK_PX,
): FitDecision {
  const next = [...needed];
  if (stage < FIT_WRAP && m.overflow > FIT_EPSILON_PX) {
    next[stage] = m.available + m.overflow;
    return { stage: (stage + 1) as FitStage, needed: next };
  }
  if (stage > FIT_FULL) {
    const before = next[stage - 1];
    if (before !== undefined && m.available >= before + slack) {
      return { stage: (stage - 1) as FitStage, needed: next };
    }
  }
  return { stage, needed: next };
}

/** The toolbar's controls as the user sees them: buttons, and a native
 *  select's visible chip (the reasoning level lies invisibly over its words).
 *  Controls inside a menu drawn in the toolbar (`data-fit-ignore`) are not
 *  the row. */
export function fitControls(bar: HTMLElement): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (const el of Array.from(bar.querySelectorAll<HTMLElement>("button, select"))) {
    if (el.closest("[data-fit-ignore]")) continue;
    const shown = el.tagName === "SELECT" && el.parentElement ? el.parentElement : el;
    if (!out.includes(shown)) out.push(shown);
  }
  return out;
}

/** The words the controls carry, and how many there are: a change means
 *  what each stage needs has to be measured again. */
export function fitSignature(bar: HTMLElement): string {
  return fitControls(bar)
    .map((el) => (el.textContent || "").replace(/\s+/g, " ").trim())
    .join("|");
}

/** The toolbar's inner width and how much the row lacks: how far its
 *  right-most control runs past the inner edge, or — for a group that
 *  overflows toward its start (the IJ harness's right group ends at the box's
 *  edge) — how far controls run over one another or past the left edge,
 *  whichever is more. Null while it has no box (hidden for a question in the
 *  composer's place, or not laid out). */
export function measureFit(bar: HTMLElement): FitMeasure | null {
  const box = bar.getBoundingClientRect();
  if (!(box.width > 0)) return null;
  const s = getComputedStyle(bar);
  const px = (v: string) => Number.parseFloat(v) || 0;
  const innerLeft = box.left + px(s.borderLeftWidth) + px(s.paddingLeft);
  const innerRight = box.right - px(s.borderRightWidth) - px(s.paddingRight);
  const boxes: DOMRect[] = [];
  for (const el of fitControls(bar)) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) boxes.push(r);
  }
  if (boxes.length === 0) return null;
  boxes.sort((a, b) => a.left - b.left);
  let right = Number.NEGATIVE_INFINITY;
  let edge = innerLeft;
  let overlap = 0;
  for (const r of boxes) {
    if (r.left < edge) overlap += edge - r.left;
    edge = Math.max(edge, r.right);
    right = Math.max(right, r.right);
  }
  return { available: innerRight - innerLeft, overflow: Math.max(right - innerRight, overlap) };
}

/**
 * The model chip's floor: what its chip needs besides the name, plus the name
 * up to `MODEL_LABEL_MIN_PX` — never more than the whole chip needs. Set on
 * the chip's wrapper (`data-fit-model`), which is the item flexbox shrinks.
 */
export function syncModelFloor(bar: HTMLElement, on: boolean): void {
  const wrap = bar.querySelector<HTMLElement>("[data-fit-model]");
  if (!wrap) return;
  if (!on) {
    if (wrap.style.minWidth) wrap.style.minWidth = "";
    return;
  }
  const chip = wrap.querySelector<HTMLElement>("button");
  const label = wrap.querySelector<HTMLElement>('[data-testid="model-chip-words"]');
  if (!chip || !label) return;
  const chipW = chip.getBoundingClientRect().width;
  const shown = label.getBoundingClientRect().width;
  const name = Math.max(shown, label.scrollWidth);
  const floor = Math.ceil(chipW - shown + Math.min(name, MODEL_LABEL_MIN_PX));
  const want = `${floor}px`;
  if (wrap.style.minWidth !== want) wrap.style.minWidth = want;
}

/** Applies a stage to the toolbar's attributes (absent = the old wrapping
 *  row, which is also what a phone and an inactive toolbar get). */
export function applyFit(bar: HTMLElement, stage: FitStage | null): void {
  const on = stage !== null;
  bar.toggleAttribute("data-fit-row", on && stage < FIT_WRAP);
  bar.toggleAttribute("data-fit-icons", on && stage >= FIT_ICONS);
  bar.toggleAttribute("data-fit-short", on && stage >= FIT_TIGHT);
}

/** True where the toolbar decides its own stage (from `sm` up). */
function fitActive(): boolean {
  if (typeof window === "undefined") return false;
  const mq = window.matchMedia?.(`(min-width: ${FIT_MIN_VIEWPORT_PX}px)`);
  return mq ? mq.matches : window.innerWidth >= FIT_MIN_VIEWPORT_PX;
}

/**
 * The toolbar's fit, as a callback ref (the toolbar may mount after its
 * owner, as the IJ harness's does) and the settled stage for React — FULL
 * until a stage is measured, and FULL wherever the toolbar is left alone.
 */
export function useToolbarFit(): [(el: HTMLElement | null) => void, FitStage] {
  const [stage, setStage] = useState<FitStage>(FIT_FULL);
  const told = useRef<FitStage>(FIT_FULL);
  const cleanup = useRef<(() => void) | null>(null);

  const ref = useCallback((bar: HTMLElement | null) => {
    cleanup.current?.();
    cleanup.current = null;
    if (!bar || typeof window === "undefined") return;

    let current: FitStage = FIT_FULL;
    let needed: (number | undefined)[] = [];
    let signature = "";
    /** Words changed while the toolbar had no box (a question in the
     *  composer's place): measured from FULL once it is back. Until then it
     *  keeps the stage it had, so it comes back looking as it left. */
    let stale = false;
    const tell = (s: FitStage) => {
      if (told.current === s) return;
      told.current = s;
      setStage(s);
    };

    const settle = (reset: boolean) => {
      if (!fitActive()) {
        applyFit(bar, null);
        syncModelFloor(bar, false);
        current = FIT_FULL;
        needed = [];
        tell(FIT_FULL);
        return;
      }
      if (!(bar.getBoundingClientRect().width > 0)) {
        if (reset) stale = true;
        return;
      }
      if (reset || stale) {
        current = FIT_FULL;
        needed = [];
        stale = false;
      }
      applyFit(bar, current);
      for (let i = 0; i < 8; i++) {
        syncModelFloor(bar, current < FIT_WRAP);
        const m = measureFit(bar);
        if (!m) break;
        const next = nextFitStage(current, m, needed);
        needed = next.needed;
        if (next.stage === current) break;
        current = next.stage;
        applyFit(bar, current);
      }
      tell(current);
    };

    // A resize (the window, a chip's own box) is re-measured on the next
    // frame: deciding inside a ResizeObserver callback would resize the very
    // controls it watches, which the browser reports as an undelivered loop.
    let frame = 0;
    const later = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        settle(false);
      });
    };
    let watched: HTMLElement[] = [];
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(later) : null;
    const watch = () => {
      if (!ro) return;
      const now = fitControls(bar);
      for (const el of watched) if (!now.includes(el)) ro.unobserve(el);
      for (const el of now) if (!watched.includes(el)) ro.observe(el);
      watched = now;
    };
    // New WORDS on the controls (a model picked, the reasoning chip coming or
    // going, the tools chip's count) start again from FULL, at once (a
    // mutation is handled before the next paint). Anything else the observer
    // sees — a menu opening inside the toolbar, the mic's pulse — changes no
    // words and is left alone. Typing changes nothing it watches.
    const mo =
      typeof MutationObserver !== "undefined"
        ? new MutationObserver(() => {
            watch();
            const sig = fitSignature(bar);
            if (sig === signature) return;
            signature = sig;
            settle(true);
          })
        : null;
    // The app's font arriving after the first layout changes every chip's
    // width: what each stage needs is measured again.
    const fonts = typeof document !== "undefined" ? document.fonts : undefined;
    const fontsDone = () => settle(true);
    fonts?.addEventListener?.("loadingdone", fontsDone);

    signature = fitSignature(bar);
    ro?.observe(bar);
    watch();
    mo?.observe(bar, { childList: true, characterData: true, subtree: true });
    settle(true);

    cleanup.current = () => {
      if (frame) window.cancelAnimationFrame(frame);
      ro?.disconnect();
      mo?.disconnect();
      fonts?.removeEventListener?.("loadingdone", fontsDone);
    };
  }, []);

  return [ref, stage];
}
