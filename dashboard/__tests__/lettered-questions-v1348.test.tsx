/**
 * v1.348.0, polish wave A, lane L2: an app's question with one fixed set of
 * choices reads A / B / C (the idea from 21st.dev agent-elements' question
 * card, MIT, no code taken).
 *
 *  - An MCP elicitation with exactly ONE choice field (at most
 *    MAX_LETTERED options) draws the choices as lettered rows (a radio
 *    group), not a menu; a form with several choice fields keeps its menus.
 *  - The matching letter key picks a row, only from INSIDE the dock card,
 *    never from a field that takes typing, never in the first moment after
 *    the question appears (DOCK_KEY_ARM_MS), and never sends: Enter (or Send)
 *    does that, through the card's own checks.
 *  - The question card's "Not now" is a quiet "Skip", the SAME cancel answer
 *    (outcome "Skipped for now"), no new call.
 *
 * Driven through the REAL ElicitationCard and DockAsk with the real helpers.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import { DockAsk } from "@/components/chat/DockAsk";
import { ElicitationCard } from "@/components/chat/ElicitationCard";
import {
  CHOICE_GROUP_ATTR,
  CHOICE_LETTER_ATTR,
  choiceKey,
  choiceLetter,
  letteredField,
  MAX_LETTERED,
} from "@/lib/letteredChoices";
import type { McpElicitationAsk, McpField } from "@/lib/mcpInteract";

const CHOICE: McpField = {
  name: "target",
  type: "string",
  title: "Where should it deploy?",
  description: "",
  required: true,
  enum: ["staging", "production", "preview"],
  enum_names: ["Staging", "Production", "A preview link"],
};
const NOTE: McpField = { name: "note", type: "string", title: "Note", description: "", required: false };

function ask(fields: McpField[] = [CHOICE], over: Partial<McpElicitationAsk> = {}): McpElicitationAsk {
  return { kind: "elicitation", id: "e1", callId: "c1", pack: "Deployer", message: "Pick one.", fields, ...over };
}

function renderDock(a: McpElicitationAsk, over: Partial<Parameters<typeof DockAsk>[0]> = {}) {
  const props = {
    onAnswerElicitation: vi.fn(async () => ({ ok: true }) as const),
    onDecideSampling: vi.fn(async () => true),
    armDelayMs: 0,
    ...over,
  };
  render(<DockAsk asks={[{ kind: "elicitation", id: a.id, ask: a }]} {...props} />);
  return props;
}

const row = (letter: string) => screen.getByTestId(`mcp-choice-target-${letter}`);
const checked = () =>
  Array.from(document.querySelectorAll('[role="radio"]'))
    .filter((r) => r.getAttribute("aria-checked") === "true")
    .map((r) => r.getAttribute("data-choice-letter"));

beforeEach(() => {
  H.post.mockReset();
  H.post.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  (document.activeElement as HTMLElement | null)?.blur?.();
});

/* ------------------------------------------------------------ the rows */

