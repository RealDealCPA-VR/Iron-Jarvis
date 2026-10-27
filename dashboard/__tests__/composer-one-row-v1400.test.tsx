/**
 * v1.400.0 — the composer's toolbar is ONE ROW whatever model is picked.
 *
 * The user's report (2026-10-27): "it looks clean until the model with the
 * longer name is selected and then things start to look a bit off with
 * spacing". The layout itself can only be seen in a real browser
 * (e2e/composer-one-row-v1400.spec.ts); pinned here:
 *
 *  - the ORDER (review): the toolbar gives up space one step at a time,
 *    least needed first — the project's name, Web, Auto tools, "Local only"
 *    to "Local", then the model's name truncates, then the permission level,
 *    the reasoning words, the name's last characters (CHAT_FIT_ORDER); the
 *    IJ harness has its own (HARNESS_FIT_ORDER);
 *  - the level decision (lib/toolbarFit `nextFitLevel`): an overflow records
 *    what the level needs and takes the next step; a step is only given back
 *    with `FIT_SLACK_PX` more room than the level before it needed, so a box
 *    jittering by a few pixels around a boundary never flickers (with no
 *    slack, the same jitter flips the row on every step);
 *  - the measuring helpers: what counts as a control (a menu drawn inside the
 *    toolbar does not; the reasoning select is its chip), what counts as a
 *    change of words (the signature), the model chip's floor (read from its
 *    PARTS — the review found the first cut read it off the chip's own width,
 *    which held the last floor, so it only ever grew);
 *  - the chips: each keeps its aria-label, id and data-testid when it drops
 *    its words; an icon-only chip's tooltip leads with the words it no
 *    longer shows; a state the words carried is still told without them (the
 *    review: "Tools off", "Ask first", "Planning" and models picked to try
 *    on had nothing left but a hue, or not even that; "Local only" keeps the
 *    phone's "Local", never a house alone, v1.356.0 G75).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { useEffect } from "react";

import {
  CHAT_FIT_ORDER,
  FIT_SLACK_PX,
  FIT_STEPS,
  HARNESS_FIT_ORDER,
  MODEL_LABEL_MIN_PX,
  MODEL_LABEL_TIGHT_PX,
  applyFit,
  fitControls,
  fitSignature,
  fitStepOn,
  fitStepsAt,
  fitWrapLevel,
  measureFit,
  modelLabelMin,
  nextFitLevel,
  syncModelFloor,
  useToolbarFit,
  type FitStep,
} from "@/lib/toolbarFit";
import { fitIconChip, fitShows, fitWords, iconOnlyTitle, type FitChipStep } from "@/lib/composerChips";
import { reasoningShortLabel } from "@/lib/reasoningChip";
import { PermissionChip } from "@/components/chat/PermissionChip";
import { ModelMenu, ReasoningChip } from "@/components/chat/ModelMenu";

const read = (...parts: string[]) =>
  readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

/** A toolbar whose levels need these widths: settles the way the hook does
 *  (measure, decide, until the level holds) for one box width. */
function settleAt(
  start: number,
  needed0: (number | undefined)[],
  available: number,
  needs: number[],
  slack = FIT_SLACK_PX,
): { level: number; needed: (number | undefined)[] } {
  const wrap = needs.length;
  let level = start;
  let needed = needed0;
  for (let i = 0; i < 2 * (wrap + 1); i++) {
    const overflow = level >= wrap ? -1 : needs[level] - available;
    const next = nextFitLevel(level, { available, overflow }, needed, wrap, slack);
    needed = next.needed;
    if (next.level === level) break;
    level = next.level;
  }
  return { level, needed };
}

