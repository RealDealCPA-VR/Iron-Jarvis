/**
 * Three things the user asked for, beside a screenshot of a coding session:
 *
 *  1. "the option for just 'your claude plan'. When this is selected it does
 *     not indicate which model is being used and does not make any sense" —
 *     the CLI's own-default row is FOLDED into the model it runs (the catalog
 *     row marked `cli_default`), in the chat's menu, the coding session's menu
 *     and Try on; a saved pick of it is marked on that model and the chip
 *     names that model. A list that keeps both rows (Settings) says
 *     "Claude Code's default (Opus 5.5)". With no live picker, nothing folds.
 *  2. "in effort it shows the options default, low, medium and high. Default
 *     should not be there and there should be Extra in models that allow for
 *     it" — the effort list holds the levels only, "Extra" (xhigh) where the
 *     row offers it; nothing is sent until the user picks a level other than
 *     the one marked.
 *  3. The session's six pane words become icons; each word is its tab's name
 *     and a tip shown only while the pointer rests there or the tab has
 *     keyboard focus.
 */
import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, gets: {} as Record<string, unknown> };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) =>
    p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new api.ApiError(`unmocked ${p}`, 404)),
  post: () => Promise.resolve({ changes: [] }),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { ModelMenu, ReasoningChip, modelTriggerOf } from "@/components/chat/ModelMenu";
