/**
 * v1.299.0 — argument-scoped standing grants, the dashboard half.
 *
 * Pinned here:
 *  - ApprovalCard: "Always allow exactly this" exists ONLY when the ask
 *    carries `can_always`, and it POSTs decision "always" to the same
 *    /chat/approvals/{id} route the other three answers use;
 *  - the bell's agent-ask row: same button, same gate, same decision word;
 *  - StandingGrants: lists scope · tool · label · expiry · uses from
 *    GET /grants?live=1; Revoke is TWO presses (the first POSTs nothing) and
 *    the second POSTs /grants/{id}/revoke; a 404 (older daemon) renders
 *    NOTHING — no card, no empty state;
 *  - toActivity: grant.revoked → one /autonomy notification naming the label;
 *    grant.created → null (the user just pressed the button);
 *  - the Autonomy goals section renders EXACT offers from `grant_offers_exact`
 *    ("with exactly <label>") and PATCHes them as `add_exact: [{tool,
 *    args_hash, label}]` — never widened onto `add`, which stays the per-tool
 *    lane — and lists the goal's `standing_grants` under the offers with the
 *    shared two-press Revoke row.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    fetches: [] as { path: string; method?: string; body?: unknown }[],
    responses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  wsUrl: (p: string) => p,
  sseUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  api: (path: string, init?: { method?: string; body?: string }) => {
    api.fetches.push({
      path,
      method: init?.method,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    });
    return Promise.resolve({});
  },
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r ?? {});
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({ ok: true });
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
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
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { NotificationBell, toActivity } from "@/components/NotificationBell";
import { StandingGrants } from "@/components/StandingGrants";
import AutonomyPage from "@/app/autonomy/page";
import type { IJEvent } from "@/lib/types";
import type { StandingGrant } from "@/lib/types";

const read = (p: string) => readFileSync(join(process.cwd(), p), "utf8").replace(/\r\n/g, "\n");

afterEach(() => {
  cleanup();
  api.gets = [];
  api.posts = [];
  api.fetches = [];
  api.responses = {};
  window.localStorage.clear();
});

const ASK = { id: "apr_1", callId: "c1", tool: "shell", args: { command: "git status" } };

/* ------------------------------------------------------------ ApprovalCard */

describe("ApprovalCard — Always allow exactly this (v1.299.0)", () => {
  it("is absent when the ask does not carry can_always (today's card)", () => {
    render(<ApprovalCard approval={ASK} />);
    expect(screen.queryByTestId("approval-always")).toBeNull();
    expect(screen.queryByTestId("approval-always-note")).toBeNull();
    // The three answers of today are still there.
    expect(screen.getByRole("button", { name: /allow once/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /allow for this conversation/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^deny$/i })).toBeEnabled();
  });

  it("appears with can_always and POSTs decision 'always' to the approval route", async () => {
    render(<ApprovalCard approval={{ ...ASK, canAlways: true, argsHash: "sha256:abc" }} />);
    const btn = screen.getByTestId("approval-always");
    expect(btn.textContent).toBe("Always allow exactly this");
    expect(screen.getByTestId("approval-always-note").textContent).toContain("30 days");
    fireEvent.click(btn);
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0]).toEqual({ path: "/chat/approvals/apr_1", body: { decision: "always" } });
    // One decision has a write path: the card disabled itself.
    expect(screen.getByRole("button", { name: /allow once/i })).toBeDisabled();
  });

  it("the stream hook reads can_always/args_hash off the approval frame", () => {
    const src = read("lib/useChatStream.ts");
    expect(src).toContain("canAlways: ev.can_always === true");
    expect(src).toContain("argsHash: ev.args_hash");
  });
});

/* ------------------------------------------------------------------- bell */

const PENDING_PATH = "/chat/approvals/pending";

async function openBell() {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
}

