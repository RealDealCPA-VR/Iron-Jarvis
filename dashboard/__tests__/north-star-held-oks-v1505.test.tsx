/**
 * v1.505.0 (north-star 5.2, decision D10): a schedule or a workflow is
 * approved ONCE, and the OK is visible where it lives.
 *
 * Pinned here:
 *  - the approval card says WHERE "Always" keeps the grant when the daemon
 *    names the place (`always_scope`): "Always for this schedule" / "Always
 *    for this workflow", and the note names the page with its Remove press;
 *    without it the card keeps the v1.299.0 words (the v1417 DOM fixture test
 *    pins that card byte for byte);
 *  - a schedule's row on the Schedules page and a saved workflow's row on the
 *    Workflows page list their standing OKs, each with a two-press Remove
 *    that POSTs /grants/{id}/revoke and re-reads the list;
 *  - a row with none (or an older daemon's row) shows nothing.
 */
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
    calls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({});
  },
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
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import SchedulesPage from "@/app/schedules/page";
import { ApprovalCard, type ApprovalAsk } from "@/components/chat/ApprovalCard";
import { HeldOks, heldOksOf } from "@/components/workflow/HeldOks";
import { SavedWorkflows } from "@/components/workflow/SavedWorkflows";
import {
  ALWAYS_EXACT,
  ALWAYS_SCHEDULE,
  ALWAYS_WORKFLOW,
  alwaysLabel,
  alwaysScopeOf,
} from "@/lib/approvalWords";
import type { Schedule } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.posts = [];
  api.responses = {};
});

const GRANT = {
  id: "grant_rent1",
  scope_kind: "schedule",
  scope_id: "rent-list",
  tool: "shell",
  args_hash: "a".repeat(64),
  exact: true,
  label: 'shell {"command": "git status"}',
  created_by: "user",
  created_at: "2026-11-15T08:00:00+00:00",
  expires_at: "2030-01-01T00:00:00+00:00",
  revoked_at: null,
  uses: 2,
  last_used_at: null,
  live: true,
};

const ASK: ApprovalAsk = {
  id: "ap1",
  callId: "",
  tool: "shell",
  args: { command: "git status" },
  canAlways: true,
  argsHash: "a".repeat(64),
} as ApprovalAsk;

describe("the card says where Always keeps the grant", () => {
  it("names the schedule, and the note names the page with its Remove", async () => {
    render(<ApprovalCard approval={{ ...ASK, alwaysScope: "schedule" }} scope="run" />);
    const btn = screen.getByTestId("approval-always");
    expect(btn).toHaveTextContent(ALWAYS_SCHEDULE);
    expect(btn).toHaveTextContent("Always for this schedule");
    const note = screen.getByTestId("approval-always-note");
    expect(note.textContent).toContain("on this schedule for 30 days");
    expect(note.textContent).toContain("Remove it any time on the Schedules page.");
    fireEvent.click(btn);
    await waitFor(() => expect(api.posts).toHaveLength(1));
    // The answer posted is still the daemon's "always" (a word renamed, never a value).
    expect(api.posts[0]).toEqual({ path: "/chat/approvals/ap1", body: { decision: "always" } });
  });

  it("names the workflow", () => {
    render(<ApprovalCard approval={{ ...ASK, alwaysScope: "workflow" }} scope="run" />);
    expect(screen.getByTestId("approval-always")).toHaveTextContent(ALWAYS_WORKFLOW);
    expect(screen.getByTestId("approval-always-note").textContent).toContain("on the Workflows page");
  });

  it("without a place (or an unknown one) keeps the v1.299.0 words", () => {
    render(<ApprovalCard approval={{ ...ASK, alwaysScope: "goal" }} scope="run" />);
    expect(screen.getByTestId("approval-always")).toHaveTextContent(ALWAYS_EXACT);
    expect(screen.getByTestId("approval-always-note").textContent).toContain("Revoke");
    expect(alwaysLabel(undefined)).toBe(ALWAYS_EXACT);
    expect(alwaysScopeOf("chat")).toBeNull();
  });
});

describe("a holder's row lists its OKs with Remove", () => {
  it("HeldOks shows each OK and Remove (two presses) revokes it, then re-reads", async () => {
    const removed = vi.fn();
    render(<HeldOks grants={[GRANT]} place="schedule" name="rent-list" onRemoved={removed} />);
    const box = screen.getByTestId("held-oks-rent-list");
    expect(box).toHaveTextContent("Allowed without asking on this schedule:");
    expect(within(box).getByTestId("held-ok-grant_rent1")).toHaveTextContent('shell {"command": "git status"}');
    const remove = screen.getByTestId("held-ok-remove-grant_rent1");
    expect(remove).toHaveTextContent("Remove");
    fireEvent.click(remove);
    expect(api.posts).toHaveLength(0); // the first press only arms it
    fireEvent.click(screen.getByTestId("held-ok-remove-grant_rent1"));
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].path).toBe("/grants/grant_rent1/revoke");
    await waitFor(() => expect(removed).toHaveBeenCalledTimes(1));
  });

  it("nothing held, or an older daemon's row, shows nothing", () => {
    const { container } = render(<HeldOks grants={undefined} place="workflow" name="w" />);
    expect(container.textContent).toBe("");
    expect(heldOksOf([{ nope: 1 }, null, "x"])).toEqual([]);
  });

  it("the Schedules page shows a schedule's OK on its row", async () => {
    const row = {
      name: "rent-list",
      cron: "0 8 * * 1",
      kind: "task",
      enabled: true,
      next_run: "2030-01-06T08:00:00Z",
      last_run: null,
      trigger_type: "cron",
      payload_json: JSON.stringify({ task: "Build the rent list." }),
      skills: [],
      workspace_root: "",
      context_from: "",
      script: null,
      skip_memory: false,
      standing_grants: [GRANT],
    } as unknown as Schedule;
    api.responses["/schedules"] = { schedules: [row] };
    api.responses["/workflows"] = { workflows: [] };
    api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
    api.responses["/projects"] = { projects: [] };
    api.responses["/comm/channels"] = { channels: [] };
    api.responses["/autonomy"] = { kill_switch: false };
    render(<SchedulesPage />);
    const box = await screen.findByTestId("held-oks-rent-list");
    expect(box).toHaveTextContent("on this schedule");
    const reads = api.calls.filter((p) => p === "/schedules").length;
    fireEvent.click(within(box).getByTestId("held-ok-remove-grant_rent1"));
    fireEvent.click(within(box).getByTestId("held-ok-remove-grant_rent1"));
    await waitFor(() => expect(api.posts.map((p) => p.path)).toContain("/grants/grant_rent1/revoke"));
    await waitFor(() => expect(api.calls.filter((p) => p === "/schedules").length).toBeGreaterThan(reads));
  });

  it("a saved workflow's row shows its OK", async () => {
    api.responses["/workflows"] = {
      workflows: [
        {
          id: "wf1",
          name: "Monthly close",
          steps_json: "[]",
          standing_grants: [{ ...GRANT, id: "grant_wf1", scope_kind: "workflow", scope_id: "Monthly close" }],
        },
        { id: "wf2", name: "Plain", steps_json: "[]" },
      ],
    };
    render(<SavedWorkflows />);
    const box = await screen.findByTestId("held-oks-Monthly close");
    expect(box).toHaveTextContent("Allowed without asking on this workflow:");
    expect(screen.queryByTestId("held-oks-Plain")).toBeNull();
  });
});