import { HarnessPanes, PANE_TABS, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { companyMenu } from "@/lib/harnessModelMenu";
import { foldPlanRows, modelPickWords, modelRowName, planModelRow } from "@/lib/onboarding";
import { levelWords, reasoningShownLevel } from "@/lib/reasoningChip";
import { RECENT_MODELS_KEY } from "@/lib/recentModels";
import { tryOnGroups } from "@/lib/tryOnMenu";
import { decodeSessionView } from "@/lib/buildSessions";
import type { ModelOption } from "@/lib/types";
import type { TryOnRow } from "@/lib/buildAttempts";

const XHIGH = ["low", "medium", "high", "xhigh"];
const BASE = ["low", "medium", "high"];

const row = (provider: string, model: string, extra: Partial<ModelOption> & Record<string, unknown> = {}): ModelOption =>
  ({ provider, model, kind: "cli", available: true, inherited_from: null, ...extra }) as ModelOption;

/** The real daemon's shape (harness-model-fold-v1376.json): the plan row has
 *  no label, the live picker's rows say which one is the CLI's default. */
const CATALOG: ModelOption[] = [
  row("claude-cli", "subscription", { reasoning: XHIGH, reasoning_default: "" }),
  row("claude-cli", "claude-opus-5-5", { label: "Opus 5.5", cli_default: true, reasoning: XHIGH }),
  row("claude-cli", "claude-sonnet-5-5", { label: "Sonnet 5.5", cli_default: false, reasoning: XHIGH }),
  row("claude-cli", "claude-haiku-4-5-20251001", { label: "Haiku 4.5", cli_default: false, reasoning: BASE }),
  row("codex-cli", "subscription", { reasoning: BASE }),
  row("codex-cli", "gpt-6", { reasoning: BASE }),
];
/** No live picker yet: the pinned table names no default. */
const NO_PICKER: ModelOption[] = CATALOG.map((m) => ({ ...m, cli_default: undefined }) as ModelOption);

beforeEach(() => {
  api.gets = { "/browser/status": { paired: false } };
  localStorage.clear();
});
afterEach(() => cleanup());

// ==========================================================================
// 1. the plan row is the model it runs
// ==========================================================================

describe("the plan row is the model it runs", () => {
  it("planModelRow finds the CLI's default row, and only from the live picker", () => {
    expect(planModelRow("claude-cli", CATALOG)?.model).toBe("claude-opus-5-5");
    expect(planModelRow("codex-cli", CATALOG)).toBeUndefined();
    expect(planModelRow("claude-cli", NO_PICKER)).toBeUndefined();
    expect(planModelRow("claude-cli", null)).toBeUndefined();
  });

  it("foldPlanRows lists each model once and maps the plan's pick onto the model", () => {
    const { models, alias } = foldPlanRows(CATALOG);
    expect(models.map((m) => `${m.provider}::${m.model}`)).toEqual([
      "claude-cli::claude-opus-5-5",
      "claude-cli::claude-sonnet-5-5",
      "claude-cli::claude-haiku-4-5-20251001",
      // Codex's CLI names no default: its plan row stays.
      "codex-cli::subscription",
      "codex-cli::gpt-6",
    ]);
    expect(alias).toEqual({ "claude-cli::subscription": "claude-cli::claude-opus-5-5" });
    // Nothing folds while the catalog names no default.
    expect(foldPlanRows(NO_PICKER).models).toHaveLength(CATALOG.length);
  });

  it("the plan row is named by its model; a list that keeps both says whose default it is", () => {
    const plan = { provider: "claude-cli", model: "subscription" };
    expect(modelRowName(plan, CATALOG)).toBe("Opus 5.5");
    expect(modelPickWords(plan, CATALOG)).toBe("Claude Code's default (Opus 5.5)");
    // No picker: the old words stay (never a guessed model).
    expect(modelRowName(plan, NO_PICKER)).toBe("Your Claude plan");
    expect(modelPickWords(plan, NO_PICKER)).toBe("Your Claude plan");
    // Another row is untouched.
    expect(modelPickWords({ provider: "claude-cli", model: "claude-sonnet-5-5", label: "Sonnet 5.5" }, CATALOG)).not.toMatch(
      /default/,
    );
  });

  it("the chip names the model a saved plan pick runs", () => {
    expect(modelTriggerOf("claude-cli::subscription", CATALOG, "", "").text).toBe("Opus 5.5");
    expect(modelTriggerOf("", CATALOG, "claude-cli", "subscription").text).toBe("Opus 5.5");
  });

  it("the coding session's menu folds it and marks the plan's pick on the model", () => {
    const menu = companyMenu(CATALOG, null, null, "claude-cli::subscription");
    expect(menu.models.some((m) => m.provider === "claude-cli" && m.model === "subscription")).toBe(false);
    expect(menu.choice).toBe("claude-cli::claude-opus-5-5");
  });

  it("Try on lists it once, as the model", () => {
    const groups = tryOnGroups(CATALOG as unknown as TryOnRow[], false);
    const claude = groups.find((g) => g.id === "claude-cli");
    expect(claude).toBeTruthy();
    const models = claude!.models.map((e) => e.model);
    expect(models).not.toContain("subscription");
    expect(models.filter((m) => m === "claude-opus-5-5")).toHaveLength(1);
  });

  it("the chat's menu lists the model once and marks a saved plan pick there", () => {
    function Menu() {
      const [open, setOpen] = useState(false);
      return (
        <ModelMenu
          open={open}
          onOpenChange={setOpen}
          choice="claude-cli::subscription"
          models={CATALOG}
          trigger={modelTriggerOf("claude-cli::subscription", CATALOG, "", "")}
          onPick={() => {}}
        />
      );
    }
    render(<Menu />);
    expect(screen.getByTestId("model-chip-words").textContent).toBe("Opus 5.5");
    fireEvent.click(screen.getByTitle("Switch model"));
    const menu = screen.getByTestId("model-menu");
    fireEvent.change(within(menu).getByTestId("model-filter"), { target: { value: "claude" } });
    const matches = within(menu).getAllByTestId("model-match");
    const words = matches.map((m) => m.textContent ?? "");
    expect(words.some((w) => /Your Claude plan/.test(w))).toBe(false);
    expect(words.filter((w) => w.includes("Opus 5.5"))).toHaveLength(1);
    const opus = matches.find((m) => (m.textContent ?? "").includes("Opus 5.5"))!;
    expect(opus.className).toContain("text-accent-soft");
  });

  it.each([
    ["the plan pick alone", ["claude-cli::subscription"]],
    ["the plan pick and the model", ["claude-cli::subscription", "claude-cli::claude-opus-5-5"]],
  ])("Recent lists %s once, as the model", (_name, picks) => {
    localStorage.setItem(RECENT_MODELS_KEY, JSON.stringify(picks));
    function Menu() {
      const [open, setOpen] = useState(false);
      return (
        <ModelMenu
          open={open}
          onOpenChange={setOpen}
          choice=""
          models={CATALOG}
          trigger={modelTriggerOf("", CATALOG, "", "")}
          onPick={() => {}}
        />
      );
    }
    render(<Menu />);
    fireEvent.click(screen.getByTitle("Switch model"));
    const recent = screen.getByTestId("model-recent");
    const text = recent.textContent ?? "";
    expect(text.split("Opus 5.5").length - 1).toBe(1);
    expect(text).not.toMatch(/Your Claude plan/);
  });
});

// ==========================================================================
// 2. the effort list
// ==========================================================================

function options(): HTMLOptionElement[] {
  return Array.from(screen.getByTestId("reasoning-level").querySelectorAll("option"));
}
const visible = () => options().filter((o) => !o.hidden).map((o) => o.textContent);

describe("the effort list: the levels only, Extra where offered", () => {
  it("names xhigh Extra", () => {
    expect(levelWords("xhigh")).toBe("Extra");
    expect(levelWords("high")).toBe("High");
    expect(levelWords("max")).toBe("");
  });

  it("a model that takes Extra lists Low, Medium, High, Extra and no Default", () => {
    render(<ReasoningChip levels={XHIGH} value="" def="" onChange={() => {}} />);
    expect(visible()).toEqual(["Low", "Medium", "High", "Extra"]);
    expect(visible().some((w) => /default/i.test(w ?? ""))).toBe(false);
  });

  it("a model that does not take it has no Extra", () => {
    render(<ReasoningChip levels={BASE} value="" def="medium" onChange={() => {}} />);
    expect(visible()).toEqual(["Low", "Medium", "High"]);
  });

  it("with nothing picked and no known default, choosing Low really picks it", () => {
    const onChange = vi.fn();
    render(<ReasoningChip levels={XHIGH} value="" def="" onChange={onChange} />);
    const select = screen.getByTestId("reasoning-level") as HTMLSelectElement;
    // A hidden placeholder holds the selection: no level reads as chosen.
    expect(select.value).toBe("");
    expect(screen.getByTestId("reasoning-level").closest("span")?.textContent).toContain("Reasoning");
    fireEvent.change(select, { target: { value: "low" } });
    expect(onChange).toHaveBeenCalledWith("low");
  });

  it("the model's own level is marked, and choosing it again sends nothing", () => {
    const onChange = vi.fn();
    render(<ReasoningChip levels={XHIGH} value="" def="medium" onChange={onChange} />);
    const select = screen.getByTestId("reasoning-level") as HTMLSelectElement;
    expect(select.value).toBe("medium");
    expect(options().some((o) => o.value === "" )).toBe(false);
    fireEvent.change(select, { target: { value: "medium" } });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(select, { target: { value: "xhigh" } });
    expect(onChange).toHaveBeenCalledWith("xhigh");
  });

  it("a picked Extra reads Extra on the chip", () => {
    render(<ReasoningChip levels={XHIGH} value="xhigh" def="medium" onChange={() => {}} />);
    expect((screen.getByTestId("reasoning-level") as HTMLSelectElement).value).toBe("xhigh");
    expect(screen.getByTestId("reasoning-level").closest("span")?.textContent).toContain("Extra");
  });

  it("reasoningShownLevel: the pick, else an offered default, else none", () => {
    expect(reasoningShownLevel(XHIGH, "xhigh", "medium")).toBe("xhigh");
    expect(reasoningShownLevel(BASE, "xhigh", "medium")).toBe("medium");
    expect(reasoningShownLevel(XHIGH, "", "high")).toBe("high");
    expect(reasoningShownLevel(XHIGH, "", "auto")).toBe("");
    expect(reasoningShownLevel(XHIGH, "", "")).toBe("");
  });

  it("a keyless API folded into Claude Code never offers Extra", () => {
    const withApi = [
      ...CATALOG,
      row("anthropic", "claude-opus-4-8", { kind: "api", inherited_from: "claude-cli", reasoning: XHIGH }),
    ];
    const menu = companyMenu(withApi, null, null, "");
    const folded = menu.models.find((m) => m.provider === "claude-cli" && m.model === "claude-opus-4-8");
    expect(folded).toBeTruthy();
    expect(folded!.reasoning).toEqual(BASE);
  });
});

// ==========================================================================
// 3. the pane tabs are icons, their words a tip
// ==========================================================================

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

function panes() {
  const v = decodeSessionView({
    id: "s1",
    title: "Cart",
    folder: "C:\\work\\shop",
    folder_name: "shop",
    session_id: "build:s1",
    messages: [{ role: "user", content: "Build the shop", at: "2026-10-25T10:00:00" }],
  });
  if (!v) throw new Error("bad fixture");
  render(<HarnessPanes view={v} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />);
}

describe("the pane tabs: icons, the word on hover or focus", () => {
  it("each tab is an icon whose name is its word", () => {
    panes();
    const tabs = within(screen.getByTestId("harness-pane-tabs")).getAllByRole("tab");
    expect(tabs).toHaveLength(6);
    for (const t of PANE_TABS) {
      const tab = screen.getByRole("tab", { name: t.label });
      expect(tab.getAttribute("data-testid")).toBe(`harness-pane-tab-${t.value}`);
      expect(tab.querySelector("svg")).toBeTruthy();
      // The ONLY words in the tab are its tip.
      const tip = within(tab).getByTestId(`harness-pane-tip-${t.value}`);
      expect(tip.textContent).toBe(t.label);
      expect(tab.textContent).toBe(t.label);
    }
  });

  it("the tip is faded until the pointer rests (after a pause) or the tab has keyboard focus", () => {
    panes();
    for (const t of PANE_TABS) {
      const tip = screen.getByTestId(`harness-pane-tip-${t.value}`);
      const cls = tip.className.split(/\s+/);
      expect(cls).toContain("opacity-0");
      expect(cls).toContain("group-hover:opacity-100");
      expect(cls).toContain("group-hover:delay-300");
      expect(cls).toContain("group-focus-visible:opacity-100");
      expect(cls).toContain("pointer-events-none");
      // Not hidden from a screen reader: it is the tab's name.
      expect(tip.getAttribute("aria-hidden")).toBeNull();
      expect(screen.getByTestId(`harness-pane-tab-${t.value}`).getAttribute("aria-label")).toBeNull();
    }
  });

  it("arrow keys still move between the tabs", () => {
    panes();
    const plan = screen.getByTestId("harness-pane-tab-plan");
    plan.focus();
    fireEvent.keyDown(plan, { key: "ArrowRight" });
    expect(document.activeElement).toBe(screen.getByTestId("harness-pane-tab-timeline"));
    expect(screen.getByTestId("harness-pane-tab-timeline").getAttribute("aria-selected")).toBe("true");
  });
});