describe("the order: one step at a time, least needed first", () => {
  it("the chat gives up the project's name, Web, Auto tools and 'only' before the model's name", () => {
    expect(CHAT_FIT_ORDER).toEqual(["project", "web", "tools", "local", "name", "ask", "short", "tight"]);
    // The model's name truncates only after the four cheap steps, and its
    // last characters (the tight floor) go after everything else.
    expect(CHAT_FIT_ORDER.indexOf("name")).toBe(4);
    expect(CHAT_FIT_ORDER[CHAT_FIT_ORDER.length - 1]).toBe("tight");
  });

  it("the IJ harness gives up 'Try on…' first, then the name, then 'Plan first'", () => {
    expect(HARNESS_FIT_ORDER).toEqual(["tryon", "name", "plan", "ask", "short", "tight"]);
  });

  it("every step a toolbar takes is one the attributes know", () => {
    for (const s of [...CHAT_FIT_ORDER, ...HARNESS_FIT_ORDER]) expect(FIT_STEPS).toContain(s);
  });

  it("a level is the steps before it; the wrap level takes them all", () => {
    expect(fitStepsAt(CHAT_FIT_ORDER, 0)).toEqual([]);
    expect(fitStepsAt(CHAT_FIT_ORDER, 2)).toEqual(["project", "web"]);
    expect(fitWrapLevel(CHAT_FIT_ORDER)).toBe(CHAT_FIT_ORDER.length + 1);
    expect(fitStepsAt(CHAT_FIT_ORDER, fitWrapLevel(CHAT_FIT_ORDER))).toEqual([...CHAT_FIT_ORDER]);
    expect(fitStepOn(CHAT_FIT_ORDER, 2, "web")).toBe(true);
    expect(fitStepOn(CHAT_FIT_ORDER, 2, "tools")).toBe(false);
    // A step the toolbar does not have is never on.
    expect(fitStepOn(CHAT_FIT_ORDER, 99, "plan")).toBe(false);
  });

  it("the model's name keeps every character until its step, then about thirteen, then about nine", () => {
    const name = CHAT_FIT_ORDER.indexOf("name");
    for (let l = 0; l <= name; l++) expect(modelLabelMin(CHAT_FIT_ORDER, l)).toBe(Number.POSITIVE_INFINITY);
    expect(modelLabelMin(CHAT_FIT_ORDER, name + 1)).toBe(MODEL_LABEL_MIN_PX);
    expect(modelLabelMin(CHAT_FIT_ORDER, CHAT_FIT_ORDER.length)).toBe(MODEL_LABEL_TIGHT_PX);
    expect(MODEL_LABEL_TIGHT_PX).toBeLessThan(MODEL_LABEL_MIN_PX);
  });
});

describe("the level decision (nextFitLevel)", () => {
  const WRAP = 4;

  it("an overflow records what the level needs and takes the next step", () => {
    const d = nextFitLevel(0, { available: 700, overflow: 37 }, [], WRAP);
    expect(d.level).toBe(1);
    expect(d.needed[0]).toBe(737);
    expect(nextFitLevel(1, { available: 500, overflow: 4 }, d.needed, WRAP).level).toBe(2);
    expect(nextFitLevel(3, { available: 400, overflow: 4 }, d.needed, WRAP).level).toBe(4);
  });

  it("sub-pixel layout is not an overflow", () => {
    expect(nextFitLevel(0, { available: 700, overflow: 0.4 }, [], WRAP).level).toBe(0);
  });

  it("the wrapping row is the last resort: it never steps further", () => {
    expect(nextFitLevel(WRAP, { available: 300, overflow: 50 }, [800, 700, 600, 500], WRAP).level).toBe(WRAP);
  });

  it("gives back one step only with FIT_SLACK_PX more room than the level before it needed", () => {
    const needed = [737];
    expect(nextFitLevel(1, { available: 737, overflow: -100 }, needed, WRAP).level).toBe(1);
    expect(nextFitLevel(1, { available: 737 + FIT_SLACK_PX - 1, overflow: -100 }, needed, WRAP).level).toBe(1);
    expect(nextFitLevel(1, { available: 737 + FIT_SLACK_PX, overflow: -100 }, needed, WRAP).level).toBe(0);
    // Nothing known about the level before: stay.
    expect(nextFitLevel(1, { available: 2000, overflow: -1000 }, [], WRAP).level).toBe(1);
  });

  it("settles in one go: one step per overflow, as few as the box needs, and back with room", () => {
    // Each step saves a little: a box a little short takes ONE step, not all.
    const needs = [760, 730, 700, 640];
    expect(settleAt(0, [], 745, needs).level).toBe(1);
    expect(settleAt(0, [], 715, needs).level).toBe(2);
    const narrow = settleAt(0, [], 600, needs);
    expect(narrow.level).toBe(4);
    expect(narrow.needed).toEqual([760, 730, 700, 640]);
    expect(settleAt(4, narrow.needed, 900, needs).level).toBe(0);
    expect(settleAt(4, narrow.needed, 760, needs).level).toBe(1);
  });

  it("A FEW PIXELS OF JITTER AROUND A BOUNDARY NEVER FLICKERS (and with no slack it does)", () => {
    // A row that needs 740 px at FULL and 700 px with one step taken. The box
    // goes back and forth by a few pixels around 740 (a window being sized,
    // a scrollbar that comes and goes with the row).
    const needs = [740, 700, 660, 620];
    const widths = [745, 738, 742, 737, 744, 739, 746, 736, 750, 741];
    const flips = (slack: number) => {
      let s: { level: number; needed: (number | undefined)[] } = { level: 0, needed: [] };
      let changes = 0;
      for (const w of widths) {
        const next = settleAt(s.level, s.needed, w, needs, slack);
        if (next.level !== s.level) changes += 1;
        s = next;
      }
      return changes;
    };
    // With the slack: it takes the step once and keeps it.
    expect(flips(FIT_SLACK_PX)).toBe(1);
    // Without it, the same jitter flips the row nearly every step.
    expect(flips(0)).toBeGreaterThanOrEqual(6);
  });
});

