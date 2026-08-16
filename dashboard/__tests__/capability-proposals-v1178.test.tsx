import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

/**
 * Capability requests (v1.178.0, P4) — the review card's promises.
 *
 * The card answers the failure that made this feature exist: "rename all files
 * in this folder" ran four times and renamed nothing because no rename tool
 * existed, and the agent had no way to SAY so. Everything below is a failure
 * mode that is invisible on a happy path:
 *
 *  - the REASON. The agent's own sentences are what the decision is made on, so
 *    they have to be ON SCREEN verbatim next to what it would be allowed to do.
 *  - the STAKES. Approve is the ONLY thing in the feature that creates
 *    anything, and what it creates is still permission-gated. Both halves must
 *    be in the copy, or the card either understates or overstates the click.
 *  - the ACTIONS. Approve/Reject go through THIS row's id and the queue is
 *    re-read afterwards (a locally-dropped row would hide a request that a
 *    refused approve left pending on the daemon).
 *  - the CALM. An empty queue is the good outcome, not a fault — no alert.
 *  - the DEGRADE. A daemon predating the endpoint has no card at all.
 */

const apiState = vi.hoisted(() => ({
  gets: [] as string[],
  posts: [] as string[],
  /** Reject GET /capability/proposals with this status (404 = older daemon). */
  failStatus: null as number | null,
  payload: {} as Record<string, unknown>,
  /** What POST …/approve answers. */
  approveResult: {} as Record<string, unknown>,
  /** Make one POST path reject, to check the reason is relayed verbatim. */
  postFailure: null as { path: string; message: string } | null,
  /** Hold every POST open until this resolves — the only way to observe the
   *  card WHILE a decision is in flight. */
  postGate: null as Promise<void> | null,
}));

class FakeApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

vi.mock("@/lib/api", () => ({
  ApiError: FakeApiError,
  get: (path: string) => {
    apiState.gets.push(path);
    if (apiState.failStatus !== null) {
      return Promise.reject(new FakeApiError("nope", apiState.failStatus));
    }
    return Promise.resolve(apiState.payload);
  },
  post: async (path: string) => {
    apiState.posts.push(path);
    if (apiState.postGate) await apiState.postGate;
    if (apiState.postFailure && path.includes(apiState.postFailure.path)) {
      throw new FakeApiError(apiState.postFailure.message, 409);
    }
    if (path.endsWith("/approve")) return apiState.approveResult;
    return {};
  },
}));

// Imported AFTER the mock so the component binds to the fake client.
const { ProposalsCard } = await import("@/components/capability/ProposalsCard");

/** The live shape of one `store.proposal_view` row — the rename tool the real
 *  incident wanted, as an agent would have filed it. */
const PROPOSAL = {
  id: "capprop_1",
  kind: "tool",
  kind_label: "a custom tool",
  name: "rename_file",
  rationale:
    "I was asked to rename 26 files and there is no rename tool, so I shelled " +
    "out four times and renamed nothing.",
  scope: "Rename ONE file at a time, inside the folder it is already in.",
  task: "rename 26 files in C:/clients/2025",
  spec: { command: ["cmd", "/c", "ren", "{path}", "{new_name}"] },
  command: ["cmd", "/c", "ren", "{path}", "{new_name}"],
  requested_permission: "ask",
  runs_under: "custom:rename_file",
  status: "pending",
  run_id: "run_ab82dea4bf8a",
  applied: {},
  can_apply: true,
  kind_note:
    "Approving creates it as a custom tool. It will still ask for your approval " +
    "every time it runs (custom:<name>).",
  blocked: "",
  created_at: "2026-08-15T10:00:00Z",
  decided_at: null,
};

function payload(over: Record<string, unknown> = {}) {
  return {
    proposals: [PROPOSAL],
    pending: 1,
    stats: { pending: 1, approved: 0, rejected: 0, total: 1, by_kind: { tool: 1 } },
    ...over,
  };
}

