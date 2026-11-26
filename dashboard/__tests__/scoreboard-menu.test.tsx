/**
 * Next leaps 28-a (R2.17): the record for this folder, in the model menu of a
 * coding session. Under each tool and model one muted line from the daemon's
 * own record (`GET /build/scoreboard?folder=`): "In this folder: kept 3 of 4
 * (too few to judge)". Nothing on record: no line. Read ONCE per opening,
 * never while the menu is closed (G7). Chat's own model menu never reads it
 * and keeps its markup.
 */
import React, { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: [] as string[],
  answer: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.gets.push(path);
    const folder = decodeURIComponent(path.split("folder=")[1] ?? "");
    const r = H.answer[folder];
    if (r === undefined) throw new Error(`unmocked GET ${path}`);
    return r;
  },
}));

import { ModelPicker, type PickerChoice } from "@/components/buildCode/ModelPicker";
import { ModelMenu, modelTriggerOf } from "@/components/chat/ModelMenu";
import { decodeCodingTools } from "@/lib/buildApp";
import { decodeCodingRecord, jarvisLine, recordLine } from "@/lib/scoreboard";
import type { ModelOption } from "@/lib/types";

const FOLDER = "C:\\code\\shop";
const LINE = "In this folder: kept 3 of 4 (too few to judge)";
const RECORD = {
  folder: "c:/code/shop",
  min_samples: 5,
  rows: [
    { surface: "tool", tool: "codex", provider: "", model: "gpt-6-astra", kept: 3, thrown: 1, undecided: 0,
      passed: 0, failed: 0, samples: 4, words: LINE },
    { surface: "tool", tool: "claude-code", provider: "", model: "opus", kept: 7, thrown: 2, undecided: 3,
      passed: 8, failed: 1, samples: 9, words: "In this folder: kept 7 of 9, passed its checks 8 of 9; 3 not decided yet" },
    { surface: "code", tool: "jarvis", provider: "anthropic", model: "claude-haiku-4-5", kept: 1, thrown: 0,
      undecided: 0, passed: 1, failed: 0, samples: 1,
      words: "In this folder: kept 1 of 1 (too few to judge), passed its checks 1 of 1" },
  ],
};

const TOOLS = decodeCodingTools({
  harnesses: [
    {
      id: "claude-code", label: "Claude Code", installed: true, signed_in: true,
      modes: [{ id: "ask", label: "Ask first" }],
      models: [
        { id: "claude-opus-5-5", label: "Opus 5.5", efforts: ["low", "high"] },
        { id: "claude-sonnet-5-5", label: "Sonnet 5.5", efforts: [] },
      ],
      efforts: ["low", "high"],
    },
    {
      id: "codex", label: "Codex", installed: true, signed_in: true,
      modes: [{ id: "ask", label: "Ask first" }],
      models: [{ id: "gpt-6-astra", label: "GPT-6 Astra" }, { id: "gpt-6-mini", label: "GPT-6 Mini" }],
      efforts: [],
    },
  ],
});

function Picker({ start, folder }: { start: PickerChoice; folder?: string | null }) {
  const [value, setValue] = useState(start);
  return (
    <ModelPicker
      tools={TOOLS}
      value={value}
      onChange={(field, next) => setValue((v) => ({ ...v, [field]: next }))}
      testId="p"
      recordFolder={folder}
    />
  );
}

afterEach(() => {
  cleanup();
  H.gets.length = 0;
  for (const k of Object.keys(H.answer)) delete H.answer[k];
});

describe("the record's words, decoded and matched", () => {
  it("keeps only rows with words and matches a row by tool and model", () => {
    const rec = decodeCodingRecord({ ...RECORD, rows: [...RECORD.rows, { tool: "codex", model: "x", words: "" }, 7] });
    expect(rec.rows).toHaveLength(3);
    expect(recordLine(rec, "codex", "gpt-6-astra")).toBe(LINE);
    expect(recordLine(rec, "codex", "gpt-6-mini")).toBe("");
    expect(recordLine(rec, "claude-code", "claude-opus-5-5", "", (m) => (m === "opus" ? "claude-opus-5-5" : null))).toMatch(
      /kept 7 of 9/,
    );
    expect(jarvisLine(rec, "anthropic::claude-haiku-4-5")).toMatch(/^In this folder: kept 1 of 1/);
    expect(jarvisLine(rec, "openai::claude-haiku-4-5")).toBe("");
    expect(jarvisLine(rec, "")).toBe("");
    expect(decodeCodingRecord(null).rows).toEqual([]);
    expect(decodeCodingRecord({ rows: "no" }).rows).toEqual([]);
  });
});