describe("the bell's agent ask — Always allow exactly this (v1.299.0)", () => {
  it("offers the button only when the pending row says can_always, and posts 'always'", async () => {
    api.responses["/computeruse"] = { pending_approvals: 0 };
    api.responses["/diagnostics"] = { pending_reviews: 0 };
    api.responses[PENDING_PATH] = {
      approvals: [
        { id: "apr_a", tool: "write_file", session_id: "s1", requested_at: "2026-10-02T10:00:00", can_always: true },
        { id: "apr_b", tool: "shell", session_id: "s2", requested_at: "2026-10-02T10:00:01" },
      ],
    };
    render(<NotificationBell />);
    await openBell();
    const rows = await screen.findAllByTestId("bell-agent-approval");
    expect(rows).toHaveLength(2);
    const [a, b] = rows;
    const always = within(a).getByTestId("bell-approval-always");
    expect(within(b).queryByTestId("bell-approval-always")).toBeNull();
    fireEvent.click(always);
    await waitFor(() =>
      expect(api.posts).toContainEqual({ path: "/chat/approvals/apr_a", body: { decision: "always" } }),
    );
  });

  it("maps grant.revoked to one /autonomy notification and grant.created to nothing", () => {
    const base = { id: "e1", ts: "2026-10-02T10:00:00", type: "grant.revoked" } as IJEvent;
    const revoked = toActivity({
      ...base,
      payload: { id: "g1", scope_kind: "goal", scope_id: "inbox", tool: "shell", label: "git status in ~/repo" },
    } as IJEvent);
    expect(revoked).not.toBeNull();
    expect(revoked!.href).toBe("/autonomy");
    expect(revoked!.title).toBe("Standing grant revoked: git status in ~/repo");
    const created = toActivity({
      ...base,
      id: "e2",
      type: "grant.created",
      payload: { id: "g1", tool: "shell", label: "git status" },
    } as IJEvent);
    expect(created).toBeNull();
  });
});

/* ---------------------------------------------------------- StandingGrants */

function grant(over: Partial<StandingGrant> = {}): StandingGrant {
  return {
    id: "g1",
    scope_kind: "goal",
    scope_id: "inbox-zero",
    tool: "shell",
    args_hash: "sha256:0123456789abcdef",
    label: "command: git status",
    created_at: "2026-10-01T09:00:00",
    expires_at: new Date(Date.now() + 29.5 * 86_400_000).toISOString(),
    revoked_at: null,
    uses: 4,
    last_used_at: null,
    ...over,
  };
}

describe("StandingGrants (v1.299.0)", () => {
  it("lists scope · tool · label · expiry · uses and revokes on the SECOND press only", async () => {
    api.responses["/grants?live=1"] = {
      grants: [grant(), grant({ id: "g2", scope_kind: "chat", scope_id: "chat", tool: "write_file", args_hash: "", label: "", uses: 1 })],
    };
    render(<StandingGrants />);
    const row = await screen.findByTestId("standing-grant-g1");
    expect(row.textContent).toContain("goal · inbox-zero");
    expect(row.textContent).toContain("shell");
    expect(row.textContent).toContain("command: git status");
    expect(row.textContent).toContain("expires in 29 d");
    expect(row.textContent).toContain("4 uses");
    // A per-tool (any-args) grant says so instead of showing an empty label.
    const row2 = screen.getByTestId("standing-grant-g2");
    expect(row2.textContent).toContain("chat");
    expect(row2.textContent).toContain("any arguments");
    expect(row2.textContent).toContain("1 use");

    const revoke = within(row).getByRole("button", { name: /revoke/i });
    fireEvent.click(revoke);
    // First press ARMS — nothing is sent.
    expect(api.posts).toEqual([]);
    expect(revoke.textContent).toContain("Revoke?");
    const before = api.gets.filter((p) => p === "/grants?live=1").length;
    fireEvent.click(revoke);
    await waitFor(() => expect(api.posts).toEqual([{ path: "/grants/g1/revoke", body: undefined }]));
    // The list re-reads after the revoke lands.
    await waitFor(() =>
      expect(api.gets.filter((p) => p === "/grants?live=1").length).toBeGreaterThan(before),
    );
  });

  it("renders NOTHING on a daemon without the route (404)", async () => {
    api.responses["/grants?live=1"] = new api.FakeApiError("not found", 404);
    const { container } = render(<StandingGrants />);
    await waitFor(() => expect(api.gets).toContain("/grants?live=1"));
    // Settle: the rejected fetch has landed and the component decided.
    await waitFor(() => expect(container.innerHTML).toBe(""));
    expect(screen.queryByTestId("standing-grants")).toBeNull();
    expect(screen.queryByText(/Standing grants/)).toBeNull();
  });
});

