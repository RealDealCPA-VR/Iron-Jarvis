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
import { ModelPicker, aliasTarget, isPickerShortcut, matchesModel, pickerWords, type PickerChoice } from "@/components/buildCode/ModelPicker";
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

  it("in a running session the tool is fixed: no tool list", () => {
    render(<Harness start={{ tool: "claude-code", model: "claude-opus-5-5", effort: "" }} lock />);
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.queryByTestId("p-tools")).toBeNull();
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
