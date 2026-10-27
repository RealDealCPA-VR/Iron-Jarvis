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
 * The toolbar now stays one row and gives up space ONE STEP AT A TIME, the
 * least needed first (the review of v1.400.0: the first cut dropped every
 * chip's words at once, which left a wide hole between the chips and the
 * model, and cut the model's name before anything cheaper went — the user's
 * "Qwen3.8-27B (RTX)" lost "(RTX)", the part that names the machine). Each
 * toolbar names its own ORDER of steps (`CHAT_FIT_ORDER`,
 * `HARNESS_FIT_ORDER`); the chat's is
 *
 *   0. FULL    — every chip with its words and the model's WHOLE name.
 *   1. project — the project chip (a saved chat in a project) shows its
 *                folder alone: the top bar's breadcrumb still names it.
 *   2. web     — "Web" shows its globe alone.
 *   3. tools   — "Auto tools" shows its wrench alone ("Tools off" keeps a
 *                short "Off", and tools turned on by hand keep their count:
 *                a wrench alone would say neither).
 *   4. local   — "Local only" says "Local", the phone's word (v1.356.0 G75:
 *                a house alone says nothing); on, it keeps its glow.
 *   5. name    — the model's name may truncate, down to about thirteen
 *                characters (`MODEL_LABEL_MIN_PX`).
 *   6. ask     — the permission chip shows its shield alone (each level has
 *                its own shield; the no-ask level keeps its amber).
 *   7. short   — the reasoning chip's words are cut short.
 *   8. tight   — the model's name may go down to about nine characters
 *                (`MODEL_LABEL_TIGHT_PX`) rather than the row wrapping.
 *   9. WRAP    — the last resort, only for a box too narrow for even that (a
 *                narrow IJ harness column): the old wrapping row, all steps
 *                taken.
 *
 * WHICH step is decided by MEASURING, never by guessing a breakpoint: at the
 * current level the row either fits (the right-most control ends inside the
 * box) or overflows by a measured amount. An overflow records what that level
 * NEEDS (`available + overflow`) and takes the next step; a step is only
 * given back once the box has `FIT_SLACK_PX` more room than the level before
 * it needed (hysteresis, per step), so a box resized by a few pixels back and
 * forth — or a scrollbar that comes and goes with the row — never flickers.
 *
 * The level is applied as ATTRIBUTES on the toolbar (`data-fit-row` while it
 * is one row, and `data-fit-<step>` for every step taken), which the chips
 * read through `group-data-[fit-<step>]/fit:` classes (lib/composerChips:
 * `fitIconChip`, `fitWords`, `fitShows`). The whole measure → decide → apply
 * loop runs synchronously, in one go, outside React: the page never
 * re-renders for it (v1.250.0: it must not re-render per keystroke, and
 * typing changes nothing the observers watch). React is told the settled
 * level afterwards (state, set only when it changes) for the one thing CSS
 * cannot do: the tooltips of chips that lost their words, which then lead
 * with those words.
 *
 * The model chip's floor (the width its name may not truncate below) is
 * computed from its parts — padding, the account and the chevron, and the
 * name's own width up to the step's minimum — never from the chip's current
 * width, which already holds the previous floor (the review found that
 * ratchet: after "Qwen3.8-27B (RTX)", "brain (RTX)" kept the longer chip,
 * 93 px of it empty, and the chips lost their words for nothing).
 *
 * What it watches: a change of the controls' WORDS (MutationObserver: a new
 * model, the reasoning chip coming or going, the tools chip's count, before
 * the next paint) starts again from FULL, because what each level needs was
 * measured with the old words; so does the app's font arriving. The
 * toolbar's size and each control's size (ResizeObserver: a window resize, a
 * chip whose weight changes) re-measure at the current level on the next
 * frame. Menus drawn inside the toolbar carry `data-fit-ignore` and change no
 * words: opening one changes nothing. Below `sm` (a phone) it does nothing:
 * the phone keeps its own icon-only chips and its wrapping row (v1.315.0).
 */

import { useCallback, useRef, useState } from "react";

/** One way a toolbar gives up space. A chip opts in with its step's classes
 *  (lib/composerChips); `name`, `short` and `tight` are the model chip's floor
 *  and the reasoning chip's words. */
export type FitStep = "project" | "web" | "tools" | "name" | "local" | "ask" | "tryon" | "plan" | "short" | "tight";

/** Every step, for the attributes (a step a toolbar does not use is never set). */
export const FIT_STEPS: readonly FitStep[] = [
  "project",
  "web",
  "tools",
  "name",
  "local",
  "ask",
  "tryon",
  "plan",
  "short",
  "tight",
];