/** A fake box for jsdom, which lays nothing out. */
function box(el: Element, r: { left: number; right: number; top?: number; bottom?: number }) {
  const top = r.top ?? 0;
  const bottom = r.bottom ?? 30;
  vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
    left: r.left,
    right: r.right,
    top,
    bottom,
    width: r.right - r.left,
    height: bottom - top,
    x: r.left,
    y: top,
    toJSON: () => ({}),
  } as DOMRect);
}

describe("the measuring helpers", () => {
  function bar(account = false) {
    document.body.innerHTML = `
      <div id="bar" style="padding-left:8px;padding-right:8px">
        <div id="left">
          <div><button id="plus">+</button>
            <div data-fit-ignore><button id="menu-row">Attach files</button></div></div>
          <button id="local" aria-label="Local only"><span>Local only</span></button>
        </div>
        <div id="right">
          <span id="reasoning"><span>Reasoning</span><select aria-label="Reasoning level"><option>Default</option></select></span>
          <div data-fit-model><button id="model" style="padding-left:8px;padding-right:8px;column-gap:6px;max-width:256px"><span data-testid="model-chip-words">Qwen3.8-27B (RTX)</span>${
            account ? '<span id="account">· Work</span>' : ""
          }<svg id="chevron"></svg></button></div>
          <button id="send" aria-label="Send"></button>
        </div>
      </div>`;
    return document.getElementById("bar") as HTMLElement;
  }

  it("a control is a button or a select's chip; a menu drawn in the toolbar is not the row", () => {
    const b = bar();
    const ids = fitControls(b).map((el) => el.id);
    expect(ids).toEqual(["plus", "local", "reasoning", "model", "send"]);
  });

  it("the signature changes with the controls' words, never with a menu inside the toolbar", () => {
    const b = bar();
    const before = fitSignature(b);
    (document.getElementById("menu-row") as HTMLElement).textContent = "Something else";
    expect(fitSignature(b)).toBe(before);
    (document.querySelector('[data-testid="model-chip-words"]') as HTMLElement).textContent = "brain (RTX)";
    expect(fitSignature(b)).not.toBe(before);
  });

  it("measures the inner width and how far the right-most control runs past it", () => {
    const b = bar();
    box(b, { left: 100, right: 860 });
    for (const [id, l, r] of [
      ["plus", 108, 138],
      ["local", 142, 230],
      ["reasoning", 600, 690],
      ["model", 694, 820],
      ["send", 824, 880],
    ] as const) {
      box(document.getElementById(id) as HTMLElement, { left: l, right: r });
    }
    // The menu's own row is not measured even when it runs further.
    box(document.getElementById("menu-row") as HTMLElement, { left: 108, right: 2000 });
    expect(measureFit(b)).toEqual({ available: 744, overflow: 28 });
  });

  it("a group that overflows toward its start (controls run over one another) is an overflow too", () => {
    // The IJ harness's right group ends at the box's edge: squeezed, its
    // controls ran LEFT over the chips instead of past the right edge, and
    // the row read as fitting while words were drawn on top of each other.
    const b = bar();
    box(b, { left: 100, right: 860 });
    for (const [id, l, r] of [
      ["plus", 108, 138],
      ["local", 142, 230],
      ["reasoning", 200, 290],
      ["model", 294, 800],
      ["send", 804, 852],
    ] as const) {
      box(document.getElementById(id) as HTMLElement, { left: l, right: r });
    }
    expect(measureFit(b)).toEqual({ available: 744, overflow: 30 });
    // Past the left edge counts as well.
    box(document.getElementById("plus") as HTMLElement, { left: 90, right: 120 });
    box(document.getElementById("local") as HTMLElement, { left: 124, right: 196 });
    expect(measureFit(b)?.overflow).toBe(18);
  });

  it("a toolbar with no box (hidden for a question) is not measured", () => {
    const b = bar();
    expect(measureFit(b)).toBeNull();
  });

  it("the model chip's floor: its parts plus the name up to the step's minimum", () => {
    const b = bar();
    const wrap = b.querySelector("[data-fit-model]") as HTMLElement;
    const label = b.querySelector('[data-testid="model-chip-words"]') as HTMLElement;
    // A long name, squeezed: padding 16 + gap 6 + chevron 12 = 34 px of parts.
    box(label, { left: 8, right: 64 });
    box(document.getElementById("chevron") as Element, { left: 70, right: 82 });
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 140 });
    // Before the name's step: the whole name.
    syncModelFloor(b, true, Number.POSITIVE_INFINITY);
    expect(wrap.style.minWidth).toBe(`${34 + 140}px`);
    syncModelFloor(b, true);
    expect(wrap.style.minWidth).toBe(`${34 + MODEL_LABEL_MIN_PX}px`);
    // At the tightest step the name may go down further, before a wrap.
    syncModelFloor(b, true, MODEL_LABEL_TIGHT_PX);
    expect(wrap.style.minWidth).toBe(`${34 + MODEL_LABEL_TIGHT_PX}px`);
    // A short name: the floor is the chip it needs.
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 40 });
    box(label, { left: 8, right: 48 });
    syncModelFloor(b, true);
    expect(wrap.style.minWidth).toBe("74px");
    // Never more than the chip's own max-width: the whole of a very long name
    // would leave the wrapper wider than the chip, empty.
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 400 });
    syncModelFloor(b, true, Number.POSITIVE_INFINITY);
    expect(wrap.style.minWidth).toBe("256px");
    // Off (a phone, the wrapping row): no floor.
    syncModelFloor(b, false);
    expect(wrap.style.minWidth).toBe("");
  });

  it("THE FLOOR NEVER RATCHETS: it is read from the chip's parts, never from its width", () => {
    // The review: a short name picked after a long one. The chip fills its
    // wrapper (w-full), so it was still as wide as the last floor; a floor
    // read off the chip's width (`chip - label + name`) kept it — 93 px of
    // empty chip in the real browser, and the chips lost their words for it.
    const b = bar();
    const wrap = b.querySelector("[data-fit-model]") as HTMLElement;
    const chip = document.getElementById("model") as HTMLElement;
    const label = b.querySelector('[data-testid="model-chip-words"]') as HTMLElement;
    wrap.style.minWidth = "187px";
    box(chip, { left: 0, right: 187 });
    box(label, { left: 8, right: 76 });
    box(document.getElementById("chevron") as Element, { left: 82, right: 94 });
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 68 });
    syncModelFloor(b, true, Number.POSITIVE_INFINITY);
    expect(wrap.style.minWidth).toBe(`${34 + 68}px`);
  });

  it("the account the chip names is part of the floor (the IJ harness)", () => {
    const b = bar(true);
    const wrap = b.querySelector("[data-fit-model]") as HTMLElement;
    const label = b.querySelector('[data-testid="model-chip-words"]') as HTMLElement;
    box(label, { left: 8, right: 64 });
    box(document.getElementById("account") as Element, { left: 70, right: 120 });
    box(document.getElementById("chevron") as Element, { left: 126, right: 138 });
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 140 });
    syncModelFloor(b, true);
    // padding 16 + account 50 + chevron 12 + two gaps 12 = 90, then the name.
    expect(wrap.style.minWidth).toBe(`${90 + MODEL_LABEL_MIN_PX}px`);
  });

  it("a level is attributes: the row, and one per step taken; none is the old wrapping row", () => {
    const b = bar();
    const on = () => FIT_STEPS.filter((s) => b.hasAttribute(`data-fit-${s}`));
    applyFit(b, CHAT_FIT_ORDER, 0);
    expect(b.hasAttribute("data-fit-row")).toBe(true);
    expect(on()).toEqual([]);
    applyFit(b, CHAT_FIT_ORDER, 2);
    expect(on()).toEqual(["project", "web"]);
    applyFit(b, CHAT_FIT_ORDER, CHAT_FIT_ORDER.length);
    expect(b.hasAttribute("data-fit-row")).toBe(true);
    expect(on()).toEqual(FIT_STEPS.filter((s) => CHAT_FIT_ORDER.includes(s)));
    applyFit(b, CHAT_FIT_ORDER, fitWrapLevel(CHAT_FIT_ORDER));
    expect(b.hasAttribute("data-fit-row")).toBe(false);
    expect(on().length).toBe(CHAT_FIT_ORDER.length);
    // A harness level never sets a chat step.
    applyFit(b, HARNESS_FIT_ORDER, 1);
    expect(on()).toEqual(["tryon"]);
    applyFit(b, CHAT_FIT_ORDER, null);
    expect(b.hasAttribute("data-fit-row")).toBe(false);
    expect(on()).toEqual([]);
  });
});

