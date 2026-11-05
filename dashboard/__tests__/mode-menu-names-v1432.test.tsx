import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

/**
 * v1.432.0 (the user, after v1.430.0 made the chat's approvals menu
 * names-only: "Give Build › Code's mode menu the same names-only look" —
 * "Yes, do it here"): Build › Code's mode menu lists the modes by NAME; what a
 * mode does shows in the shared box (components/RowHint) beside its row after
 * a pause; a mode the tool does not offer is a greyed name after the offered
 * ones, picking nothing, its reason in the same box.
 *
 * Pinned here:
 *  - every offered row reads its name only; Full access reads its name only
 *    until it is armed, then "Press again…" (an instruction) stays printed;
 *  - each row keeps its meaning as its accessible description;
 *  - not before the pause; after it, the row's meaning; the next row's at once;
 *    leaving the menu hides it; closing the menu hides it;
 *  - a not-offered mode: greyed, not a menuitemradio, a click picks nothing,
 *    its reason on a pause;
 *  - Tab (focus) resting on a row shows it too;
 *  - a finger held on a row shows it and the lift is not a press (so a hold on
 *    Full access never arms it); a tap still works;
 *  - the shared placement keeps the box inside the window.
 */

import { ModeChip, FULL_ACCESS_ABOUT, hiddenModeWords } from "@/components/buildCode/ModeChip";
import { ROW_HINT_DELAY_MS, rowHintPlacement } from "@/components/RowHint";
import type { CodingTool } from "@/lib/buildApp";

beforeAll(() => {
  // jsdom has no PointerEvent: React reads `pointerType` off the native event.
  if (typeof window.PointerEvent === "undefined") {
    class FakePointerEvent extends MouseEvent {
      pointerType: string;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerType = init.pointerType ?? "mouse";
      }
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).PointerEvent = FakePointerEvent;
  }
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const CODEX: CodingTool = {
  id: "codex",
  label: "Codex",
  installed: true,
  ownSignIn: false,
  signedIn: true,
  version: "0.157.0",
  modes: [
    { id: "ask", label: "Ask first", dangerous: false, about: "Asks before it changes a file or runs a command." },
    { id: "auto", label: "Auto", dangerous: false, about: "Codex's own safety check allows routine steps." },
    { id: "bare", label: "Bare", dangerous: false },
    { id: "full", label: "Full access", dangerous: true, about: "Anything goes." },
  ],
  hiddenModes: [
    { id: "edits", label: "Auto edits", reason: "Codex cannot run commands in its sandbox on Windows." },
    { id: "plan", label: "Plan", reason: "Codex has no plan mode." },
  ],
  models: [],
  efforts: [],
  accounts: [],
  commands: [],
};

function setup() {
  const onChange = vi.fn();
  render(<ModeChip tool={CODEX} value="ask" onChange={onChange} />);
  fireEvent.click(screen.getByTestId("code-mode-chip-button"));
  const menu = screen.getByTestId("code-mode-chip-menu");
  const option = (id: string) => screen.getByTestId(`code-mode-chip-option-${id}`);
  const hiddenRow = (id: string) => screen.getByTestId(`code-mode-chip-hidden-${id}`);
  return { onChange, menu, option, hiddenRow };
}

const hint = () => screen.queryByTestId("code-mode-chip-hint");
const wait = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });
const describedBy = (el: HTMLElement) => document.getElementById(el.getAttribute("aria-describedby") ?? "");

describe("names only", () => {
  it("every offered mode reads its name, nothing else", () => {
    const { menu } = setup();
    expect(within(menu).getAllByRole("menuitemradio").map((b) => b.textContent)).toEqual([
      "Ask first",
      "Auto",
      "Bare",
      "Full access",
    ]);
  });

  it("each row keeps its meaning as its accessible description", () => {
    const { option, hiddenRow } = setup();
    expect(describedBy(option("ask"))?.textContent).toBe("Asks before it changes a file or runs a command.");
    expect(describedBy(option("auto"))?.textContent).toBe("Codex's own safety check allows routine steps.");
    // Full access says its own sentence, not the daemon's.
    expect(describedBy(option("full"))?.textContent).toBe(FULL_ACCESS_ABOUT);
    // A mode with nothing to say has no description (never an empty one).
    expect(option("bare").getAttribute("aria-describedby")).toBeNull();
    expect(describedBy(hiddenRow("edits"))?.textContent).toBe(
      "Auto edits is not offered here. Codex cannot run commands in its sandbox on Windows.",
    );
    expect(describedBy(option("ask"))?.hidden).toBe(true);
  });

  it("a mode not offered is a greyed name after the offered ones, and picks nothing", () => {
    const { menu, hiddenRow, onChange } = setup();
    const row = hiddenRow("edits");
    expect(row.textContent).toBe("Auto edits");
    expect(row.getAttribute("aria-disabled")).toBe("true");
    expect(row.getAttribute("role")).toBe("menuitem");
    expect(row.className).toContain("text-zinc-600");
    const order = Array.from(menu.querySelectorAll("[data-testid^='code-mode-chip-option-'],[data-testid^='code-mode-chip-hidden-']"))
      .filter((el) => !(el as HTMLElement).hidden)
      .map((el) => (el as HTMLElement).dataset.testid);
    expect(order).toEqual([
      "code-mode-chip-option-ask",
      "code-mode-chip-option-auto",
      "code-mode-chip-option-bare",
      "code-mode-chip-option-full",
      "code-mode-chip-hidden-edits",
      "code-mode-chip-hidden-plan",
    ]);
    fireEvent.click(row);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("code-mode-chip-menu")).toBeTruthy();
  });

  it("Full access: its name until armed, then the instruction stays printed", async () => {
    const { option } = setup();
    expect(option("full").textContent).toBe("Full access");
    fireEvent.click(option("full"));
    expect(option("full").dataset.armed).toBe("true");
    expect(option("full").textContent).toBe("Full accessPress again to let it do anything without asking.");
  });
});