describe("Build › Code's model picker", () => {
  it("shows one muted line under each model with a record, read once per opening", async () => {
    H.answer[FOLDER] = RECORD;
    render(<Picker start={{ tool: "codex", model: "gpt-6-astra", effort: "" }} folder={FOLDER} />);
    expect(H.gets).toEqual([]); // never while the menu is closed
    fireEvent.click(screen.getByTestId("p-tool"));
    const line = await screen.findByTestId("p-model-record-gpt-6-astra");
    expect(line.textContent).toBe(LINE);
    expect(screen.queryByTestId("p-model-record-gpt-6-mini")).toBeNull(); // nothing on record: nothing
    expect(within(screen.getByTestId("p-model-gpt-6-mini")).queryByText(/In this folder/)).toBeNull();
    expect(H.gets).toEqual([`/build/scoreboard?folder=${encodeURIComponent(FOLDER)}`]);
    // Closed and opened again: read again (and only then).
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.queryByTestId("p-tool-menu")).toBeNull();
    expect(H.gets).toHaveLength(1);
    fireEvent.click(screen.getByTestId("p-tool"));
    await waitFor(() => expect(H.gets).toHaveLength(2));
    expect((await screen.findByTestId("p-model-record-gpt-6-astra")).textContent).toBe(LINE);
  });

  it("finds Claude Code's short model name under the model it runs", async () => {
    H.answer[FOLDER] = RECORD;
    render(<Picker start={{ tool: "claude-code", model: "claude-opus-5-5", effort: "" }} folder={FOLDER} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    const line = await screen.findByTestId("p-model-record-claude-opus-5-5");
    expect(line.textContent).toBe("In this folder: kept 7 of 9, passed its checks 8 of 9; 3 not decided yet");
    expect(screen.queryByTestId("p-model-record-claude-sonnet-5-5")).toBeNull();
  });

  it("reads nothing with no folder, and a read that fails shows nothing", async () => {
    render(<Picker start={{ tool: "codex", model: "", effort: "" }} folder={null} />);
    fireEvent.click(screen.getByTestId("p-tool"));
    expect(screen.getByTestId("p-tool-menu")).toBeTruthy();
    expect(H.gets).toEqual([]);
    cleanup();
    render(<Picker start={{ tool: "codex", model: "", effort: "" }} folder="C:\\nowhere" />);
    fireEvent.click(screen.getByTestId("p-tool"));
    await waitFor(() => expect(H.gets).toHaveLength(1));
    expect(screen.queryByText(/In this folder/)).toBeNull();
    // The model row's own markup is exactly as before when there is no line.
    const row = screen.getByTestId("p-model-gpt-6-astra");
    expect(row.querySelector("span.min-w-0.truncate")?.textContent).toBe("GPT-6 Astra");
  });
});

const row = (provider: string, model: string, kind: string, extra: Partial<ModelOption> = {}): ModelOption =>
  ({ provider, model, kind, available: true, inherited_from: null, ...extra }) as ModelOption;
const MODELS: ModelOption[] = [row("anthropic", "claude-haiku-4-5", "api"), row("anthropic", "claude-opus-4-8", "api")];

function Menu({ folder }: { folder?: string | null }) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("anthropic::claude-haiku-4-5");
  return (
    <ModelMenu
      open={open}
      onOpenChange={setOpen}
      choice={choice}
      models={MODELS}
      trigger={modelTriggerOf(choice, MODELS, "", "")}
      onPick={(v) => setChoice(v)}
      recordFolder={folder}
    />
  );
}

function openProvider() {
  fireEvent.click(screen.getByTitle("Switch model"));
  const menu = screen.getByTestId("model-menu");
  const provider = within(menu)
    .getAllByRole("button")
    .find((b) => b.getAttribute("aria-expanded") !== null && /Anthropic/i.test(b.textContent ?? "")) as HTMLElement;
  fireEvent.click(provider);
  return screen.getByTestId("model-sub");
}

describe("Chat › Code's model menu", () => {
  it("shows the line under the model row of a coding session's menu", async () => {
    H.answer[FOLDER] = RECORD;
    render(<Menu folder={FOLDER} />);
    expect(H.gets).toEqual([]);
    const sub = openProvider();
    const said = await within(sub).findByTestId("model-record");
    expect(said.textContent).toBe("In this folder: kept 1 of 1 (too few to judge), passed its checks 1 of 1");
    expect(within(sub).getAllByTestId("model-record")).toHaveLength(1); // opus has nothing on record
    expect(H.gets).toHaveLength(1);
  });

  it("the chat's own menu reads nothing and keeps its markup", () => {
    render(<Menu />);
    const sub = openProvider();
    expect(H.gets).toEqual([]);
    expect(within(sub).queryByTestId("model-record")).toBeNull();
    const cells = Array.from(sub.querySelectorAll("button > span.min-w-0.truncate")).map((s) => s.textContent);
    expect(cells.length).toBe(2);
  });
});
