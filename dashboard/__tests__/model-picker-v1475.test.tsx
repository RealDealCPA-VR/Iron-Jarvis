/**
 * v1.475.0: Build › Code's ONE model picker — a chip "tool · model · effort"
 * opening one panel: the tools on the left (one that cannot start greyed,
 * with its reason), the tool's models with a search box, and the chosen
 * model's own effort levels. Picking a model moves straight to its levels;
 * the keyboard does it all; Ctrl+Shift+M opens it.
 */
import React, { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  ModelPicker,
  aliasTarget,
  isPickerShortcut,
  matchesModel,
  pickerWords,
  type PickerChoice,
  type PickerControl,
  type PickerField,
} from "@/components/buildCode/ModelPicker";
import { decodeCodingTools } from "@/lib/buildApp";
import { LAST_PICK_KEY, lastPick } from "@/components/buildCode/StartComposer";

const TOOLS = decodeCodingTools({
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first" }],
      models: [
        { id: "claude-opus-5-5", label: "Opus 5.5", efforts: ["low", "medium", "high"] },
        { id: "claude-sonnet-5-5", label: "Sonnet 5.5", efforts: ["low", "high"] },
        { id: "claude-fable-5-1", label: "Fable 5.1", efforts: [] },
      ],
      efforts: ["low", "medium", "high"],
    },
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first" }],
      models: [{ id: "gpt-6-astra", label: "GPT-6 Astra" }],
      efforts: [],
    },
    {
      id: "grok",
      label: "Grok",
      installed: true,
      signed_in: false,
      modes: [{ id: "ask", label: "Ask first" }],
      models: [],
      efforts: [],
    },
  ],
});

function Harness({ start, lock = false, onOpen, extra }: { start: PickerChoice; lock?: boolean; onOpen?: () => void; extra?: boolean }) {
  const [v, setV] = useState(start);
  return (
    <>
      <ModelPicker
        tools={TOOLS}
        value={v}
        lockTool={lock}
        onOpen={onOpen}
        shortcut
        testId="p"
        toolExtra={extra ? (t) => <span data-testid={`signin-${t.id}`}>Sign in</span> : undefined}
        onChange={(field, next) =>
          setV((cur) => (field === "tool" ? { tool: next, model: "", effort: "" } : { ...cur, [field]: next }))
        }
      />
      <output data-testid="value">{JSON.stringify(v)}</output>
    </>
  );
}

const value = () => JSON.parse(screen.getByTestId("value").textContent || "{}") as PickerChoice;
const frame = () => act(async () => new Promise<void>((r) => requestAnimationFrame(() => r())));

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("the chip", () => {
  it("names the tool, the model and the effort in one line", () => {
    const tool = TOOLS.find((t) => t.id === "claude-code")!;
    expect(pickerWords(tool, { tool: "claude-code", model: "claude-opus-5-5", effort: "high" })).toBe(
      "Claude Code · Opus 5.5 · High",
    );
    expect(pickerWords(tool, { tool: "claude-code", model: "", effort: "" })).toBe("Claude Code · Default model");
    // A model the tool no longer lists stays named (never swapped silently).
    expect(pickerWords(tool, { tool: "claude-code", model: "claude-gone", effort: "" })).toBe("Claude Code · claude-gone");
    render(<Harness start={{ tool: "claude-code", model: "claude-opus-5-5", effort: "high" }} />);
    const chip = screen.getByTestId("p-tool");
    expect(chip.textContent).toBe("Claude Code · Opus 5.5 · High");
    expect(chip.dataset).toMatchObject({ tool: "claude-code", model: "claude-opus-5-5", effort: "high" });
    expect(chip.getAttribute("title")).toContain("Ctrl+Shift+M");
  });
});