describe("the hook watches words, never what typing changes", () => {
  function Bar({ order, onLevel }: { order: readonly FitStep[]; onLevel: (l: number) => void }) {
    const [ref, level] = useToolbarFit(order);
    useEffect(() => {
      onLevel(level);
    }, [level, onLevel]);
    return (
      <div ref={ref} data-testid="bar">
        <button type="button" id="send" aria-label="Send" disabled>
          ↑
        </button>
        <span data-fit-model="">
          <button type="button">
            <span data-testid="model-chip-words">brain</span>
          </button>
        </span>
      </div>
    );
  }

  it("a control's attribute changing (Send waking as you type) settles nothing; new words do", async () => {
    // jsdom: no matchMedia, a 1024 px window — the hook is active.
    expect(window.innerWidth).toBeGreaterThanOrEqual(640);
    const levels: number[] = [];
    render(<Bar order={CHAT_FIT_ORDER} onLevel={(l) => levels.push(l)} />);
    const bar = screen.getByTestId("bar");
    const spy = vi.spyOn(bar, "getBoundingClientRect");
    const send = document.getElementById("send") as HTMLButtonElement;
    await act(async () => {
      for (let i = 0; i < 20; i++) {
        send.disabled = i % 2 === 0;
        send.className = i % 2 === 0 ? "btn-accent" : "quiet";
      }
      await Promise.resolve();
    });
    expect(spy).not.toHaveBeenCalled();
    await act(async () => {
      (screen.getByTestId("model-chip-words") as HTMLElement).textContent = "Qwen3.8-27B (RTX)";
      await Promise.resolve();
    });
    expect(spy).toHaveBeenCalled();
    expect(levels).toEqual([0]);
  });
});