/* -------------------------------------------------------- goal exact offer */

describe("Autonomy goals — exact offers and the goal's standing grants (v1.299.0)", () => {
  const exact = { tool: "write_file", args_hash: "sha256:feedface00", label: "path: out/report.md", asked: 3, approved: 3 };
  function mountGoal() {
    api.responses["/goals"] = {
      goals: [
        {
          id: "g1",
          name: "Weekly report",
          contract_text: "Write the weekly report.",
          state: "active",
          budget: { max_dollars: 2 },
          spent: { tokens: 0, dollars: 0, wallclock_s: 0, iterations: 0 },
          verifier: { kind: "manual", checks: [] },
          grant_offers: ["shell"],
          grant_offers_exact: [exact],
          standing_grants: [grant({ id: "sg1", scope_kind: "goal", scope_id: "g1", tool: "read_file", label: "path: in/data.csv", uses: 2 })],
        },
      ],
    };
    api.responses["/goals/g1"] = { goal: { id: "g1", ask_stats: { shell: { asked: 4, approved: 4, denied: 0, timed_out: 0 } } } };
    api.responses["/grants?live=1"] = new api.FakeApiError("not found", 404);
    return render(<AutonomyPage />);
  }

  it("PATCHes add_exact for the exact offer (its own receipts) and add for the per-tool one", async () => {
    mountGoal();
    const exactRow = await screen.findByTestId("grant-offer-g1-write_file-sha256:f");
    expect(exactRow.textContent).toContain("all 3 asks");
    expect(exactRow.textContent).toContain("with exactly");
    expect(exactRow.textContent).toContain("path: out/report.md");
    expect(exactRow.textContent).toContain("always allow exactly this here?");
    const bareRow = screen.getByTestId("grant-offer-g1-shell");
    expect(bareRow.textContent).not.toContain("exactly");

    fireEvent.click(within(exactRow).getByRole("button", { name: /allow exactly this/i }));
    await waitFor(() => expect(api.fetches.some((f) => f.path === "/goals/g1/grants")).toBe(true));
    const first = api.fetches.find((f) => f.path === "/goals/g1/grants")!;
    expect(first.method).toBe("PATCH");
    // The exact lane, and ONLY the wire's keys — never the per-tool `add`.
    expect(first.body).toEqual({
      add_exact: [{ tool: "write_file", args_hash: "sha256:feedface00", label: "path: out/report.md" }],
    });
    expect(first.body).not.toHaveProperty("add");

    await waitFor(() => expect(within(bareRow).getByRole("button", { name: /^allow$/i })).toBeEnabled());
    fireEvent.click(within(bareRow).getByRole("button", { name: /^allow$/i }));
    await waitFor(() => expect(api.fetches.filter((f) => f.path === "/goals/g1/grants")).toHaveLength(2));
    expect(api.fetches.filter((f) => f.path === "/goals/g1/grants")[1].body).toEqual({ add: ["shell"] });
  });

  it("lists the goal's standing grants under the offers and revokes one on the second press", async () => {
    mountGoal();
    await screen.findByTestId("grant-offer-g1-shell");
    const list = screen.getByTestId("goal-grants-g1");
    const row = within(list).getByTestId("standing-grant-sg1");
    expect(row.textContent).toContain("read_file");
    expect(row.textContent).toContain("path: in/data.csv");
    expect(row.textContent).toContain("2 uses");
    expect(row.textContent).not.toContain("goal · g1"); // the goal card already names the scope
    const revoke = within(row).getByRole("button", { name: /revoke/i });
    fireEvent.click(revoke);
    expect(api.posts).toEqual([]);
    fireEvent.click(revoke);
    await waitFor(() => expect(api.posts).toEqual([{ path: "/grants/sg1/revoke", body: undefined }]));
    await screen.findByText(/Revoked "read_file" on "Weekly report"/);
  });
});
