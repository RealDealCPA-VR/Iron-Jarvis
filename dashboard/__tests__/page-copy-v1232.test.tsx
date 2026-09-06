/**
 * v1.232.0 (audit Wave 6, task 6A) — page copy and states.
 *
 * The usability drive read every page as the user does and found engineer's
 * words where the user stands. Each block below pins one fix:
 *  - U6  Usage: the mock provider and zero-token rows are not "models" (page
 *        guard; the daemon filters too — tests/test_usage_copy_v1232.py).
 *  - U7  Updates: a browser without the desktop bridge (the phone path) is
 *        told updates install from the desktop app, not "run from a clone".
 *  - U8  Chat: a hint line under "Auto tools" and "Web & research", a title
 *        on the approval-posture select, the footer names the default model,
 *        and the receipt's cap wording says whose cap it is.
 *  - U9  Sessions: live-activity rows carry the stepLabel words, "Traces"
 *        is "Model calls", Time-travel has a one-line hint, Prune is plain.
 *  - U11 Fleet: "unknown" reads "not detected yet".
 *  - U12 Activity: tiles say "in this view"; a decision row's second line is
 *        the event's words, not the type twice.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/* ---- api seam ------------------------------------------------------------ */
const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { FakeApiError };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  wsUrl: (p: string) => `ws://api.test${p}`,
  sseUrl: (p: string) => `http://api.test${p}`,
  get: () => Promise.resolve({}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  api: () => Promise.resolve({}),
}));

const hooks = vi.hoisted(() => ({
  byPath: {} as Record<string, unknown>,
}));
vi.mock("@/lib/useApi", () => {
  const answer = (path: string | null) => ({
    data: path ? (hooks.byPath[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  });
  return { useApi: answer, usePolledApi: answer };
});

const src = (rel: string) => readFileSync(join(process.cwd(), ...rel.split("/")), "utf8");

afterEach(() => {
  cleanup();
  hooks.byPath = {};
});

/* ---- U6 Usage ------------------------------------------------------------ */
describe("U6 — Usage lists models that did work", () => {
  it("drops the mock provider and zero-token rows from the list and the count", async () => {
    const payload = {
      totals: { input_tokens: 100, output_tokens: 10, cost_usd: 4.73, runs: 6 },
      by_day: [{ day: "2026-09-01", input_tokens: 100, output_tokens: 10, cost_usd: 4.73 }],
      by_model: [
        { provider: "custom", model: "brain", input_tokens: 100, output_tokens: 10, cost_usd: 0, runs: 2 },
        { provider: "mock", model: "mock-1", input_tokens: 0, output_tokens: 0, cost_usd: 0, runs: 2 },
        { provider: "opencode/bogusprov", model: "bogusmodel", input_tokens: 0, output_tokens: 0, cost_usd: 0, runs: 1 },
      ],
    };
    hooks.byPath["/usage?days=30"] = payload;
    hooks.byPath["/usage?days=365"] = payload;
    const { default: UsagePage } = await import("@/app/usage/page");
    render(<UsagePage />);
    expect(screen.getByText("Across 1 model")).toBeInTheDocument();
    expect(screen.getByText(/custom · brain/)).toBeInTheDocument();
    expect(screen.queryByText(/mock · mock-1/)).toBeNull();
    expect(screen.queryByText(/bogusmodel/)).toBeNull();
  });
});

/* ---- U7 Updates ---------------------------------------------------------- */
describe("U7 — Updates without the desktop bridge", () => {
  it("tells a browser/phone viewer that updates install from the desktop app", async () => {
    hooks.byPath["/update/check"] = {
      available: false,
      reason: "not a source checkout (installed package)",
    };
    delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
    const { default: UpdatesPage } = await import("@/app/updates/page");
    render(<UpdatesPage />);
    expect(
      screen.getByText(/Updates install from the desktop app on your PC \(tray → Restart to update\)/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Run it from a clone of the repo \(uv\)/)).toBeNull();
    expect(screen.queryByText("Not a source checkout")).toBeNull();
  });
});

/* ---- U8 Chat ------------------------------------------------------------- */
describe("U8 — chat controls explain themselves", () => {
  const chat = src("app/chat/page.tsx");

  it("has one hint line under Web & research and under Auto tools", () => {
    // Each hint sits AFTER its switch's closing tag (the line under it).
    const web = chat.indexOf("Web &amp; research");
    const webHint = chat.indexOf("Lets this chat search the web and read pages.");
    const auto = chat.indexOf("Auto tools\n");
    const autoHint = chat.indexOf(
      "Each request picks the safe tools it needs (files, documents, web, images).",
    );
    expect(web).toBeGreaterThan(-1);
    expect(webHint).toBeGreaterThan(web);
    expect(auto).toBeGreaterThan(webHint);
    expect(autoHint).toBeGreaterThan(auto);
  });

  it("titles the approval-posture select as a posture, with the mode's hint", () => {
    expect(chat).toContain("title={`Approval posture for this chat — ${");
    const sel = chat.indexOf('aria-label="Approval mode"');
    expect(chat.indexOf("Approval posture for this chat", sel)).toBeGreaterThan(sel);
  });

  it("the footer names the default model instead of saying 'default model'", () => {
    expect(chat).toContain("const defaultModelName = useDaemon().health?.default_model ?? \"\";");
    expect(chat).toContain("return defaultModelName ? `default · ${defaultModelName}` : \"default model\";");
    expect(chat).toContain("}, [choice, defaultModelName]);");
  });

  it("the receipt words a tool cap as the local model's cap", async () => {
    const { wordChange, adaptedLabel } = await import("@/components/chat/TurnReceipt");
    expect(wordChange("tool_cap:3")).toBe("capped at 3 tools for this local model");
    expect(adaptedLabel({ model: "brain (RTX)", changes: ["tool_cap:3"] })).toBe(
      "adapted to brain (RTX): capped at 3 tools for this local model",
    );
  });
});

/* ---- U9 Sessions --------------------------------------------------------- */
describe("U9 — sessions speak the user's words", () => {
  it("live-activity rows render the stepLabel words with the raw type on hover", () => {
    const page = src("app/sessions/[id]/page.tsx");
    expect(page).toContain('import { stepLabel } from "@/components/chat/stepLabel";');
    expect(page).toContain("{stepLabel(e) ?? e.type}");
    expect(page).toMatch(/title=\{e\.type\}>\s*\{stepLabel\(e\) \?\? e\.type\}/);
  });

  it("Time-travel keeps its name and gains a one-line hint", () => {
    const page = src("app/sessions/[id]/page.tsx");
    expect(page).toContain('title="Time-travel"');
    expect(page).toContain("every action this run took, newest first — undo what allows it");
    expect(page).not.toContain("replay &amp; undo");
  });

  it("the Traces card is 'Model calls'", async () => {
    hooks.byPath["/sessions/s1/traces"] = { traces: [] };
    const { TracesPanel } = await import("@/components/TracesPanel");
    render(<TracesPanel sessionId="s1" />);
    expect(screen.getByText("Model calls · 0")).toBeInTheDocument();
    expect(screen.getByText("No model calls recorded.")).toBeInTheDocument();
    expect(screen.queryByText(/Traces/)).toBeNull();
  });

  it("the worktree prune is plain words", () => {
    const page = src("app/sessions/page.tsx");
    expect(page).toContain('label="Clean up leftover folders"');
    expect(page).not.toContain("Prune orphaned worktrees");
  });

  it("a session with no review (200 {review: null}) renders no review panel", () => {
    // U14's client half: the daemon now answers 200 {review: null}; the page
    // must read that as "none", not hand a null-shaped object to ReviewPanel.
    const page = src("app/sessions/[id]/page.tsx");
    expect(page).toContain("useApi<Review | { review: null }>(`/sessions/${id}/review`)");
    expect(page).toContain("(reviewRes.data as { review?: unknown }).review !== null");
  });
});

/* ---- U11 Fleet ----------------------------------------------------------- */
describe("U11 — the Fleet badge says 'not detected yet'", () => {
  it("statusLabel and kindLabel word 'unknown' as a state, not a shrug", async () => {
    const { statusLabel, kindLabel } = await import("@/lib/fleet");
    expect(statusLabel("unknown")).toBe("not detected yet");
    expect(statusLabel(null)).toBe("not detected yet");
    expect(statusLabel("online")).toBe("online");
    expect(kindLabel("unknown" as never)).toBe("not detected yet");
    expect(kindLabel(null)).toBe("not detected yet");
    expect(kindLabel("ollama" as never)).toBe("Ollama");
  });
});

/* ---- U12 Activity -------------------------------------------------------- */
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));

