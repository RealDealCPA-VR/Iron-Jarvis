/**
 * v1.400.0 — the composer's toolbar is ONE ROW whatever model is picked.
 *
 * The user's report (2026-10-27): "it looks clean until the model with the
 * longer name is selected and then things start to look a bit off with
 * spacing". The layout itself can only be seen in a real browser
 * (e2e/composer-one-row-v1400.spec.ts); pinned here:
 *
 *  - the stage decision (lib/toolbarFit `nextFitStage`): an overflow records
 *    what the stage needs and moves on; a stage is only left for the one
 *    before it with `FIT_SLACK_PX` more room than that stage needed, so a box
 *    jittering by a few pixels around the boundary never flickers (with no
 *    slack, the same jitter flips the row on every step);
 *  - the measuring helpers: what counts as a control (a menu drawn inside the
 *    toolbar does not; the reasoning select is its chip), what counts as a
 *    change of words (the signature), the model chip's floor (never wider
 *    than its name needs, never squeezing the name under the minimum);
 *  - the chips: each keeps its aria-label, id and data-testid when it drops
 *    its words; an icon-only chip's tooltip leads with the words it no
 *    longer shows; the permission chip's amber (the no-ask level) and the
 *    Local only glow are untouched; the model chip shrinks inside its
 *    wrapper (the item flexbox shrinks).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import {
  FIT_FULL,
  FIT_ICONS,
  FIT_SLACK_PX,
  FIT_TIGHT,
  FIT_WRAP,
  MODEL_LABEL_MIN_PX,
  applyFit,
  fitControls,
  fitSignature,
  measureFit,
  nextFitStage,
  syncModelFloor,
  type FitStage,
} from "@/lib/toolbarFit";
import { FIT_ICON_CHIP, FIT_WORDS, iconOnlyTitle } from "@/lib/composerChips";
import { PermissionChip } from "@/components/chat/PermissionChip";
import { ModelMenu, ReasoningChip } from "@/components/chat/ModelMenu";

const read = (...parts: string[]) =>
  readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

/** A toolbar whose stages need these widths: settles the way the hook does
 *  (measure, decide, until the stage holds) for one box width. */
function settleAt(
  start: FitStage,
  needed0: (number | undefined)[],
  available: number,
  needs: number[],
  slack = FIT_SLACK_PX,
): { stage: FitStage; needed: (number | undefined)[] } {
  let stage = start;
  let needed = needed0;
  for (let i = 0; i < 8; i++) {
    const overflow = stage === FIT_WRAP ? -1 : needs[stage] - available;
    const next = nextFitStage(stage, { available, overflow }, needed, slack);
    needed = next.needed;
    if (next.stage === stage) break;
    stage = next.stage;
  }
  return { stage, needed };
}

