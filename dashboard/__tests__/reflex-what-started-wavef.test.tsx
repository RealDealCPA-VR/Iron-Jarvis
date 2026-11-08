import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * Readiness 0j.5 item 15 — a rule's Test and its last fire say WHAT STARTED,
 * in plain words, with a link to it — never "Fired session · session <id>".
 *
 * The daemon's POST /reflex/rules/{id}/test answers `words` + `href` (and a
 * rule row carries `last_result` in words + `last_href`); the page shows the
 * words and links the place. An older daemon (no `words`) keeps the old line.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    testAnswer: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    return Promise.resolve(r);
  },
  post: (path: string) =>
    path.endsWith("/test") ? Promise.resolve(api.testAnswer) : Promise.resolve({ ok: true }),
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
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
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

import ReflexPage from "@/app/reflex/page";

afterEach(() => {
  cleanup();
  api.responses = {};
  api.testAnswer = {};
});

function seed(rules: unknown[]) {
  api.responses["/reflex/rules"] = { rules };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/agents/remote"] = { agents: [] };
  api.responses["/webhooks"] = { webhooks: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/triggers"] = {};
}

function rule(over: Record<string, unknown> = {}) {
  return {
    id: "reflex_1",
    name: "on-mail",
    source: "webhook",
    match: "gh",
    action: "session",
    target: "",
    task_template: "",
    project_id: null,
    enabled: true,
    created_at: "2026-11-05T10:00:00Z",
    last_fired_at: null,
    fire_count: 0,
    last_error: null,
    last_result: null,
    ...over,
  };
}

describe("Rules: what a fire started", () => {
  it("the Test answer says what started and links it, never the session id", async () => {
    seed([rule()]);
    api.testAnswer = {
      rule_id: "reflex_1",
      ok: true,
      kind: "session",
      session_id: "session_abc123",
      words: "Started an agent run.",
      href: "/sessions/session_abc123",
    };
    render(<ReflexPage />);
    fireEvent.click(await screen.findByTitle("Try this rule now with a test signal"));
    const line = await screen.findByTestId("reflex-test-result-reflex_1");
    expect(line.textContent).toContain("Started an agent run.");
    expect(line.textContent).not.toContain("session_abc123");
    expect(line.textContent).not.toMatch(/Fired session/);
    const link = line.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/sessions/session_abc123");
    expect(link?.textContent).toBe("Open it");
  });

  it("an address that is not one of the app's pages is never linked", async () => {
    seed([rule()]);
    api.testAnswer = {
      ok: true, kind: "session", words: "Started an agent run.", href: "https://evil.example/x",
    };
    render(<ReflexPage />);
    fireEvent.click(await screen.findByTitle("Try this rule now with a test signal"));
    const line = await screen.findByTestId("reflex-test-result-reflex_1");
    expect(line.querySelector("a")).toBeNull();
  });

  it("the row's last fire says what it started and links it", async () => {
    seed([
      rule({
        fire_count: 2,
        last_fired_at: "2026-11-08T09:00:00Z",
        last_result: "Started the workflow “nightly-close”.",
        last_href: "/workflows",
      }),
    ]);
    render(<ReflexPage />);
    const last = await screen.findByTestId("reflex-last-result-reflex_1");
    expect(last.textContent).toBe("Started the workflow “nightly-close”.");
    expect(last.querySelector("a")?.getAttribute("href")).toBe("/workflows");
  });

  it("an older row with no place shows no raw last result", async () => {
    seed([rule({ last_result: "session session_x", fire_count: 1 })]);
    render(<ReflexPage />);
    await screen.findByText("on-mail");
    expect(screen.queryByTestId("reflex-last-result-reflex_1")).toBeNull();
    expect(screen.queryByText(/session session_x/)).toBeNull();
  });
});