describe("U12 — Activity tiles and rows", () => {
  it("the tiles say 'in this view', never '(loaded)'", async () => {
    vi.doMock("@/components/TimeTravelFeed", () => ({
      TimeTravelFeed: ({ onStats }: { onStats?: (s: unknown) => void }) => {
        // In an effect, never during render: setting the parent's state
        // from a child's render is a render loop, not a report.
        useEffect(() => {
          onStats?.({ total: 3, loaded: 3, undoable: 0, inputTokens: 5, outputTokens: 1, costUsd: 0 });
          // eslint-disable-next-line react-hooks/exhaustive-deps
        }, []);
        return null;
      },
    }));
    const { default: ActivityPage } = await import("@/app/activity/page");
    render(<ActivityPage />);
    expect(screen.getByText("Tokens in this view")).toBeInTheDocument();
    expect(screen.getByText("Cost in this view")).toBeInTheDocument();
    expect(screen.queryByText(/\(loaded\)/i)).toBeNull();
    vi.doUnmock("@/components/TimeTravelFeed");
  });

  it("a decision row's second line is the event's words, with the detail kept", async () => {
    const { humanSummary, isEventTypeActor } = await import("@/components/TimeTravelFeed");
    expect(humanSummary("provider.routed brain")).toBe("Picked a model for this turn · brain");
    expect(humanSummary("provider.routed")).toBe("Picked a model for this turn");
    expect(humanSummary("provider.failover claude-cli")).toBe(
      "Switched provider after a failure · claude-cli",
    );
    // An unknown type keeps its summary verbatim (reads oddly, never vanishes).
    expect(humanSummary("weird.new_kind x")).toBe("weird.new_kind x");
    // A summary that is not an event type is untouched.
    expect(humanSummary("wrote report.md")).toBe("wrote report.md");
    // The ledger's fallback actor (the type itself) is not a "by" line.
    expect(isEventTypeActor("provider.routed")).toBe(true);
    expect(isEventTypeActor("schedule:nightly")).toBe(false);
    expect(isEventTypeActor("chat")).toBe(false);
  });

  it("the row wires humanSummary and hides an event-type actor", () => {
    const feed = src("components/TimeTravelFeed.tsx");
    expect(feed).toContain("{humanSummary(e.summary)}");
    expect(feed).toContain("{e.actor && !isEventTypeActor(e.actor) && (");
  });
});