/**
 * The chat composer's order, least needed first. The model's name keeps every
 * character until the words that are cheapest to lose have gone: the project
 * (already in the breadcrumb), Web, Auto tools (the everyday default), and
 * "only" from "Local only" ("Local" says the same; the phone has always said
 * it). The name's end is what truncates, and the end is usually the machine
 * ("Qwen3.8-27B (RTX)"), so it goes after those. The permission level gives
 * up its words after the name, and the reasoning words last.
 */
export const CHAT_FIT_ORDER: readonly FitStep[] = ["project", "web", "tools", "local", "name", "ask", "short", "tight"];

/**
 * The IJ harness composer's order: "Try on…" (an occasional tool) first,
 * then the model's name, then "Plan first" (on, "Planning" keeps its word),
 * then the permission level.
 */
export const HARNESS_FIT_ORDER: readonly FitStep[] = ["tryon", "name", "plan", "ask", "short", "tight"];

/** How much more room than a level needed before the toolbar goes back to
 *  it. Wider than a classic Windows scrollbar (17 px), the one width that can
 *  come and go with the row itself. */
export const FIT_SLACK_PX = 24;
/** Sub-pixel layout is not an overflow. */
export const FIT_EPSILON_PX = 0.5;
/** From the `name` step: the model chip's name never truncates below this
 *  (about thirteen characters at 13 px); a shorter name keeps its own width. */
export const MODEL_LABEL_MIN_PX = 96;
/** From the `tight` step (a narrow IJ harness column): about nine characters,
 *  before the row would have to wrap. */
export const MODEL_LABEL_TIGHT_PX = 64;
/** The width under which the toolbar is left alone: Tailwind's `sm`. */
export const FIT_MIN_VIEWPORT_PX = 640;

/** The level that wraps (every step taken, the old wrapping row). */
export function fitWrapLevel(order: readonly FitStep[]): number {
  return order.length + 1;
}

/** The steps taken at a level. */
export function fitStepsAt(order: readonly FitStep[], level: number): FitStep[] {
  return order.slice(0, Math.max(0, Math.min(level, order.length)));
}

/** True when `step` is taken at `level` (for a chip's tooltip). */
export function fitStepOn(order: readonly FitStep[], level: number, step: FitStep): boolean {
  const at = order.indexOf(step);
  return at >= 0 && level > at;
}

export interface FitMeasure {
  /** The toolbar's inner width (inside its padding). */
  available: number;
  /** How far its right-most control ends past that width (≤ 0: it fits). */
  overflow: number;
}

export interface FitDecision {
  level: number;
  /** What each level needed when it was last seen overflowing. */
  needed: (number | undefined)[];
}

/**
 * One decision, pure. An overflow at a one-row level records what it needs
 * and takes the next step; a fitting level gives back one step only when the
 * box has `FIT_SLACK_PX` more room than the level before it needed.
 */