describe("the panel", () => {
  it("lists every tool; one that cannot start is greyed with its reason and the page's press", () => {
    render(<Harness start={{ tool: "claude-code", model: "", effort: "" }} extra />);
    fireEvent.click(screen.getByTestId("p-tool"));
    const grok = screen.getByTestId("p-tool-grok") as HTMLButtonElement;
    expect(grok.disabled).toBe(true);
    expect(grok.dataset.available).toBe("false");
    expect(screen.getByTestId("p-tool-why-grok").textContent).toMatch(/sign/i);
    expect(screen.getByTestId("signin-grok")).toBeInTheDocument();
    expect(screen.queryByTestId("signin-codex")).toBeNull();
  });

  it("picking a model moves to its levels; picking a level sets it and closes", async () => {
    render(<Harness start={{ tool: "claude-code", model: "", effort: "" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.click(screen.getByTestId("p-model-claude-opus-5-5"));
    await frame();
    expect(value().model).toBe("claude-opus-5-5");
    expect(document.activeElement).toBe(screen.getByTestId("p-effort-default"));
    // Only THIS model's levels.
    expect(screen.queryByTestId("p-effort-medium")).not.toBeNull();
    fireEvent.click(screen.getByTestId("p-effort-high"));
    expect(value()).toEqual({ tool: "claude-code", model: "claude-opus-5-5", effort: "high" });
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("p-tool"));
  });

  it("a model with no levels closes the panel at once", async () => {
    render(<Harness start={{ tool: "claude-code", model: "", effort: "" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.click(screen.getByTestId("p-model-claude-fable-5-1"));
    await frame();
    expect(value().model).toBe("claude-fable-5-1");
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
  });

  it("another tool's model sets the tool first, then the model", async () => {
    render(<Harness start={{ tool: "claude-code", model: "claude-opus-5-5", effort: "high" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.click(screen.getByTestId("p-tool-codex"));
    expect(value()).toEqual({ tool: "codex", model: "", effort: "" });
    fireEvent.click(screen.getByTestId("p-model-gpt-6-astra"));
    await frame();
    expect(value()).toEqual({ tool: "codex", model: "gpt-6-astra", effort: "" });
  });

  it("the search narrows the list by name, id or group", () => {
    expect(matchesModel({ id: "claude-opus-5-5", label: "Opus 5.5" }, "opus")).toBe(true);
    expect(matchesModel({ id: "claude-opus-5-5", label: "Opus 5.5" }, "CLAUDE-OP")).toBe(true);
    expect(matchesModel({ id: "x", label: "Y", group: "standin-b" }, "standin")).toBe(true);
    expect(matchesModel({ id: "x", label: "Y" }, "zzz")).toBe(false);
    render(<Harness start={{ tool: "claude-code", model: "", effort: "" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.change(screen.getByTestId("p-model-search"), { target: { value: "son" } });
    expect(screen.getByTestId("p-model-claude-sonnet-5-5")).toBeInTheDocument();
    expect(screen.queryByTestId("p-model-claude-opus-5-5")).toBeNull();
    fireEvent.change(screen.getByTestId("p-model-search"), { target: { value: "nothing like it" } });
    expect(screen.getByTestId("p-models").textContent).toContain("No model matches.");
  });

  it("the keyboard: the search box has focus, arrows move, Enter picks, Esc closes", async () => {
    render(<Harness start={{ tool: "claude-code", model: "", effort: "" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    const search = screen.getByTestId("p-model-search");
    expect(document.activeElement).toBe(search);
    // The chosen (default) row is highlighted first.
    expect(screen.getByTestId("p-model-default").dataset.active).toBe("true");
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    expect(screen.getByTestId("p-model-claude-sonnet-5-5").dataset.active).toBe("true");
    fireEvent.keyDown(search, { key: "ArrowUp" });
    expect(screen.getByTestId("p-model-claude-opus-5-5").dataset.active).toBe("true");
    fireEvent.keyDown(search, { key: "Enter" });
    await frame();
    expect(value().model).toBe("claude-opus-5-5");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByTestId("p-effort-low"));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
    expect(value().effort).toBe("");
  });

  it("Ctrl+Shift+M opens and closes it; the page hears every opening", () => {
    expect(isPickerShortcut({ key: "M", ctrlKey: true, metaKey: false, shiftKey: true, altKey: false })).toBe(true);
    expect(isPickerShortcut({ key: "m", ctrlKey: false, metaKey: true, shiftKey: true, altKey: false })).toBe(true);
    expect(isPickerShortcut({ key: "m", ctrlKey: true, metaKey: false, shiftKey: false, altKey: false })).toBe(false);
    expect(isPickerShortcut({ key: "m", ctrlKey: true, metaKey: false, shiftKey: true, altKey: true })).toBe(false);
    const onOpen = vi.fn();
    render(<Harness start={{ tool: "claude-code", model: "", effort: "" }} onOpen={onOpen} />);
    fireEvent.keyDown(window, { key: "M", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("p-tool-menu")).toBeInTheDocument();
    expect(onOpen).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "M", ctrlKey: true, shiftKey: true });
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
  });

  // v1.545.0: this case MOVED on purpose. A session's tool was always fixed;
  // now it is fixed only when the page locks it (the session cannot switch,
  // or a reply runs) — see "a session that can switch its coding tool" below.
  it("in a session that cannot switch (or while a reply runs) the tool is fixed: no tool list", () => {
    render(<Harness start={{ tool: "claude-code", model: "claude-opus-5-5", effort: "" }} lock />);
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.queryByTestId("p-tools")).toBeNull();
    expect(screen.queryByTestId("p-tool-codex")).toBeNull();
    expect(screen.getByTestId("p-model-claude-opus-5-5").getAttribute("aria-selected")).toBe("true");
  });

  it("a model the tool no longer lists stays in the list, marked", () => {
    render(<Harness start={{ tool: "claude-code", model: "claude-gone", effort: "" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    const kept = screen.getByTestId("p-model-claude-gone");
    expect(kept.textContent).toContain("not in the list");
    expect(kept.getAttribute("aria-selected")).toBe("true");
  });

  it("a click outside closes it", () => {
    render(<Harness start={{ tool: "claude-code", model: "", effort: "" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
  });
});

describe("new sessions start from the last pick for that tool", () => {
  it("reads a tool's last model and effort only while the tool still offers them", () => {
    const tool = TOOLS.find((t) => t.id === "claude-code")!;
    expect(lastPick(tool)).toEqual({ model: "", effort: "" });
    window.localStorage.setItem(LAST_PICK_KEY + "claude-code", JSON.stringify({ model: "claude-sonnet-5-5", effort: "high" }));
    expect(lastPick(tool)).toEqual({ model: "claude-sonnet-5-5", effort: "high" });
    // An effort the model does not take is dropped; a gone model too.
    window.localStorage.setItem(LAST_PICK_KEY + "claude-code", JSON.stringify({ model: "claude-sonnet-5-5", effort: "medium" }));
    expect(lastPick(tool)).toEqual({ model: "claude-sonnet-5-5", effort: "" });
    window.localStorage.setItem(LAST_PICK_KEY + "claude-code", JSON.stringify({ model: "claude-gone", effort: "high" }));
    expect(lastPick(tool)).toEqual({ model: "", effort: "" });
    window.localStorage.setItem(LAST_PICK_KEY + "claude-code", "not json");
    expect(lastPick(tool)).toEqual({ model: "", effort: "" });
    expect(lastPick(null)).toEqual({ model: "", effort: "" });
  });
});

describe("Claude Code's own short names", () => {
  it("'haiku' is the one listed Haiku: named so, its row checked, its levels; picking it again sends nothing", async () => {
    const tool = TOOLS.find((t) => t.id === "claude-code")!;
    expect(aliasTarget(tool, "sonnet")).toBe("claude-sonnet-5-5");
    expect(aliasTarget(tool, "Opus")).toBe("claude-opus-5-5");
    expect(aliasTarget(tool, "gpt")).toBeNull();
    expect(aliasTarget(TOOLS.find((t) => t.id === "codex")!, "sonnet")).toBeNull();
    const two = { ...tool, models: [...tool.models, { id: "claude-sonnet-4-8", label: "Sonnet 4.8" }] };
    expect(aliasTarget(two, "sonnet")).toBeNull();
    expect(pickerWords(tool, { tool: "claude-code", model: "sonnet", effort: "" })).toBe("Claude Code · Sonnet 5.5");
    render(<Harness start={{ tool: "claude-code", model: "sonnet", effort: "" }} lock />);
    expect(screen.getByTestId("p-tool").textContent).toBe("Claude Code · Sonnet 5.5");
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.queryByTestId("p-model-sonnet")).toBeNull();
    expect(screen.getByTestId("p-model-claude-sonnet-5-5").getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByTestId("p-effort-medium")).toBeNull(); // Sonnet's own levels: low, high
    expect(screen.getByTestId("p-effort-high")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("p-model-claude-sonnet-5-5"));
    await frame();
    expect(value().model).toBe("sonnet");
  });
});

/* v1.475.0 review fixes. */
describe("a session's model change goes to the daemon first", () => {
  function Slow({ applied }: { applied: { current: (m: string) => void } }) {
    // The value only changes when the test says the daemon answered.
    const [v, setV] = useState<PickerChoice>({ tool: "claude-code", model: "claude-sonnet-5-5", effort: "" });
    const asked = React.useRef("");
    applied.current = (m) => setV((cur) => ({ ...cur, model: m }));
    return (
      <>
        <ModelPicker tools={TOOLS} value={v} lockTool testId="p" onChange={(f, n) => { if (f === "model") asked.current = n; }} />
        <output data-testid="asked">{asked.current}</output>
      </>
    );
  }

  it("the new model's levels show at once but wait until it is applied", async () => {
    const applied = { current: (_m: string) => {} };
    render(<Slow applied={applied} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.click(screen.getByTestId("p-model-claude-opus-5-5"));
    await frame();
    // Opus's levels (medium is Opus's, not Sonnet's), all waiting.
    const medium = screen.getByTestId("p-effort-medium") as HTMLButtonElement;
    expect(medium.disabled).toBe(true);
    expect(medium.title).toBe("Changing the model…");
    expect(screen.getByTestId("p-model-claude-opus-5-5").getAttribute("aria-selected")).toBe("true");
    act(() => applied.current("claude-opus-5-5"));
    expect((screen.getByTestId("p-effort-medium") as HTMLButtonElement).disabled).toBe(false);
  });

  // v1.545.0 (review): a session that can switch its tool is NOT locked (its
  // tool list shows), and the same rule must hold there.
  function SlowUnlocked({ applied }: { applied: { current: (m: string) => void } }) {
    const [v, setV] = useState<PickerChoice>({ tool: "claude-code", model: "claude-sonnet-5-5", effort: "" });
    applied.current = (m) => setV((cur) => ({ ...cur, model: m }));
    return <ModelPicker tools={TOOLS} value={v} toolAsks testId="p" onChange={() => {}} />;
  }

  it("…also in a session whose tool list shows (it can switch its coding tool)", async () => {
    const applied = { current: (_m: string) => {} };
    render(<SlowUnlocked applied={applied} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.getByTestId("p-tools")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("p-model-claude-opus-5-5"));
    await frame();
    const medium = screen.getByTestId("p-effort-medium") as HTMLButtonElement;
    expect(medium.disabled).toBe(true);
    expect(medium.title).toBe("Changing the model…");
    expect(screen.getByTestId("p-model-claude-opus-5-5").getAttribute("aria-selected")).toBe("true");
    // A press on a level that waits does nothing.
    fireEvent.click(medium);
    expect(screen.getByTestId("p-tool-menu")).toBeInTheDocument();
    act(() => applied.current("claude-opus-5-5"));
    expect((screen.getByTestId("p-effort-medium") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("leaving the panel", () => {
  it("Tab closes it and the cursor goes back to the chip", () => {
    render(<Harness start={{ tool: "claude-code", model: "", effort: "" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.keyDown(screen.getByTestId("p-model-search"), { key: "Tab" });
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("p-tool"));
  });

  it("with no tools listed the shortcut opens nothing", () => {
    render(<ModelPicker tools={[]} value={{ tool: "", model: "", effort: "" }} onChange={() => {}} testId="p" shortcut />);
    fireEvent.keyDown(window, { key: "M", ctrlKey: true, shiftKey: true });
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
  });
});

/* v1.545.0: a session can go on with another coding tool. */
describe("a session that can switch its coding tool", () => {
  const START: PickerChoice = { tool: "claude-code", model: "claude-opus-5-5", effort: "high" };

  /** The page of a session: it never applies a tool (a press there is a
   *  question); `draft` is what it says it is asking about. */
  function Session({
    asked,
    lock = false,
    draft = null,
    footer,
    onClose,
    control,
  }: {
    asked: [PickerField, string][];
    lock?: boolean;
    draft?: PickerChoice | null;
    footer?: React.ReactNode;
    onClose?: () => void;
    control?: React.MutableRefObject<PickerControl | null>;
  }) {
    return (
      <ModelPicker
        tools={TOOLS}
        value={START}
        lockTool={lock}
        toolAsks
        draft={draft}
        footer={footer}
        onClose={onClose}
        control={control}
        testId="p"
        onChange={(field, next) => asked.push([field, next])}
      />
    );
  }

  it("shows the tool list, with the session's own tool chosen", () => {
    render(<Session asked={[]} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.getByTestId("p-tools")).toBeInTheDocument();
    expect(screen.getByTestId("p-tool-claude-code").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("p-tool-codex").getAttribute("aria-checked")).toBe("false");
    // A tool that cannot start is still greyed, with its reason.
    expect((screen.getByTestId("p-tool-grok") as HTMLButtonElement).disabled).toBe(true);
  });

  it("the page's lock still wins: no tool list", () => {
    render(<Session asked={[]} lock />);
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.queryByTestId("p-tools")).toBeNull();
  });

  it("another tool is asked of the page, never set: the panel stays and shows that tool's models", async () => {
    const asked: [PickerField, string][] = [];
    render(<Session asked={asked} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.click(screen.getByTestId("p-tool-codex"));
    await frame();
    expect(asked).toEqual([["tool", "codex"]]);
    expect(screen.getByTestId("p-tool-menu")).toBeInTheDocument();
    // The chip still names the session's tool; the list is the other tool's,
    // with its default offered first.
    expect(screen.getByTestId("p-tool").dataset.tool).toBe("claude-code");
    expect(screen.getByTestId("p-model-gpt-6-astra")).toBeInTheDocument();
    expect(screen.getByTestId("p-model-default")).toBeInTheDocument();
    expect(screen.queryByTestId("p-model-claude-opus-5-5")).toBeNull();
    // A model there: the tool first, then the model; nothing closes.
    fireEvent.click(screen.getByTestId("p-model-gpt-6-astra"));
    await frame();
    expect(asked).toEqual([
      ["tool", "codex"],
      ["tool", "codex"],
      ["model", "gpt-6-astra"],
    ]);
    expect(screen.getByTestId("p-tool-menu")).toBeInTheDocument();
  });

  it("what the page is asking about shows as chosen, with that model's own levels to ask for", () => {
    const asked: [PickerField, string][] = [];
    const { rerender } = render(<Session asked={asked} draft={{ tool: "codex", model: "gpt-6-astra", effort: "" }} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.click(screen.getByTestId("p-tool-codex"));
    expect(screen.getByTestId("p-model-gpt-6-astra").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("p-no-efforts").textContent).toBe("This model has no effort levels.");
    // Back on the session's own tool the question is taken back.
    asked.length = 0;
    fireEvent.click(screen.getByTestId("p-tool-claude-code"));
    expect(asked).toEqual([["tool", "claude-code"]]);
    expect(screen.getByTestId("p-model-claude-opus-5-5").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("p-effort-high").getAttribute("aria-checked")).toBe("true");
    // With no question open, the session's own tool asks nothing.
    rerender(<Session asked={asked} draft={null} />);
    asked.length = 0;
    fireEvent.click(screen.getByTestId("p-tool-claude-code"));
    expect(asked).toEqual([]);
  });

  it("a level of the tool asked about is asked too, and the panel stays", () => {
    const asked: [PickerField, string][] = [];
    const two = decodeCodingTools({
      harnesses: [
        { id: "claude-code", label: "Claude Code", installed: true, signed_in: true, models: [{ id: "claude-opus-5-5", label: "Opus 5.5", efforts: ["high"] }] },
        { id: "codex", label: "Codex", installed: true, signed_in: true, models: [{ id: "gpt-6-astra", label: "GPT-6 Astra", efforts: ["low", "high"] }] },
      ],
    });
    render(
      <ModelPicker
        tools={two}
        value={START}
        toolAsks
        draft={{ tool: "codex", model: "gpt-6-astra", effort: "low" }}
        effortHeld="Not while a reply runs"
        testId="p"
        onChange={(field, next) => asked.push([field, next])}
      />,
    );
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.click(screen.getByTestId("p-tool-codex"));
    asked.length = 0;
    const high = screen.getByTestId("p-effort-high") as HTMLButtonElement;
    // (What holds the SESSION's levels does not hold an offer.)
    expect(high.disabled).toBe(false);
    expect(screen.getByTestId("p-effort-low").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(high);
    expect(asked).toEqual([["effort", "high"]]);
    expect(screen.getByTestId("p-tool-menu")).toBeInTheDocument();
  });

  it("the page's confirmation is drawn in the panel, and the page hears the panel close", () => {
    const onClose = vi.fn();
    render(<Session asked={[]} footer={<p data-testid="conf">Switch?</p>} onClose={onClose} />);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.getByTestId("p-footer").contains(screen.getByTestId("conf"))).toBe(true);
    expect(screen.getByTestId("p-tool-menu").contains(screen.getByTestId("p-footer"))).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByTestId("p-model-search"), { key: "Escape" });
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
    expect(onClose).toHaveBeenCalledTimes(1);
    // A press outside is a closing too.
    fireEvent.click(screen.getByTestId("p-tool"));
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("with the page's question under the lists, Tab goes to it instead of closing; the hint says so", () => {
    const onClose = vi.fn();
    const { rerender } = render(<Session asked={[]} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.getByTestId("p-hint").textContent).toContain("Esc or Tab closes");
    rerender(
      <Session
        asked={[]}
        onClose={onClose}
        footer={
          <div>
            <button type="button" data-testid="conf-a">Cancel</button>
            <button type="button" data-testid="conf-b">Switch</button>
          </div>
        }
      />,
    );
    expect(screen.getByTestId("p-hint").textContent).toContain("Tab goes to the question below");
    expect(screen.getByTestId("p-hint").textContent).not.toContain("Tab closes");
    fireEvent.keyDown(screen.getByTestId("p-model-search"), { key: "Tab" });
    expect(screen.getByTestId("p-tool-menu")).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByTestId("conf-a"));
    expect(onClose).not.toHaveBeenCalled();
    // From the tool list too.
    screen.getByTestId("p-tool-claude-code").focus();
    fireEvent.keyDown(screen.getByTestId("p-tool-claude-code"), { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByTestId("conf-a"));
    // A Tab the question itself lets through (it has none of its own here)
    // still leaves the panel, as a Tab always did.
    fireEvent.keyDown(screen.getByTestId("conf-a"), { key: "Tab" });
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("the page can put the cursor back in the model list", () => {
    const control: React.MutableRefObject<PickerControl | null> = { current: null };
    render(<Session asked={[]} control={control} footer={<button type="button" data-testid="conf-a">Cancel</button>} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    screen.getByTestId("conf-a").focus();
    act(() => control.current!.focusList());
    expect(document.activeElement).toBe(screen.getByTestId("p-model-search"));
  });

  it("the page can open it on the tool list, and close it", () => {
    const control: React.MutableRefObject<PickerControl | null> = { current: null };
    render(<Session asked={[]} control={control} />);
    expect(control.current!.isOpen()).toBe(false);
    act(() => control.current!.openTools());
    expect(screen.getByTestId("p-tools")).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByTestId("p-tool-claude-code"));
    expect(control.current!.isOpen()).toBe(true);
    act(() => control.current!.close());
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
    expect(control.current!.isOpen()).toBe(false);
    expect(document.activeElement).toBe(screen.getByTestId("p-tool"));
    // Opened by its chip, the cursor is in the search box as always.
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(document.activeElement).toBe(screen.getByTestId("p-model-search"));
  });
});