beforeEach(() => {
  apiState.gets = [];
  apiState.posts = [];
  apiState.failStatus = null;
  apiState.payload = payload();
  apiState.approveResult = {
    ...PROPOSAL,
    status: "approved",
    applied: {
      ok: true,
      created: "rename_file",
      permission_key: "custom:rename_file",
      permission_mode: "ask",
    },
  };
  apiState.postFailure = null;
  apiState.postGate = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("a pending request renders with the agent's own reason", () => {
  it("shows what is asked for, of what kind, why, and what it could do", async () => {
    render(<ProposalsCard />);
    await screen.findByText("rename_file");
    // The REASON, verbatim — this is the text the decision is made on.
    expect(
      screen.getByText(/there is no rename tool, so I shelled out four times/),
    ).toBeTruthy();
    // The kind, in the daemon's own plain-language noun.
    expect(screen.getByText("a custom tool")).toBeTruthy();
    // Exactly what it would be ALLOWED to do.
    expect(screen.getByText(/Rename ONE file at a time/)).toBeTruthy();
    // The job that was in hand — without it the ask is unreviewable.
    expect(screen.getByText(/rename 26 files in C:\/clients\/2025/)).toBeTruthy();
    // And the concrete argv, so "rename a file" can't hide a shell.
    expect(screen.getByText("{new_name}")).toBeTruthy();
  });

  it("says approving CREATES, and that what it creates still asks", async () => {
    const { container } = render(<ProposalsCard />);
    await screen.findByText("rename_file");
    // Both halves of the stakes, in the card's own words.
    expect(container.textContent).toMatch(/Asking creates nothing/i);
    expect(container.textContent).toMatch(/approving is what creates it/i);
    expect(container.textContent).toMatch(/permission-gated/i);
    expect(container.textContent).toMatch(/deny floor can never be raised/i);
    // The per-row permission truth: the key it lands on, and that it asks.
    expect(screen.getByText("custom:rename_file")).toBeTruthy();
    expect(container.textContent).toMatch(/asks you every time/i);
  });

  it("shows the mode the agent WANTED without implying it was granted", async () => {
    apiState.payload = payload({
      proposals: [{ ...PROPOSAL, requested_permission: "allow" }],
    });
    render(<ProposalsCard />);
    await screen.findByText(/asked to run this at “allow”/);
    expect(screen.getByText(/Approving does not grant that/)).toBeTruthy();
  });
});

describe("the decision goes through this row's id and re-reads the queue", () => {
  it("approve POSTs the approve route for the right proposal", async () => {
    render(<ProposalsCard />);
    const button = await screen.findByRole("button", { name: /Approve “rename_file”/ });
    await act(async () => {
      button.click();
    });
    await waitFor(() =>
      expect(apiState.posts).toContain("/capability/proposals/capprop_1/approve"),
    );
    // …and the list is re-read, not patched locally (a refused approve leaves
    // the row pending on the daemon, so the local copy would go stale).
    await waitFor(() =>
      expect(apiState.gets.filter((p) => p === "/capability/proposals").length).toBe(2),
    );
  });

  it("reports what was CREATED and the mode read back off the engine", async () => {
    render(<ProposalsCard />);
    const button = await screen.findByRole("button", { name: /Approve “rename_file”/ });
    await act(async () => {
      button.click();
    });
    await screen.findByText(
      /Created “rename_file”\. It runs as custom:rename_file at “ask”/,
    );
  });

  it("reject POSTs the reject route and re-reads the queue", async () => {
    render(<ProposalsCard />);
    const button = await screen.findByRole("button", { name: /Reject “rename_file”/ });
    await act(async () => {
      button.click();
    });
    await waitFor(() =>
      expect(apiState.posts).toContain("/capability/proposals/capprop_1/reject"),
    );
    await waitFor(() =>
      expect(apiState.gets.filter((p) => p === "/capability/proposals").length).toBe(2),
    );
    await screen.findByText(/won't ask for this one again/i);
  });

  it("relays a refused approve verbatim, on the row", async () => {
    apiState.postFailure = {
      path: "/approve",
      message: "“shell” is on the deny floor — ask for a NARROW tool instead.",
    };
    render(<ProposalsCard />);
    const button = await screen.findByRole("button", { name: /Approve “rename_file”/ });
    await act(async () => {
      button.click();
    });
    await screen.findByText(/is on the deny floor — ask for a NARROW tool instead/);
  });
});

describe("honest about what approval cannot do", () => {
  it("disables Approve and explains, before the click, for an MCP ask", async () => {
    apiState.payload = payload({
      proposals: [
        {
          ...PROPOSAL,
          id: "capprop_2",
          kind: "mcp",
          kind_label: "an MCP server",
          name: "postgres",
          runs_under: "",
          can_apply: false,
          kind_note:
            "Iron Jarvis can’t add an MCP server for you: it needs a command and " +
            "credentials only you have.",
        },
      ],
    });
    render(<ProposalsCard />);
    const approve = await screen.findByRole("button", { name: /Approve “postgres”/ });
    expect(approve).toBeDisabled();
    expect(screen.getByText(/can’t add an MCP server for you/)).toBeTruthy();
    // Reject stays live — "I'll handle it myself" must still be sayable.
    expect(screen.getByRole("button", { name: /Reject “postgres”/ })).not.toBeDisabled();
  });

  it("disables Approve and shows the reason for a deny-floor request", async () => {
    apiState.payload = payload({
      proposals: [
        {
          ...PROPOSAL,
          name: "shell",
          blocked:
            "“shell” is on the deny floor — it can never be raised to allow, by an " +
            "agent definition or by an approved proposal.",
        },
      ],
    });
    render(<ProposalsCard />);
    const approve = await screen.findByRole("button", { name: /Approve “shell”/ });
    expect(approve).toBeDisabled();
    expect(screen.getByText(/can never be raised to allow/)).toBeTruthy();
  });
});

describe("an empty queue is calm, and an old daemon has no card", () => {
  it("says there is nothing to review, with no error anywhere", async () => {
    apiState.payload = payload({ proposals: [], pending: 0 });
    render(<ProposalsCard />);
    await screen.findByText(/Nothing to review/i);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
  });

  it("renders nothing at all when /capability/proposals 404s", async () => {
    apiState.failStatus = 404;
    const { container } = render(<ProposalsCard />);
    await waitFor(() => expect(apiState.gets).toContain("/capability/proposals"));
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("a LIVE error is not the same silence — the card says what went wrong", async () => {
    // The other half of the 404 rule, and the reason it can't just be
    // "render nothing on any failure": a daemon that HAS the endpoint and is
    // failing on it must not look identical to one that never had it.
    apiState.failStatus = 500;
    render(<ProposalsCard />);
    await screen.findByRole("alert");
    // …and it must not invent a verdict on a queue it never read.
    expect(screen.queryByText(/Nothing to review/i)).toBeNull();
  });

  it("stays silent while the daemon is simply not running", async () => {
    // status 0 = offline. Every page already reports that once at the top;
    // a second scary box per card is noise, not honesty.
    apiState.failStatus = 0;
    const { container } = render(<ProposalsCard />);
    await waitFor(() => expect(apiState.gets).toContain("/capability/proposals"));
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("renders nothing before the first answer lands", () => {
    const { container } = render(<ProposalsCard />);
    expect(container.textContent).toBe("");
  });

  it("drops the error once the queue reads cleanly again", async () => {
    // REVIEW DEFECT (found by the adversarial pass): `refresh` set `error` on a
    // live failure and never cleared it on the next success — the house hook
    // (lib/useApi) does `setError(null)` there for exactly this reason. The card
    // has NO polling and NO retry button, so one transient 500 during a
    // post-decision re-read pinned a red alert to the card for as long as the
    // user stayed on /tools, over a queue that was reading fine.
    render(<ProposalsCard />);
    const reject = await screen.findByRole("button", { name: /Reject “rename_file”/ });
    apiState.failStatus = 500; // the re-read after the decision fails
    await act(async () => {
      reject.click();
    });
    await screen.findByRole("alert");
    apiState.failStatus = null; // …and the daemon comes back
    await act(async () => {
      (await screen.findByRole("button", { name: /Reject “rename_file”/ })).click();
    });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("locks every row while one decision is in flight", async () => {
    // REVIEW DEFECT: `decide` opens with `if (busy) return`, but only the BUSY
    // row's buttons were disabled. Every other row kept a live-looking Approve
    // that silently did nothing when clicked — a control that swallows a click
    // is worse than a disabled one, because the user reads it as "it worked".
    let release!: () => void;
    apiState.postGate = new Promise<void>((r) => {
      release = r;
    });
    apiState.payload = payload({
      proposals: [PROPOSAL, { ...PROPOSAL, id: "capprop_2", name: "move_file" }],
    });
    render(<ProposalsCard />);
    const approve = await screen.findByRole("button", { name: /Approve “rename_file”/ });
    await act(async () => {
      approve.click();
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Approve “move_file”/ })).toBeDisabled();
      expect(screen.getByRole("button", { name: /Reject “move_file”/ })).toBeDisabled();
    });
    await act(async () => {
      release();
    });
  });

  it("a failed read is recoverable without leaving the page", async () => {
    // REVIEW DEFECT: the card fetches on mount and after a decision, and never
    // polls — so a live error was TERMINAL. With no rows there is no decision to
    // make, so nothing could ever trigger a second read, and an unknown queue
    // sat there looking like a broken card until the user navigated away.
    apiState.failStatus = 500;
    render(<ProposalsCard />);
    const retry = await screen.findByRole("button", { name: /Try again/i });
    apiState.failStatus = null;
    await act(async () => {
      retry.click();
    });
    await waitFor(() => {
      expect(screen.getByText("rename_file")).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });

  it("is actually mounted on the Tools page", () => {
    // The v1.163.0 lesson, as a source pin: every assertion above renders the
    // component directly, so deleting the ONE line that puts it on a page would
    // have left this whole file green. The page is too heavy to mount here —
    // same technique as the v1.172.0 connector-status suite.
    const pageSrc = readFileSync(
      join(__dirname, "..", "app", "tools", "page.tsx"),
      "utf8",
    );
    expect(pageSrc).toMatch(
      /import \{ ProposalsCard \} from "@\/components\/capability\/ProposalsCard"/,
    );
    expect(pageSrc).toMatch(/<ProposalsCard\s*\/>/);
  });
});
