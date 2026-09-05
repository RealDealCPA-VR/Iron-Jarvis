import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

/**
 * v1.231.0 (audit Wave 5, AE6/AE7) — a rule or watcher that could not do its
 * job says so ON ITS ROW.
 *
 *  - Reflexes: a rule whose last matched signal could not start (its workflow
 *    was deleted) renders `could not start: <reason>` on the row; a healthy
 *    rule renders no such text.
 *  - Sentinels: a watcher whose folder vanished renders the daemon's
 *    `last_error` ("root unreachable since <t>") under Last checked; a
 *    healthy watcher renders nothing there.
 *
 * Both fields are additive on the API; the pages must render them, or the
 * daemon's honesty never reaches the user (the Reliability-wave lesson).
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
  post: () => Promise.resolve({ ok: true }),
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
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
import SentinelsPage from "@/app/sentinels/page";

afterEach(() => {
  cleanup();
  api.responses = {};
});

function seedReflex(rules: unknown[]) {
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
    name: "on-push",
    source: "webhook",
    match: "gh",
    action: "workflow",
    target: "nightly-close",
    task_template: "",
    project_id: null,
    enabled: true,
    created_at: "2026-09-05T10:00:00Z",
    last_fired_at: null,
    fire_count: 0,
    last_error: null,
    last_result: null,
    ...over,
  };
}

function seedSentinels(sentinels: unknown[]) {
  api.responses["/sentinels"] = { enabled: true, sentinels };
  api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
}

function sentinel(over: Record<string, unknown> = {}) {
  return {
    id: "sentinel_1",
    name: "intake",
    kind: "file",
    config: { path: "E:\\intake" },
    task: "triage new intake scans",
    agent_type: "builder",
    risk: "low",
    enabled: true,
    last_checked_at: "2026-09-05T10:00:00Z",
    last_error: null,
    created_at: "2026-09-01T10:00:00Z",
    ...over,
  };
}

describe("Reflexes page renders a rule's last_error on its row", () => {
  it("shows 'could not start: <reason>' when the daemon reports one", async () => {
    seedReflex([rule({ last_error: "no saved workflow 'nightly-close'" })]);
    render(<ReflexPage />);
    const note = await screen.findByTestId("reflex-last-error-reflex_1");
    expect(note.textContent).toBe("could not start: no saved workflow 'nightly-close'");
  });

  it("renders nothing for a healthy rule", async () => {
    seedReflex([rule({ last_error: null, fire_count: 3 })]);
    render(<ReflexPage />);
    await screen.findByText("on-push");
    expect(screen.queryByTestId("reflex-last-error-reflex_1")).toBeNull();
    expect(screen.queryByText(/could not start/)).toBeNull();
  });
});

describe("Sentinels page renders a watcher's last_error on its row", () => {
  it("shows 'root unreachable since <t>' when the folder is gone", async () => {
    seedSentinels([
      sentinel({ last_error: "root unreachable since 2026-09-05T11:02:00+00:00" }),
    ]);
    render(<SentinelsPage />);
    const note = await screen.findByTestId("sentinel-last-error-sentinel_1");
    expect(note.textContent).toBe("root unreachable since 2026-09-05T11:02:00+00:00");
  });

  it("renders nothing for a healthy watcher", async () => {
    seedSentinels([sentinel()]);
    render(<SentinelsPage />);
    await screen.findByText("intake");
    expect(screen.queryByTestId("sentinel-last-error-sentinel_1")).toBeNull();
    expect(screen.queryByText(/root unreachable/)).toBeNull();
  });
});