describe("one choice field reads A / B / C", () => {
  it("draws a labelled radio group of lettered rows instead of a menu; the letters are the shortcuts", () => {
    render(<ElicitationCard ask={ask()} onAnswer={vi.fn()} />);
    expect(document.querySelector("select")).toBeNull();
    const group = screen.getByRole("radiogroup", { name: /Where should it deploy\?/ });
    expect(group.getAttribute("aria-required")).toBe("true");
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.getAttribute("data-choice-letter"))).toEqual(["A", "B", "C"]);
    expect(radios.map((r) => r.getAttribute("aria-keyshortcuts"))).toEqual(["A", "B", "C"]);
    // The accessible name is the choice's words; the letter badge is for the eye.
    expect(screen.getByRole("radio", { name: "Production" })).toBe(row("B"));
    expect(row("B").textContent).toBe("BProduction");
    // One tab stop: the first row until something is picked.
    expect(radios.map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    // The rows carry exactly the attributes the dock looks for.
    expect(group.hasAttribute(CHOICE_GROUP_ATTR)).toBe(true);
    expect(row("A").getAttribute(CHOICE_LETTER_ATTR)).toBe("A");
  });

  it("a click picks; Send sends the picked choice's VALUE through the card's checks", async () => {
    const onAnswer = vi.fn(async () => ({ ok: true }));
    render(<ElicitationCard ask={ask()} onAnswer={onAnswer} />);
    // Nothing picked: the required check stops the send and marks the group.
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.getByTestId("mcp-field-target").textContent).toContain("Please pick one.");
    expect(screen.getByRole("radiogroup").getAttribute("aria-invalid")).toBe("true");
    fireEvent.click(row("B"));
    expect(checked()).toEqual(["B"]);
    expect(screen.getAllByRole("radio").map((r) => r.tabIndex)).toEqual([-1, 0, -1]);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith("accept", { target: "production" }));
  });

  it("arrow keys move and pick (wrapping), Space picks, Home/End jump", () => {
    render(<ElicitationCard ask={ask()} onAnswer={vi.fn()} />);
    row("A").focus();
    fireEvent.keyDown(row("A"), { key: "ArrowDown" });
    expect(checked()).toEqual(["B"]);
    expect(document.activeElement).toBe(row("B"));
    fireEvent.keyDown(row("B"), { key: "ArrowUp" });
    fireEvent.keyDown(row("A"), { key: "ArrowUp" });
    expect(checked()).toEqual(["C"]);
    fireEvent.keyDown(row("C"), { key: "Home" });
    expect(checked()).toEqual(["A"]);
    fireEvent.keyDown(row("A"), { key: "End" });
    expect(checked()).toEqual(["C"]);
    fireEvent.keyDown(row("B"), { key: " " });
    expect(checked()).toEqual(["B"]);
  });

  it("a default picks its row; an answered card disables every row", () => {
    render(
      <ElicitationCard ask={ask([{ ...CHOICE, default: "preview" }], { outcome: "accept" })} onAnswer={vi.fn()} />,
    );
    expect(checked()).toEqual(["C"]);
    for (const r of screen.getAllByRole("radio")) {
      expect(r.getAttribute("aria-disabled")).toBe("true");
      expect(r.tabIndex).toBe(-1);
    }
    fireEvent.click(row("A"));
    expect(checked()).toEqual(["C"]);
  });

  it("CONTROL: a form with two choice fields keeps its menus (and 'Not now')", () => {
    render(
      <ElicitationCard
        ask={ask([CHOICE, { ...CHOICE, name: "region", title: "Region", enum: ["us", "eu"], enum_names: undefined }])}
        onAnswer={vi.fn()}
      />,
    );
    expect(document.querySelectorAll("select")).toHaveLength(2);
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Not now" })).toBeTruthy();
    expect(screen.queryByTestId("mcp-elicitation-skip")).toBeNull();
  });
});

/* ------------------------------------------------------------ Skip */

describe("the question card offers a quiet Skip on the existing cancel path", () => {
  it("Skip answers 'cancel' with no content and says what happened; Send and Decline stay", async () => {
    const onAnswer = vi.fn(async () => ({ ok: true }));
    render(<ElicitationCard ask={ask()} onAnswer={onAnswer} />);
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Decline" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
    const skip = screen.getByRole("button", { name: "Skip" });
    expect(skip.getAttribute("data-testid")).toBe("mcp-elicitation-skip");
    expect(skip.getAttribute("type")).toBe("button");
    fireEvent.click(skip);
    await waitFor(() =>
      expect(screen.getByTestId("mcp-elicitation-outcome").textContent).toBe(
        "Skipped for now. Deployer was told you did not answer.",
      ),
    );
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(onAnswer).toHaveBeenCalledWith("cancel", undefined);
  });
});

/* ------------------------------------------------------------ letter keys in the dock */