export function nextFitLevel(
  level: number,
  m: FitMeasure,
  needed: readonly (number | undefined)[],
  wrap: number,
  slack: number = FIT_SLACK_PX,
): FitDecision {
  const next = [...needed];
  if (level < wrap && m.overflow > FIT_EPSILON_PX) {
    next[level] = m.available + m.overflow;
    return { level: level + 1, needed: next };
  }
  if (level > 0) {
    const before = next[level - 1];
    if (before !== undefined && m.available >= before + slack) {
      return { level: level - 1, needed: next };
    }
  }
  return { level, needed: next };
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
 *  what each level needs has to be measured again. */
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
 * The model chip's floor: the chip's own parts (its padding and border, the
 * account it names, the chevron, the gaps between them) plus its name up to
 * `labelMin` — `Infinity` before the `name` step, so the whole name; never
 * more than the chip's own `max-width`. Set on the chip's wrapper
 * (`data-fit-model`), which is the item flexbox shrinks.
 *
 * Measured from the PARTS, never from the chip's width: the chip fills its
 * wrapper (`w-full`), so its width already holds the floor set last time, and
 * a floor read off it could only grow (a short name picked after a long one
 * kept the long chip, empty inside).
 */
export function syncModelFloor(bar: HTMLElement, on: boolean, labelMin: number = MODEL_LABEL_MIN_PX): void {
  const wrap = bar.querySelector<HTMLElement>("[data-fit-model]");
  if (!wrap) return;
  if (!on) {
    if (wrap.style.minWidth) wrap.style.minWidth = "";
    return;
  }
  const chip = wrap.querySelector<HTMLElement>("button");
  const label = wrap.querySelector<HTMLElement>('[data-testid="model-chip-words"]');
  if (!chip || !label) return;
  const s = getComputedStyle(chip);
  const px = (v: string) => Number.parseFloat(v) || 0;
  let parts = px(s.paddingLeft) + px(s.paddingRight) + px(s.borderLeftWidth) + px(s.borderRightWidth);
  let items = 0;
  for (const kid of Array.from(chip.children) as HTMLElement[]) {
    if (kid === label) {
      items += 1;
      continue;
    }
    const w = kid.getBoundingClientRect().width;
    if (w > 0) {
      parts += w;
      items += 1;
    }
  }
  parts += px(s.columnGap) * Math.max(0, items - 1);
  const name = Math.max(label.getBoundingClientRect().width, label.scrollWidth);
  const cap = px(s.maxWidth) || Number.POSITIVE_INFINITY;
  const floor = Math.ceil(Math.min(parts + Math.min(name, labelMin), cap));
  const want = `${floor}px`;
  if (wrap.style.minWidth !== want) wrap.style.minWidth = want;
}

/** The name's minimum at a level: the whole name, then about thirteen
 *  characters from the `name` step, about nine from `tight`. */
export function modelLabelMin(order: readonly FitStep[], level: number): number {
  if (fitStepOn(order, level, "tight")) return MODEL_LABEL_TIGHT_PX;
  if (fitStepOn(order, level, "name")) return MODEL_LABEL_MIN_PX;
  return Number.POSITIVE_INFINITY;
}

/** Applies a level to the toolbar's attributes (null = the old wrapping
 *  row with every word, which is what a phone and an inactive toolbar get). */
export function applyFit(bar: HTMLElement, order: readonly FitStep[], level: number | null): void {
  const on = level !== null;
  const taken = on ? fitStepsAt(order, level) : [];
  bar.toggleAttribute("data-fit-row", on && level < fitWrapLevel(order));
  for (const step of FIT_STEPS) bar.toggleAttribute(`data-fit-${step}`, taken.includes(step));
}

/** True where the toolbar decides its own level (from `sm` up). */
function fitActive(): boolean {
  if (typeof window === "undefined") return false;
  const mq = window.matchMedia?.(`(min-width: ${FIT_MIN_VIEWPORT_PX}px)`);
  return mq ? mq.matches : window.innerWidth >= FIT_MIN_VIEWPORT_PX;
}

/**
 * The toolbar's fit, as a callback ref (the toolbar may mount after its
 * owner, as the IJ harness's does) and the settled level for React — 0 (FULL)
 * until a level is measured, and 0 wherever the toolbar is left alone.
 * `order` is the toolbar's steps, a module constant (`CHAT_FIT_ORDER`,
 * `HARNESS_FIT_ORDER`).
 */
export function useToolbarFit(order: readonly FitStep[]): [(el: HTMLElement | null) => void, number] {
  const [level, setLevel] = useState(0);
  const told = useRef(0);
  const cleanup = useRef<(() => void) | null>(null);
  const orderRef = useRef(order);
  orderRef.current = order;

  const ref = useCallback((bar: HTMLElement | null) => {
    cleanup.current?.();
    cleanup.current = null;
    if (!bar || typeof window === "undefined") return;

    const steps = orderRef.current;
    const wrap = fitWrapLevel(steps);
    let current = 0;
    let needed: (number | undefined)[] = [];
    let signature = "";
    /** Words changed while the toolbar had no box (a question in the
     *  composer's place): measured from FULL once it is back. Until then it
     *  keeps the level it had, so it comes back looking as it left. */
    let stale = false;
    const tell = (l: number) => {
      if (told.current === l) return;
      told.current = l;
      setLevel(l);
    };

    const settle = (reset: boolean) => {
      if (!fitActive()) {
        applyFit(bar, steps, null);
        syncModelFloor(bar, false);
        current = 0;
        needed = [];
        tell(0);
        return;
      }
      if (!(bar.getBoundingClientRect().width > 0)) {
        if (reset) stale = true;
        return;
      }
      if (reset || stale) {
        current = 0;
        needed = [];
        stale = false;
      }
      applyFit(bar, steps, current);
      for (let i = 0; i < 2 * (wrap + 1); i++) {
        syncModelFloor(bar, current < wrap, modelLabelMin(steps, current));
        const m = measureFit(bar);
        if (!m) break;
        const next = nextFitLevel(current, m, needed, wrap);
        needed = next.needed;
        if (next.level === current) break;
        current = next.level;
        applyFit(bar, steps, current);
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
    // width: what each level needs is measured again.
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

  return [ref, level];
}