describe("the stage decision (nextFitStage)", () => {
  it("an overflow records what the stage needs and moves to the next stage", () => {
    const d = nextFitStage(FIT_FULL, { available: 700, overflow: 37 }, []);
    expect(d.stage).toBe(FIT_ICONS);
    expect(d.needed[FIT_FULL]).toBe(737);
    expect(nextFitStage(FIT_ICONS, { available: 500, overflow: 4 }, d.needed).stage).toBe(FIT_TIGHT);
    expect(nextFitStage(FIT_TIGHT, { available: 400, overflow: 4 }, d.needed).stage).toBe(FIT_WRAP);
  });

  it("sub-pixel layout is not an overflow", () => {
    expect(nextFitStage(FIT_FULL, { available: 700, overflow: 0.4 }, []).stage).toBe(FIT_FULL);
  });

  it("the wrapping row is the last resort: it never steps further", () => {
    expect(nextFitStage(FIT_WRAP, { available: 300, overflow: 50 }, [800, 700, 600]).stage).toBe(FIT_WRAP);
  });

  it("goes back one stage only with FIT_SLACK_PX more room than that stage needed", () => {
    const needed = [737];
    expect(nextFitStage(FIT_ICONS, { available: 737, overflow: -100 }, needed).stage).toBe(FIT_ICONS);
    expect(nextFitStage(FIT_ICONS, { available: 737 + FIT_SLACK_PX - 1, overflow: -100 }, needed).stage).toBe(
      FIT_ICONS,
    );
    expect(nextFitStage(FIT_ICONS, { available: 737 + FIT_SLACK_PX, overflow: -100 }, needed).stage).toBe(FIT_FULL);
    // Nothing known about the stage before: stay.
    expect(nextFitStage(FIT_ICONS, { available: 2000, overflow: -1000 }, []).stage).toBe(FIT_ICONS);
  });

  it("settles in one go: a box far too narrow walks every stage, a wide one goes back to FULL", () => {
    const needs = [740, 560, 520];
    const narrow = settleAt(FIT_FULL, [], 480, needs);
    expect(narrow.stage).toBe(FIT_WRAP);
    expect(narrow.needed).toEqual([740, 560, 520]);
    expect(settleAt(FIT_WRAP, narrow.needed, 900, needs).stage).toBe(FIT_FULL);
    expect(settleAt(FIT_WRAP, narrow.needed, 600, needs).stage).toBe(FIT_ICONS);
  });

  it("A FEW PIXELS OF JITTER AROUND THE BOUNDARY NEVER FLICKERS (and with no slack it does)", () => {
    // A row that needs 740 px with its words and 560 px without them. The box
    // goes back and forth by a few pixels around 740 (a window being sized,
    // a scrollbar that comes and goes with the row).
    const needs = [740, 560, 520];
    const widths = [745, 738, 742, 737, 744, 739, 746, 736, 750, 741];
    const flips = (slack: number) => {
      let s: { stage: FitStage; needed: (number | undefined)[] } = { stage: FIT_FULL, needed: [] };
      let changes = 0;
      for (const w of widths) {
        const next = settleAt(s.stage, s.needed, w, needs, slack);
        if (next.stage !== s.stage) changes += 1;
        s = next;
      }
      return changes;
    };
    // With the slack: it drops the words once and keeps them dropped.
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
  function bar() {
    document.body.innerHTML = `
      <div id="bar" style="padding-left:8px;padding-right:8px">
        <div id="left">
          <div><button id="plus">+</button>
            <div data-fit-ignore><button id="menu-row">Attach files</button></div></div>
          <button id="local" aria-label="Local only"><span>Local only</span></button>
        </div>
        <div id="right">
          <span id="reasoning"><span>Reasoning</span><select aria-label="Reasoning level"><option>Default</option></select></span>
          <div data-fit-model><button id="model"><span data-testid="model-chip-words">Qwen3.8-27B (RTX)</span></button></div>
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

  it("the model chip's floor: its chrome plus the name up to the minimum, never more than the name needs", () => {
    const b = bar();
    const wrap = b.querySelector("[data-fit-model]") as HTMLElement;
    const chip = document.getElementById("model") as HTMLElement;
    const label = b.querySelector('[data-testid="model-chip-words"]') as HTMLElement;
    // A long name, squeezed: chrome 34 px + the minimum.
    box(chip, { left: 0, right: 90 });
    box(label, { left: 8, right: 64 });
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 140 });
    syncModelFloor(b, true);
    expect(wrap.style.minWidth).toBe(`${34 + MODEL_LABEL_MIN_PX}px`);
    // A short name: the floor is the chip it already is.
    box(chip, { left: 0, right: 74 });
    box(label, { left: 8, right: 48 });
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 40 });
    syncModelFloor(b, true);
    expect(wrap.style.minWidth).toBe("74px");
    // Off (a phone, the wrapping row): no floor.
    syncModelFloor(b, false);
    expect(wrap.style.minWidth).toBe("");
  });

  it("a stage is three attributes; none is the old wrapping row", () => {
    const b = bar();
    applyFit(b, FIT_FULL);
    expect([b.hasAttribute("data-fit-row"), b.hasAttribute("data-fit-icons"), b.hasAttribute("data-fit-short")]).toEqual(
      [true, false, false],
    );
    applyFit(b, FIT_ICONS);
    expect([b.hasAttribute("data-fit-row"), b.hasAttribute("data-fit-icons"), b.hasAttribute("data-fit-short")]).toEqual(
      [true, true, false],
    );
    applyFit(b, FIT_TIGHT);
    expect([b.hasAttribute("data-fit-row"), b.hasAttribute("data-fit-icons"), b.hasAttribute("data-fit-short")]).toEqual(
      [true, true, true],
    );
    applyFit(b, FIT_WRAP);
    expect([b.hasAttribute("data-fit-row"), b.hasAttribute("data-fit-icons"), b.hasAttribute("data-fit-short")]).toEqual(
      [false, true, true],
    );
    applyFit(b, null);
    expect([b.hasAttribute("data-fit-row"), b.hasAttribute("data-fit-icons"), b.hasAttribute("data-fit-short")]).toEqual(
      [false, false, false],
    );
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

  it("the classes that drop the words are the toolbar's own stage, and whole pixels", () => {
    expect(FIT_WORDS).toBe("group-data-[fit-icons]/fit:hidden");
    expect(FIT_ICON_CHIP).toContain("group-data-[fit-icons]/fit:w-[30px]");
    expect(`${FIT_WORDS} ${FIT_ICON_CHIP}`).not.toMatch(/\.5px|\[\d+\.\d+px\]/);
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
    expect(chip.className).toContain(FIT_ICON_CHIP);
    const plainTitle = chip.getAttribute("title") ?? "";
    expect(plainTitle.startsWith(words)).toBe(false);
    rerender(<PermissionChip id="chat-approval-mode" value="yolo" onChange={() => {}} iconOnlyOnPhone iconOnly />);
    chip = screen.getByTestId("permission-chip");
    expect(chip.getAttribute("aria-label")).toBe(name);
    expect(chip.getAttribute("title")).toBe(`${words}: ${plainTitle}`);
    expect(chip.getAttribute("data-tone")).toBe("warn");
    // Its words and chevron are the ones the toolbar's stage hides.
    const label = Array.from(chip.querySelectorAll("span")).find((s) => s.textContent === words);
    expect(label?.className).toContain(FIT_WORDS);
  });

  it("the reasoning chip shortens its words last, and its tooltip then names them", () => {
    const { rerender } = render(
      <ReasoningChip levels={["low", "medium", "high"]} value="" def="auto" onChange={() => {}} />,
    );
    const words = screen.getByTestId("reasoning-chip-words");
    expect(words.className).toContain("group-data-[fit-short]/fit:max-w-[3.5rem]");
    expect(words.className).not.toContain("fit-icons");
    const select = screen.getByTestId("reasoning-level");
    const plain = select.getAttribute("title") ?? "";
    rerender(<ReasoningChip levels={["low", "medium", "high"]} value="" def="auto" short onChange={() => {}} />);
    expect(screen.getByTestId("reasoning-level").getAttribute("title")).toBe(`Model decides: ${plain}`);
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

  it("is one fit group: one row while it fits, the right group shrinks instead of wrapping", () => {
    const src = toolbar();
    expect(src).toContain("ref={toolbarFitRef}");
    expect(src).toMatch(/group\/fit flex flex-wrap items-center[^"]*data-\[fit-row\]:flex-nowrap/);
    expect(src).toContain("group-data-[fit-row]/fit:shrink-0 group-data-[fit-row]/fit:flex-nowrap");
    // The right group: never `shrink-0` again — the model chip inside it gives way.
    expect(src).toMatch(/className="ml-auto flex min-w-0 items-center gap-1 group-data-\[fit-row\]\/fit:pl-3"/);
    expect(src).not.toMatch(/className="ml-auto flex shrink-0 items-center gap-1"/);
  });

  it("every left chip drops its words with the toolbar's stage and keeps its name", () => {
    const src = toolbar();
    // Local only, the tools chip and web here; the project chip in projectSwitch.
    expect((src.match(/\$\{FIT_WORDS\}/g) ?? []).length).toBeGreaterThanOrEqual(3);
    const sw = page();
    const at = sw.indexOf("const projectSwitch = ");
    expect(sw.slice(at, at + 6000)).toContain("${FIT_WORDS}");
    expect(src).toContain('aria-label="Local only"');
    expect(src).toContain("aria-label={`Tools: ${toolsChip.text}`}");
    expect(src).toContain('aria-label="Web research"');
    expect(src).toContain('id="chat-approval-mode"');
    expect(src).toContain("iconOnly={fitIcons}");
    expect(src).toContain('data-testid="local-only-chip"');
    expect(src).toContain('data-testid="composer-tools"');
    expect(src).toContain('data-testid="composer-web"');
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

  it("is the same fit group, and its chips keep their names", () => {
    const s = src();
    expect(s).toContain("ref={harnessFitRef}");
    expect(s).toContain('data-testid="harness-composer-toolbar"');
    expect(s).toMatch(/group\/fit flex min-w-0 flex-wrap items-center[^"]*data-\[fit-row\]:flex-nowrap/);
    expect(s).toContain("group-data-[fit-row]/fit:flex-nowrap");
    expect(s).toContain("iconOnly={harnessIcons}");
    // The plan chip and the try-on chip had no aria-label: their words were
    // their name. Now they keep a name when the words go.
    expect(s).toMatch(/data-testid="harness-plan-mode"[\s\S]{0,400}aria-label=/);
    const tryOn = read("components", "harness", "HarnessTryOn.tsx");
    expect(tryOn).toMatch(/data-testid="harness-try-on"[\s\S]{0,400}aria-label=/);
    expect(tryOn).toContain("FIT_WORDS");
  });
});