describe("in the dock, the letter key picks (and never sends)", () => {
  it("'b' on the card picks B and moves focus there; Enter then sends it", async () => {
    const props = renderDock(ask());
    const dock = screen.getByTestId("dock-ask");
    expect(document.activeElement).toBe(dock);
    fireEvent.keyDown(dock, { key: "b" });
    expect(checked()).toEqual(["B"]);
    expect(document.activeElement).toBe(row("B"));
    // A letter alone never answers, and never even tries (a try would run the
    // card's checks and put up an alert).
    await new Promise((r) => setTimeout(r, 20));
    expect(props.onAnswerElicitation).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
    // Enter from the picked row is the dock's: it presses Send.
    fireEvent.keyDown(row("B"), { key: "Enter" });
    await waitFor(() =>
      expect(props.onAnswerElicitation).toHaveBeenCalledWith("e1", "accept", { target: "production" }),
    );
  });

  it("with a choice already picked (a default), a letter still only re-picks; nothing is sent", async () => {
    const props = renderDock(ask([{ ...CHOICE, default: "staging" }]));
    expect(checked()).toEqual(["A"]);
    fireEvent.keyDown(screen.getByTestId("dock-ask"), { key: "c" });
    expect(checked()).toEqual(["C"]);
    await new Promise((r) => setTimeout(r, 20));
    expect(props.onAnswerElicitation).not.toHaveBeenCalled();
  });

  it("upper case works too; a letter with no row, or with Ctrl/Alt/Meta, does nothing", () => {
    renderDock(ask());
    const dock = screen.getByTestId("dock-ask");
    fireEvent.keyDown(dock, { key: "C" });
    expect(checked()).toEqual(["C"]);
    fireEvent.keyDown(dock, { key: "z" });
    fireEvent.keyDown(dock, { key: "a", ctrlKey: true });
    fireEvent.keyDown(dock, { key: "a", altKey: true });
    fireEvent.keyDown(dock, { key: "a", metaKey: true });
    fireEvent.keyDown(dock, { key: "a", repeat: true });
    expect(checked()).toEqual(["C"]);
  });

  it("a letter typed in a text field is a letter, never a pick", () => {
    renderDock(ask([CHOICE, NOTE]));
    const note = screen.getByLabelText(/^Note/) as HTMLInputElement;
    note.focus();
    fireEvent.keyDown(note, { key: "a" });
    expect(checked()).toEqual([]);
    // CONTROL: the same key from the card itself does pick.
    fireEvent.keyDown(screen.getByTestId("dock-ask"), { key: "a" });
    expect(checked()).toEqual(["A"]);
  });

  it("a letter in the first moment after the question appears is ignored (DOCK_KEY_ARM_MS)", () => {
    let now = 2_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    renderDock(ask(), { armDelayMs: 400 });
    const dock = screen.getByTestId("dock-ask");
    now += 100;
    fireEvent.keyDown(dock, { key: "a" });
    expect(checked()).toEqual([]);
    now += 400;
    fireEvent.keyDown(dock, { key: "a" });
    expect(checked()).toEqual(["A"]);
  });

  it("only from inside the card: a letter with the page body focused picks nothing", () => {
    renderDock(ask());
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "a" });
    expect(checked()).toEqual([]);
  });

  it("Esc on the card still declines; the letter rows change nothing about it", async () => {
    const props = renderDock(ask());
    fireEvent.keyDown(screen.getByTestId("mcp-elicitation-card"), { key: "Escape" });
    await waitFor(() => expect(props.onAnswerElicitation).toHaveBeenCalledWith("e1", "decline", undefined));
  });

  it("an answered card's rows ignore the letter key", () => {
    renderDock(ask([CHOICE], { outcome: "decline" }));
    fireEvent.keyDown(screen.getByTestId("dock-ask"), { key: "a" });
    expect(checked()).toEqual([]);
  });
});

/* ------------------------------------------------------------ pure helpers */

describe("lib/letteredChoices (pure)", () => {
  it("letteredField: exactly one choice field of at most MAX_LETTERED options", () => {
    expect(letteredField([CHOICE])).toBe("target");
    expect(letteredField([CHOICE, NOTE])).toBe("target");
    expect(letteredField([NOTE])).toBeNull();
    expect(letteredField([CHOICE, { ...CHOICE, name: "b" }])).toBeNull();
    const many = Array.from({ length: MAX_LETTERED + 1 }, (_, i) => `o${i}`);
    expect(letteredField([{ ...CHOICE, enum: many, enum_names: undefined }])).toBeNull();
    expect(letteredField([{ ...CHOICE, enum: many.slice(0, MAX_LETTERED), enum_names: undefined }])).toBe("target");
    expect(letteredField([{ ...NOTE, type: "boolean" }])).toBeNull();
  });

  it("choiceLetter and choiceKey", () => {
    expect([0, 1, 9].map(choiceLetter)).toEqual(["A", "B", "J"]);
    const k = (key: string, over: Partial<KeyboardEvent> = {}) =>
      choiceKey({ key, ctrlKey: false, altKey: false, metaKey: false, repeat: false, isComposing: false, ...over }, null);
    expect(k("b")).toBe("B");
    expect(k("B")).toBe("B");
    expect(k("Enter")).toBeNull();
    expect(k("1")).toBeNull();
    expect(k("b", { isComposing: true })).toBeNull();
    const input = document.createElement("input");
    expect(choiceKey({ key: "b", ctrlKey: false, altKey: false, metaKey: false, repeat: false, isComposing: false }, input)).toBeNull();
  });
});
