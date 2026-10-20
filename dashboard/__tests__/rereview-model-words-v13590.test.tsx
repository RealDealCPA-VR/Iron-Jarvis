/**
 * v1.359.0 re-review — the model's name in words on the surfaces lane 9B's
 * lists did not reach.
 *
 * Lane 9B (and its integrator) named a model in words in every MENU and every
 * model LIST ("Your Claude plan", "Your model server", "Opus 4.8"), and
 * `lib/answeredModel` taught the reply's receipt that a provider-default id
 * ("subscription") is answered by THAT provider ("answered by Claude Code").
 * Four places still printed the raw words a user on Claude Code reads:
 *
 *   - the run card under an agent's answer: "answered by subscription";
 *   - the helper-agent row in chat: "Ran on subscription";
 *   - a saved template's model chip: "subscription";
 *   - the terminal assist's footer and the skill's "Use this skill" answer:
 *     "claude-cli · subscription" (and "mock · mock-1" for the demo).
 *
 * Each now goes through the helper the rest of the wave uses
 * (`answeredModelName` for "who answered", `modelPickWords` for a picked
 * model), with the raw pair kept in the title. The look is unchanged (same
 * elements, same classes): only the words move.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    base: "http://127.0.0.1:8797",
    responses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get API_BASE() {
    return api.base;
  },
  ijToken: () => "",
  wsUrl: (p: string) => p,
  sseUrl: (p: string) => p,
  flattenDetail: (d: unknown) => String(d),
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  api: async () => ({}),
  get: async (path: string) => api.responses[path] ?? {},
  post: async () => ({ ok: true }),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1 data-testid="page-title">{title}</h1>
      {actions}
    </div>
  ),
}));

import { RunResultCard, type RunResult } from "@/components/chat/RunResultCard";
import { AgentRunReceipt, runModelName } from "@/components/chat/AgentRunReceipt";
import TemplatesPage from "@/app/templates/page";
import { modelPickWords } from "@/lib/onboarding";

afterEach(() => {
  cleanup();
  api.responses = {};
});

const RESULT: RunResult = {
  found: true,
  session_id: "s1",
  status: "completed",
  task: "find what we decided",
  summary: "",
  steps: 2,
  tools_used: [],
  tools_failed: [],
  files_created: [],
  files_changed: [],
  errors: [],
  revertable: 0,
  duration_s: 12,
  outcome: "completed",
  provider: "claude-cli",
  model: "subscription",
  agent_name: null,
  agent_type: "researcher",
  route_note: "",
  activity: [],
};

describe("who answered a run: the provider for its own default, never 'subscription'", () => {
  it("the run card under the answer says 'answered by Claude Code'; the raw id is the title", () => {
    render(<RunResultCard result={RESULT} />);
    const who = screen.getByTestId("run-result-model");
    expect(who.textContent).toBe("answered by Claude Code");
    expect(who.getAttribute("title")).toBe("Claude Code · subscription");
    expect(screen.getByTestId("run-result-meta").textContent).not.toContain("subscription");
  });

  it("CONTROL: a named model still reads its name ('Opus 5.5'), exactly as before", () => {
    render(<RunResultCard result={{ ...RESULT, model: "claude-opus-5-5" }} />);
    expect(screen.getByTestId("run-result-model").textContent).toBe("answered by Opus 5.5");
  });

  it("the helper-agent row in chat says 'Ran on Claude Code' (or Codex), never the raw id", () => {
    expect(runModelName({ provider: "claude-cli", model: "subscription" })).toBe("Claude Code");
    expect(runModelName({ provider: "codex-cli", model: "subscription" })).toBe("Codex");
    // Unchanged: a named model, the demo, and a run with no model.
    expect(runModelName({ provider: "anthropic", model: "claude-opus-4-8" })).toBe("Opus 4.8");
    expect(runModelName({ provider: "claude-cli", model: "" })).toBe("Claude Code");
    render(<AgentRunReceipt run={{ session: "s1", startedAt: 1, endedAt: 2, steps: 1, provider: "claude-cli", model: "subscription" }} />);
    const row = screen.getByTestId("agent-run-model");
    expect(row.textContent).toContain("Ran on Claude Code");
    expect(row.textContent).not.toContain("subscription");
  });
});

describe("a saved template's model chip names the model the way its picker does", () => {
  it("'Your Claude plan', with the raw pair as the chip's title", async () => {
    api.responses["/templates"] = {
      templates: [
        {
          id: "t1",
          name: "Morning brief",
          agent_type: "builder",
          task: "Summarise the inbox",
          description: "",
          provider: "claude-cli",
          model: "subscription",
          created_at: "2026-10-01T00:00:00Z",
        },
      ],
    };
    api.responses["/templates/starters"] = { starters: [] };
    api.responses["/templates/suggestions"] = { suggestions: [] };
    api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
    api.responses["/models"] = { models: [] };
    render(<TemplatesPage />);
    const chip = await screen.findByTestId("template-model");
    expect(chip.textContent?.trim()).toBe("Your Claude plan");
    expect(chip.getAttribute("title")).toBe("claude-cli · subscription");
    const card = chip.closest("div.rounded-xl") as HTMLElement;
    expect(within(card).queryByText(/subscription/)).toBeNull();
  });
});

describe("the terminal assist's footer and the skill's answer name the model in words", () => {
  const read = (p: string) => readFileSync(join(__dirname, "..", p), "utf8").replace(/\r\n/g, "\n");

  it("both print modelPickWords, the raw pair only in the title (source pin)", () => {
    const pane = read("components/terminal/TerminalPane.tsx");
    expect(pane).not.toContain("{aiResult.provider} · {aiResult.model}");
    expect(pane).toContain("modelPickWords(aiResult)");
    expect(pane).toContain("title={modelPickTitle(aiResult)}");
    const skills = read("app/skills/page.tsx");
    expect(skills).not.toContain("{applyResult.provider} · {applyResult.model}");
    expect(skills).toContain("modelPickWords(applyResult)");
    expect(skills).toContain("title={modelPickTitle(applyResult)}");
  });

  it("the words those surfaces now print (the real helper)", () => {
    expect(modelPickWords({ provider: "claude-cli", model: "subscription" })).toBe("Your Claude plan");
    expect(modelPickWords({ provider: "mock", model: "mock-1" })).not.toMatch(/mock/);
    expect(modelPickWords({ provider: "anthropic", model: "claude-opus-4-8" })).not.toContain("claude-");
  });
});

describe("anti-vacuity", () => {
  it("the old words were really there: friendlyModelName alone keeps 'subscription'", async () => {
    const { friendlyModelName } = await import("@/lib/friendlyModelName");
    expect(friendlyModelName("subscription")).toBe("subscription");
  });
});