describe("the chips keep their names when they drop their words", () => {
  it("an icon-only chip's tooltip leads with the words it no longer shows", () => {
    expect(iconOnlyTitle("Local only", "Keep this chat on this PC.")).toBe("Local only: Keep this chat on this PC.");
    expect(iconOnlyTitle("Web", "Let this chat search the web and read pages")).toBe(
      "Web: Let this chat search the web and read pages",
    );
    // Already said: not twice.
    expect(iconOnlyTitle("Auto tools", "Auto tools is off.")).toBe("Auto tools is off.");
  });

  it("each chip step's classes read its own attribute, and are whole pixels", () => {
    const steps: FitChipStep[] = ["project", "web", "tools", "local", "ask", "tryon", "plan"];
    for (const s of steps) {
      expect(fitWords(s)).toBe(`group-data-[fit-${s}]/fit:hidden`);
      expect(fitShows(s)).toBe(`group-data-[fit-${s}]/fit:inline`);
      expect(fitIconChip(s)).toContain(`group-data-[fit-${s}]/fit:w-[30px]`);
      expect(`${fitWords(s)} ${fitIconChip(s)} ${fitShows(s)}`).not.toMatch(/\.5px|\[\d+\.\d+px\]/);
      // One step's classes never answer another step's attribute.
      for (const o of steps.filter((x) => x !== s)) expect(fitIconChip(s)).not.toContain(`fit-${o}]`);
    }
  });

  it("the permission chip keeps its id, testid, aria-label and amber; its tooltip names the level when icon-only", () => {
    const { rerender } = render(<PermissionChip id="chat-approval-mode" value="yolo" onChange={() => {}} iconOnlyOnPhone />);
    let chip = screen.getByTestId("permission-chip");
    expect(chip.id).toBe("chat-approval-mode");
    const name = chip.getAttribute("aria-label") ?? "";
    expect(name).toMatch(/^Permissions: /);
    const words = name.replace(/^Permissions: /, "");
    expect(chip.getAttribute("data-tone")).toBe("warn");
    expect(chip.className).toContain("text-tone-warn");
    expect(chip.className).toContain(fitIconChip("ask"));
    const plainTitle = chip.getAttribute("title") ?? "";
    expect(plainTitle.startsWith(words)).toBe(false);
    rerender(<PermissionChip id="chat-approval-mode" value="yolo" onChange={() => {}} iconOnlyOnPhone iconOnly />);
    chip = screen.getByTestId("permission-chip");
    expect(chip.getAttribute("aria-label")).toBe(name);
    expect(chip.getAttribute("title")).toBe(`${words}: ${plainTitle}`);
    expect(chip.getAttribute("data-tone")).toBe("warn");
    // Its words and chevron are the ones the toolbar's `ask` step hides.
    const label = Array.from(chip.querySelectorAll("span")).find((s) => s.textContent === words);
    expect(label?.className).toContain(fitWords("ask"));
  });

  it("EACH LEVEL HAS ITS OWN SHIELD: the shield alone still says which level is on", () => {
    // The review: "Ask first" and "Ask when risky" shared one plain shield in
    // the same grey, so with their words gone (a phone, or the `ask` step)
    // nothing on screen told them apart.
    const icon = (value: string) => {
      render(<PermissionChip value={value} onChange={() => {}} iconOnlyOnPhone />);
      const svg = screen.getByTestId("permission-chip").querySelector("svg") as SVGElement;
      const cls = svg.getAttribute("class") ?? "";
      cleanup();
      return cls.split(/\s+/).find((c) => c.startsWith("lucide-shield")) ?? "";
    };
    const shields = ["always_ask", "approve_for_me", "yolo"].map(icon);
    expect(shields.every(Boolean)).toBe(true);
    expect(new Set(shields).size).toBe(3);
  });

  it("the reasoning chip: a level keeps its word; other words give way to a brain, and the tooltip names them", () => {
    expect(reasoningShortLabel("high", "")).toBeNull();
    expect(reasoningShortLabel("", "medium")).toBeNull();
    expect(reasoningShortLabel("", "")).toBe("");
    expect(reasoningShortLabel("", "auto")).toBe("");
    expect(reasoningShortLabel("", "off")).toBe("Off");
    const { rerender } = render(
      <ReasoningChip levels={["low", "medium", "high"]} value="" def="auto" onChange={() => {}} />,
    );
    const words = screen.getByTestId("reasoning-chip-words");
    expect(words.className).toContain("group-data-[fit-short]/fit:hidden");
    // Never cut to a broken word ("Reaso…").
    expect(words.className).not.toMatch(/fit-short\]\/fit:max-w/);
    const short = screen.getByTestId("reasoning-chip-short");
    expect(short.className).toMatch(/(^|\s)hidden(\s|$)/);
    expect(short.className).toContain("group-data-[fit-short]/fit:inline-flex");
    expect(short.querySelector("svg")).not.toBeNull();
    const select = screen.getByTestId("reasoning-level");
    const plain = select.getAttribute("title") ?? "";
    rerender(<ReasoningChip levels={["low", "medium", "high"]} value="" def="auto" short onChange={() => {}} />);
    expect(screen.getByTestId("reasoning-level").getAttribute("title")).toBe(`Model decides: ${plain}`);
    // A level: its word stays at the short step, no brain, the tooltip as it was.
    rerender(<ReasoningChip levels={["low", "medium", "high"]} value="high" def="auto" short onChange={() => {}} />);
    expect(screen.getByTestId("reasoning-chip-words").className).not.toContain("fit-short");
    expect(screen.queryByTestId("reasoning-chip-short")).toBeNull();
    expect(screen.getByTestId("reasoning-level").getAttribute("title")).toBe(plain);
  });

  it("the model chip fills its wrapper, which is the item that shrinks (the floor is set on it)", () => {
    render(
      <ModelMenu
        open={false}
        onOpenChange={() => {}}
        choice="fleet-rtx::qwen"
        models={[]}
        trigger={{ text: "Qwen3.8-27B (RTX)", raw: "fleet-rtx · qwen", isDefault: false }}
        onPick={() => {}}
      />,
    );
    const chip = screen.getByTitle("Switch model");
    const wrap = chip.parentElement as HTMLElement;
    expect(wrap.hasAttribute("data-fit-model")).toBe(true);
    expect(wrap.className).toContain("min-w-0");
    expect(chip.className).toContain("w-full");
    expect(chip.className).toContain("max-w-[11rem]");
    expect(chip.className).toContain("sm:max-w-[16rem]");
    expect(screen.getByTestId("model-chip-words").getAttribute("title")).toBe("fleet-rtx · qwen");
  });
});

