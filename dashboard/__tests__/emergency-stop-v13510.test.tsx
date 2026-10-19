/**
 * v1.351.0 lane 1A — Emergency stop says what it really stops (readiness
 * audit G13).
 *
 * The switch used to flash "Kill switch engaged — all autonomy halted." while
 * only the motivation loop read it. The daemon now refuses every unattended
 * door and cancels what they had running, and answers `stopped` (how many):
 *  - the flash names what will not start and the daemon's count;
 *  - with nothing running it does not invent a count;
 *  - the engaged banner says chat and the user's own work keep going;
 *  - the button keeps its name, its look and its one press (pinned by the
 *    v1.316.0 file; only the words changed).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  wsUrl: (p: string) => `ws://api.test${p}`,
  api: () => Promise.resolve({}),
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    return Promise.resolve(r === undefined ? {} : r);
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import AutonomyPage from "@/app/autonomy/page";

function prime(kill = false) {
  api.responses["/autonomy"] = {
    enabled: true,
    level: "suggest",
    dry_run: false,
    kill_switch: kill,
    tick_seconds: 900,
    max_actions_per_day: 20,
    max_tokens_per_day: 200000,
    used_actions_24h: 0,
    used_tokens_24h: 0,
    active_goals: 0,
    pending_proposals: 0,
  };
  api.responses["/goals"] = { goals: [] };
  api.responses["/goals/digest?hours=24"] = { digest: { goals: [], since: "2026-10-18T09:00:00Z" } };
  api.responses["/autonomy/goals"] = { goals: [] };
  api.responses["/proposals?status=pending"] = { proposals: [] };
  api.responses["/autonomy/briefing"] = {
    text: "quiet morning",
    active_goals: 0,
    recent_actions: 0,
    pending_proposals: 0,
    pushed: null,
  };
  api.responses["/grants?live=1"] = { grants: [] };
}

beforeEach(() => {
  window.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
});

async function pressKill() {
  const btn = await screen.findByRole("button", { name: /^\s*Kill switch\s*$/ });
  await waitFor(() => expect(btn).toBeEnabled());
  fireEvent.click(btn);
}

describe("Emergency stop says what it stopped", () => {
  it("names what will not start and the daemon's count of stopped runs", async () => {
    prime();
    api.postResponses["/autonomy/kill"] = { kill_switch: true, stopped: 2 };
    render(<AutonomyPage />);
    await pressKill();
    expect(
      await screen.findByText(
        "Kill switch engaged — schedules, goals, rules, phone tasks and autonomy will not start. Stopped 2 running tasks.",
      ),
    ).toBeInTheDocument();
    expect(api.posts).toContainEqual({ path: "/autonomy/kill", body: { enabled: true } });
    expect(screen.queryByText(/all autonomy halted/)).toBeNull();
  });

  it("with nothing running it invents no count", async () => {
    prime();
    api.postResponses["/autonomy/kill"] = { kill_switch: true, stopped: 0 };
    render(<AutonomyPage />);
    await pressKill();
    expect(
      await screen.findByText(
        "Kill switch engaged — schedules, goals, rules, phone tasks and autonomy will not start.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Stopped \d/)).toBeNull();
  });

  it("an older daemon's answer (no count) reads the same as nothing stopped", async () => {
    prime();
    api.postResponses["/autonomy/kill"] = { kill_switch: true };
    render(<AutonomyPage />);
    await pressKill();
    expect(
      await screen.findByText(
        "Kill switch engaged — schedules, goals, rules, phone tasks and autonomy will not start.",
      ),
    ).toBeInTheDocument();
  });

  it("the engaged banner says chat and the user's own work keep going", async () => {
    prime(true);
    render(<AutonomyPage />);
    expect(await screen.findByRole("button", { name: /Release kill switch/ })).toBeInTheDocument();
    const scope = screen.getByTestId("kill-switch-scope");
    expect(scope.textContent).toMatch(/Schedules, goals, rules, phone tasks and autonomy will not start/);
    expect(scope.textContent).toMatch(/Chat and work you start still\s+run/);
  });
});