describe("the meaning, after a pause", () => {
  it("not at once; then the row's meaning, beside the menu", () => {
    vi.useFakeTimers();
    const { option } = setup();
    fireEvent.mouseEnter(option("auto"));
    expect(hint()).toBeNull();
    wait(ROW_HINT_DELAY_MS - 1);
    expect(hint()).toBeNull();
    wait(1);
    expect(hint()!.dataset.mode).toBe("auto");
    expect(hint()!.textContent).toBe("Codex's own safety check allows routine steps.");
    expect(hint()!.getAttribute("aria-hidden")).toBe("true");
    expect(hint()!.className).toContain("pointer-events-none");
  });

  it("the next row's shows at once; a not-offered row says why", () => {
    vi.useFakeTimers();
    const { option, hiddenRow } = setup();
    fireEvent.mouseEnter(option("ask"));
    wait(ROW_HINT_DELAY_MS);
    fireEvent.mouseEnter(hiddenRow("plan"));
    expect(hint()!.textContent).toBe(hiddenModeWords(CODEX.hiddenModes[1]));
    fireEvent.mouseEnter(option("full"));
    expect(hint()!.textContent).toBe(FULL_ACCESS_ABOUT);
  });

  it("a mode with nothing to say shows no box", () => {
    vi.useFakeTimers();
    const { option } = setup();
    fireEvent.mouseEnter(option("bare"));
    wait(ROW_HINT_DELAY_MS * 2);
    expect(hint()).toBeNull();
  });

  it("leaving the menu hides it; closing it hides it, and a reopened menu starts empty", () => {
    vi.useFakeTimers();
    const { option, menu } = setup();
    fireEvent.mouseEnter(option("auto"));
    wait(ROW_HINT_DELAY_MS);
    fireEvent.mouseLeave(menu);
    expect(hint()).toBeNull();
    fireEvent.mouseEnter(option("ask"));
    wait(ROW_HINT_DELAY_MS);
    expect(hint()).not.toBeNull();
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByTestId("code-mode-chip-menu")).toBeNull();
    expect(hint()).toBeNull();
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    expect(hint()).toBeNull();
  });

  it("focus resting on a row shows it too", () => {
    vi.useFakeTimers();
    const { hiddenRow } = setup();
    fireEvent.focus(hiddenRow("edits"));
    expect(hint()).toBeNull();
    wait(ROW_HINT_DELAY_MS);
    expect(hint()!.textContent).toContain("Auto edits is not offered here.");
  });

  it("a click still picks", () => {
    vi.useFakeTimers();
    const { option, onChange } = setup();
    fireEvent.mouseEnter(option("auto"));
    wait(ROW_HINT_DELAY_MS);
    fireEvent.click(option("auto"));
    expect(onChange).toHaveBeenCalledWith("auto", false);
    expect(hint()).toBeNull();
  });
});

describe("a touch screen", () => {
  it("a held finger shows the meaning, and the lift never arms Full access", () => {
    vi.useFakeTimers();
    const { option, onChange } = setup();
    const full = option("full");
    fireEvent.pointerDown(full, { pointerType: "touch" });
    wait(ROW_HINT_DELAY_MS);
    expect(hint()!.textContent).toBe(FULL_ACCESS_ABOUT);
    fireEvent.pointerUp(full, { pointerType: "touch" });
    fireEvent.click(full);
    expect(option("full").dataset.armed).toBeUndefined();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("a tap still picks", () => {
    vi.useFakeTimers();
    const { option, onChange } = setup();
    const auto = option("auto");
    fireEvent.pointerDown(auto, { pointerType: "touch" });
    wait(80);
    fireEvent.pointerUp(auto, { pointerType: "touch" });
    wait(ROW_HINT_DELAY_MS * 2);
    expect(hint()).toBeNull();
    fireEvent.click(auto);
    expect(onChange).toHaveBeenCalledWith("auto", false);
  });
});

describe("the shared placement", () => {
  const box = (left: number, top: number, w: number, h: number) => ({ left, top, right: left + w, bottom: top + h });
  it("beside the menu on a desktop, under the row on a 360px phone", () => {
    const size = { width: 256, height: 70 };
    expect(rowHintPlacement(box(300, 500, 224, 200), box(304, 520, 216, 30), size, { width: 1280, height: 800 }).side).toBe("right");
    const phone = rowHintPlacement(box(20, 500, 224, 200), box(24, 520, 216, 30), size, { width: 360, height: 780 });
    expect(phone.side).toBe("under");
    expect(phone.left).toBeGreaterThanOrEqual(8);
    expect(phone.left + 256).toBeLessThanOrEqual(360 - 8);
  });
});
