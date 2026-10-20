/**
 * v1.359.0 integrate — Wave 9 joined, the dashboard's half.
 *
 * THE SEAMS
 *  (a) the run words (lane 9A, lib/runWords AGENT_TOOLS) and the model words
 *      (lane 9B) — the vocabulary scan (vocabulary.test.ts) reads them; here:
 *      a connected app's tool reads the daemon's name ("Gmail › search
 *      messages", the ledger row's `name`) in "What it did", the words the
 *      daemon's "Step by step" uses two lines below;
 *  (b) the Connect door (lane 9C) in the real daemon's done frame goes to the
 *      Directory's own address (lib/relocated's `/marketplace`);
 *  (c) every model LIST outside the composer's menu names a model in the
 *      menu's words (lib/onboarding.modelPickWords), never "subscription" /
 *      "fleet"; the raw id rides the option's title.
 *
 * THE FOLLOW-UPS (lane 9B review): the Try-again menu keeps the raw id on
 * hover; the composer's filter finds a row by the words it shows; an older
 * stopped run's engine words read as the shared stopped sentence on the run
 * page. (The optional "answered by your model server" was NOT taken: the
 * mission receipt says "<model> on <place>", and the place is already named.)
 *
 * Real components and helpers; only the transport is faked.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return { FakeApiError, gets: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    const r = H.gets[path.split("?")[0]];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 500);
    return r;
  },
  put: async () => ({}),
  post: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "claude-cli", providers: [] },
    refresh: vi.fn(),
  }),
}));

import { DefaultModelPicker } from "@/components/settings/DefaultModelPicker";
import { RegenerateMenu } from "@/components/chat/RegenerateMenu";
import { modelPickTitle, modelPickWords } from "@/lib/onboarding";
import { matchModels } from "@/lib/recentModels";
import { rowName, runActionLines, toolDidWords, toolFailedWords } from "@/lib/runWords";
import { stampTools } from "@/lib/agentRun";
import { shownSummary, STOPPED_SUMMARY, STOPPED_BEFORE_START } from "@/components/sessions/runPageWords";
import { RELOCATED } from "@/lib/relocated";
import { DEMO_MODEL_LABEL } from "@/lib/demoWords";
import type { ModelOption } from "@/lib/types";

const root = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(root, rel), "utf8").replace(/\r\n/g, "\n");

const PLAN: ModelOption = { provider: "claude-cli", model: "subscription", kind: "cli", available: true };
const CODEX: ModelOption = { provider: "codex-cli", model: "subscription", kind: "cli", available: true };
const FLEET: ModelOption = { provider: "custom", model: "fleet", kind: "local", available: true };
const OPUS: ModelOption = { provider: "claude-cli", model: "claude-opus-4-8", kind: "cli", available: true };
const OPENCODE: ModelOption = { provider: "opencode-cli", model: "spark/fleet", kind: "cli", available: true };

afterEach(() => cleanup());

describe("(c) one model-list rule: modelPickWords", () => {
  it("names a provider-default row by its plan or server, an alias by the server, an id by its name", () => {
    expect(modelPickWords(PLAN)).toBe("Your Claude plan");
    expect(modelPickWords(CODEX)).toBe("Your ChatGPT plan");
    expect(modelPickWords(FLEET)).toBe("Your model server");
    expect(modelPickWords(OPUS)).toBe("Claude Code · Opus 4.8");
    expect(modelPickWords({ provider: "mock", model: "mock-1" })).toBe(DEMO_MODEL_LABEL);
    // A labelled row keeps its label; the raw pair is the title.
    expect(modelPickWords({ ...OPUS, label: "Opus 4.8 (1M)" })).toBe("Claude Code · Opus 4.8 (1M)");
    expect(modelPickTitle(PLAN)).toBe("claude-cli · subscription");
    for (const m of [PLAN, CODEX, FLEET, OPUS, OPENCODE]) {
      expect(modelPickWords(m)).not.toMatch(/\bsubscription\b|\bfleet\b|claude-cli|codex-cli/);
    }
  });

  it("the six lists outside the composer's menu all name a model through it", () => {
    // A source pin on the CALL SITE (the helper is pinned above): each list
    // used to print `${m.provider} · ${m.model}` by hand.
    for (const rel of [
      "components/agents/SetupCard.tsx",
      "components/terminal/TerminalPane.tsx",
      "app/templates/page.tsx",
      "app/projects/[id]/page.tsx",
      "components/NewSessionForm.tsx",
    ]) {
      const src = read(rel);
      expect(src, rel).toContain("modelPickWords(");
      expect(src, rel).not.toMatch(/\{m\.provider\} · \{m\.model\}/);
      expect(src, rel).not.toMatch(/\{m\.name \|\| m\.provider\} \/ \{m\.model\}/);
    }
  });

  it("the Settings default-model list (the real DefaultModelPicker) reads in words; the raw id is the title", async () => {
    H.gets = { "/models": { models: [PLAN, OPUS, CODEX] } };
    render(<DefaultModelPicker provider="claude-cli" value="subscription" onChange={() => {}} />);
    const box = screen.getByRole("combobox", { name: "Default model" });
    await waitFor(() => expect(within(box).getByRole("option", { name: "Your Claude plan" })).toBeTruthy());
    const plan = within(box).getByRole("option", { name: "Your Claude plan" });
    expect(plan.getAttribute("title")).toBe("subscription");
    expect(within(box).getByRole("option", { name: "Opus 4.8" }).getAttribute("title")).toBe("claude-opus-4-8");
    expect(box.textContent).not.toMatch(/\bsubscription\b/);
    // Scoped to the provider: Codex's plan is not in Claude Code's list.
    expect(within(box).queryByRole("option", { name: "Your ChatGPT plan" })).toBeNull();
  });
});

describe("lane 9B review follow-ups", () => {
  it("the Try-again menu keeps the raw id on an unlabelled row's hover", async () => {
    render(
      <RegenerateMenu
        models={[OPUS, PLAN]}
        recent={[]}
        current="codex-cli::subscription"
        answeredBy={{ provider: "codex-cli", kind: "cli" }}
        onRegenerate={() => {}}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("regen-more"));
    });
    const opus = screen.getByText("Opus 4.8");
    expect(opus.getAttribute("title")).toBe("claude-opus-4-8");
    expect(screen.getByText("Your Claude plan").getAttribute("title")).toBe("subscription");
  });

  it("the composer's filter finds a row by the words it shows", () => {
    const rows = [PLAN, FLEET, OPUS, CODEX];
    expect(matchModels(rows, "Claude plan", 5)).toEqual([PLAN]);
    expect(matchModels(rows, "model server", 5)).toEqual([FLEET]);
    expect(matchModels(rows, "opus 4.8", 5)).toEqual([OPUS]);
    // The raw id still finds it (the old pin's half).
    expect(matchModels(rows, "claude-opus-4-8", 5)).toEqual([OPUS]);
  });

});

describe("(a) the run card names an app's tool the daemon's way", () => {
  const GMAIL = "mcp__gmail__search_messages";

  it("'What it did' prefers the ledger row's name, the Step-by-step words", () => {
    const lines = runActionLines(
      [{ tool: GMAIL, count: 3, name: "Gmail › search messages" }],
      [{ tool: GMAIL, count: 1, name: "Gmail › search messages" }],
    );
    expect(lines.map((l) => l.text)).toEqual([
      "Used Gmail › search messages 2 times",
      "One Gmail › search messages call failed",
    ]);
    expect(lines[0].tool).toBe(GMAIL);
    // No name (an older daemon): the pack's own words, as before.
    expect(toolDidWords(GMAIL, 1)).toBe("Used gmail: search messages");
    expect(toolFailedWords(GMAIL, 2, "Gmail › search messages")).toBe("2 Gmail › search messages calls failed");
  });

  it("the settled row's stamp keeps the name; junk is no name", () => {
    const tools = stampTools([{ tool: GMAIL, count: 2, name: "Gmail › search messages" }], []);
    expect(tools).toEqual([{ tool: GMAIL, count: 2, failed: 0, name: "Gmail › search messages" }]);
    expect(stampTools([{ tool: "read_file", count: 1 }], [])).toEqual([{ tool: "read_file", count: 1, failed: 0 }]);
    expect(rowName("<b>x</b>")).toBe("");
    expect(rowName(42)).toBe("");
    expect(rowName("x".repeat(161))).toBe("");
  });

  it("a built-in the run log once spaced out reads in words", () => {
    expect(toolDidWords("assign_work", 1)).toBe("Queued a task for a teammate");
    expect(toolFailedWords("pane_send", 2)).toBe("2 terminal entries failed");
    expect(toolDidWords("app_connect", 1)).toBe("Connected an app");
  });
});

describe("the run page reads an older stopped run in the shared sentence", () => {
  it("maps the engine's old words; any other summary is shown as it is", () => {
    expect(shownSummary("Session cancelled by the user.", false)).toBe(STOPPED_SUMMARY);
    expect(shownSummary("Cancelled while queued (never started).", false)).toBe(STOPPED_BEFORE_START);
    expect(shownSummary("The agent was stopped before it finished.", false)).toBe(STOPPED_SUMMARY);
    expect(shownSummary("Wrote the brief.", false)).toBe("Wrote the brief.");
  });
});

describe("(b) the Connect door goes to the Directory's own address", () => {
  it("the real daemon's door href is lib/relocated's /marketplace", () => {
    const fixture = JSON.parse(read("__tests__/fixtures/connect-door-v13590.json"));
    const doors: { href: string; label: string }[] = fixture.stream_done.doors;
    expect(Array.isArray(doors) && doors.length > 0).toBe(true);
    const dir = RELOCATED["/marketplace"];
    const address = `${dir.base}?${new URLSearchParams(dir.extra).toString()}`;
    for (const d of doors) {
      expect(d.href).toBe(address);
      expect(d.label).toMatch(/^Connect .+ in Settings$/);
    }
  });
});

beforeEach(() => {
  H.gets = {};
});