describe("the chat page's toolbar", () => {
  const page = () => read("app", "chat", "page.tsx");
  const toolbar = () => {
    const src = page();
    const at = src.indexOf('data-testid="composer-toolbar"');
    expect(at).toBeGreaterThan(-1);
    const end = src.indexOf("<VoiceAutoSend", at);
    expect(end).toBeGreaterThan(at);
    return src.slice(at - 400, end);
  };

  it("is one fit group in the chat's order: one row while it fits, the right group shrinks instead of wrapping", () => {
    const src = toolbar();
    expect(page()).toContain("useToolbarFit(CHAT_FIT_ORDER)");
    expect(src).toContain("ref={toolbarFitRef}");
    expect(src).toMatch(/group\/fit flex flex-wrap items-center[^"]*data-\[fit-row\]:flex-nowrap/);
    expect(src).toContain("group-data-[fit-row]/fit:shrink-0 group-data-[fit-row]/fit:flex-nowrap");
    // The right group: never `shrink-0` again — the model chip inside it gives way.
    expect(src).toMatch(/className="ml-auto flex min-w-0 items-center gap-1 group-data-\[fit-row\]\/fit:pl-3"/);
    expect(src).not.toMatch(/className="ml-auto flex shrink-0 items-center gap-1"/);
  });

  it("every left chip drops its words at ITS OWN step and keeps its name", () => {
    const src = toolbar();
    expect(src).toContain('${fitWords("web")}');
    expect(src).toContain('${fitIconChip("web")}');
    expect(src).toContain('${fitWords("tools")}');
    expect(src).toContain('${fitIconChip("tools")}');
    expect(src).toContain('${fitWords("local")}');
    expect(src).toContain('iconOnly={fitOn("ask")}');
    const sw = page();
    const at = sw.indexOf("const projectSwitch = ");
    expect(sw.slice(at, at + 6000)).toContain('${fitWords("project")}');
    expect(src).toContain('aria-label="Local only"');
    expect(src).toContain("aria-label={`Tools: ${toolsChip.text}`}");
    expect(src).toContain('aria-label="Web research"');
    expect(src).toContain('id="chat-approval-mode"');
    expect(src).toContain('data-testid="local-only-chip"');
    expect(src).toContain('data-testid="composer-tools"');
    expect(src).toContain('data-testid="composer-web"');
  });

  it("'Local only' becomes the phone's 'Local', never a house alone (v1.356.0 G75)", () => {
    const src = toolbar();
    const at = src.indexOf('data-testid="local-only-chip"');
    const chip = src.slice(at, src.indexOf("</button>", at));
    expect(chip).not.toContain("fitIconChip(");
    expect(chip).toContain('<span className={`sm:hidden ${fitShows("local")}`}>Local</span>');
  });

  it("the tools chip keeps a count, or 'Off', where a wrench alone would say neither", () => {
    const src = toolbar();
    const at = src.indexOf('data-testid="composer-tools"');
    const chip = src.slice(at, src.indexOf("</button>", at));
    expect(chip).toContain('toolsChip.armed > 0 || !autoTools ? "" : ` ${fitIconChip("tools")}`');
    expect(chip).toContain('<span className={`sm:hidden ${fitShows("tools")}`}>{toolsChip.armed}</span>');
    expect(chip).toMatch(/toolsChip\.armed === 0 && !autoTools && \(\s*<span className=\{`hidden \$\{fitShows\("tools"\)\}`\}>Off<\/span>/);
  });

  it("the menus drawn inside the toolbar are not the row", () => {
    const src = toolbar();
    expect((src.match(/data-fit-ignore/g) ?? []).length).toBeGreaterThanOrEqual(2);
    const sw = page();
    const at = sw.indexOf("const projectSwitch = ");
    expect(sw.slice(at, at + 6000)).toContain("data-fit-ignore");
  });
});

describe("the IJ harness composer", () => {
  const src = () => read("components", "harness", "HarnessSession.tsx");

  it("is the same fit group in its own order, and its chips keep their names", () => {
    const s = src();
    expect(s).toContain("useToolbarFit(HARNESS_FIT_ORDER)");
    expect(s).toContain("ref={harnessFitRef}");
    expect(s).toContain('data-testid="harness-composer-toolbar"');
    expect(s).toMatch(/group\/fit flex min-w-0 flex-wrap items-center[^"]*data-\[fit-row\]:flex-nowrap/);
    expect(s).toContain("group-data-[fit-row]/fit:flex-nowrap");
    // Its right group ends at the box's edge (`justify-end`): on one row it
    // overflows toward its END instead, so a squeeze is measured as an
    // overflow and never drawn over the chips on the left (measureFit also
    // counts controls running over one another: either alone holds it).
    expect(s).toContain("group-data-[fit-row]/fit:justify-start");
    expect(s).toContain('iconOnly={harnessOn("ask")}');
    // The plan chip and the try-on chip had no aria-label: their words were
    // their name. Now they keep a name when the words go.
    expect(s).toMatch(/data-testid="harness-plan-mode"[\s\S]{0,400}aria-label=/);
    const tryOn = read("components", "harness", "HarnessTryOn.tsx");
    expect(tryOn).toMatch(/data-testid="harness-try-on"[\s\S]{0,400}aria-label=/);
    expect(tryOn).toContain('fitWords("tryon")');
  });

  it("'Planning' (on) keeps its word; models picked to try on keep their count", () => {
    const s = src();
    expect(s).toContain('className={`${composerChipClass(planning)}${planning ? "" : ` ${fitIconChip("plan")}`}`}');
    expect(s).toContain('<span className={planning ? undefined : fitWords("plan")}>{words}</span>');
    const tryOn = read("components", "harness", "HarnessTryOn.tsx");
    expect(tryOn).toContain('${picks.length > 0 ? "" : ` ${fitIconChip("tryon")}`}');
    expect(tryOn).toContain('<span className={`hidden ${fitShows("tryon")}`}>{picks.length}</span>');
  });
});
