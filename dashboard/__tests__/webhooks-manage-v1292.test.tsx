/**
 * v1.292.0 (platform-06) — the Webhooks page never says "all" for an empty
 * event list, refuses an outbound add with no event types before it reaches
 * the daemon, and every row has a Remove button that calls DELETE.
 *
 * Before: the form hint read "Leave blank for all events" and the table
 * rendered an empty list as "all", while the daemon delivers only the types
 * listed — the integration silently received nothing. And no row could be
 * removed from the app at all.
 *
 * Pinned here (the real page, only the API module mocked):
 *  - the form's hint and the table carry no "all" wording;
 *  - an outbound row with no types says it sends nothing;
 *  - submitting an outbound webhook with a blank Event types box shows the
 *    daemon's sentence and posts NOTHING;
 *  - Remove is the existing two-press confirm: the first press arms, the
 *    second calls DELETE /webhooks/<slug> and the list reloads.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    getCalls: [] as string[],
    post: vi.fn(async () => ({ ok: true })),
    del: vi.fn(async () => ({ ok: true })),
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.getCalls.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    return Promise.resolve(r);
  },
  post: api.post,
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: api.del,
}));

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

import WebhooksPage from "@/app/webhooks/page";

afterEach(() => {
  cleanup();
  api.responses = {};
  api.getCalls = [];
  api.post.mockClear();
  api.del.mockClear();
});

function seed(webhooks: unknown[]) {
  api.responses["/webhooks"] = { webhooks };
}

const INBOUND = { slug: "gh", direction: "inbound", target_url: null, event_types_json: "[]", enabled: true };
const OUTBOUND_EMPTY = {
  slug: "zap",
  direction: "outbound",
  target_url: "https://example.com/hook",
  event_types_json: "[]",
  enabled: true,
};
const OUTBOUND_TYPED = {
  slug: "typed",
  direction: "outbound",
  target_url: "https://example.com/typed",
  event_types_json: '["session.completed"]',
  enabled: true,
};

describe("Webhooks page: no 'all' promise for an empty event list", () => {
  it("renders an outbound row with no types as sending nothing, never 'all'", async () => {
    seed([INBOUND, OUTBOUND_EMPTY, OUTBOUND_TYPED]);
    render(<WebhooksPage />);
    const zapRow = (await screen.findByText("zap")).closest("tr") as HTMLElement;
    expect(within(zapRow).getByText(/none — nothing is sent/)).toBeTruthy();
    // The table never says "all" anywhere.
    const table = screen.getByRole("table");
    expect(table.textContent).not.toMatch(/\ball\b/);
    // A typed row still lists its types as chips.
    const typedRow = screen.getByText("typed").closest("tr") as HTMLElement;
    expect(within(typedRow).getByText("session.completed")).toBeTruthy();
  });

  it("the outbound form hint says to pick at least one type, not 'leave blank for all'", async () => {
    seed([]);
    render(<WebhooksPage />);
    fireEvent.click(screen.getByRole("button", { name: /add webhook/i }));
    fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "outbound" } });
    const hint = await screen.findByText(/pick at least one/i);
    expect(hint.textContent).not.toMatch(/all events/i);
    expect(hint.textContent).not.toMatch(/leave blank/i);
    expect(document.body.textContent).not.toMatch(/leave blank for all/i);
  });

  it("refuses to submit an outbound webhook with a blank Event types box and posts nothing", async () => {
    seed([]);
    render(<WebhooksPage />);
    fireEvent.click(screen.getByRole("button", { name: /add webhook/i }));
    fireEvent.change(screen.getByPlaceholderText("github-push"), { target: { value: "zap" } });
    fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "outbound" } });
    fireEvent.change(screen.getByPlaceholderText("https://example.com/hook"), {
      target: { value: "https://example.com/hook" },
    });
    const submit = screen.getAllByRole("button", { name: /add webhook/i }).find((b) => b.getAttribute("type") === "submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await screen.findByText(/Pick at least one event type for an outbound webhook/);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("still posts an outbound webhook that lists a type", async () => {
    seed([]);
    render(<WebhooksPage />);
    fireEvent.click(screen.getByRole("button", { name: /add webhook/i }));
    fireEvent.change(screen.getByPlaceholderText("github-push"), { target: { value: "zap" } });
    fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "outbound" } });
    fireEvent.change(screen.getByPlaceholderText("https://example.com/hook"), {
      target: { value: "https://example.com/hook" },
    });
    fireEvent.change(screen.getByPlaceholderText("session.completed, workflow.completed"), {
      target: { value: "session.completed" },
    });
    const submit = screen.getAllByRole("button", { name: /add webhook/i }).find((b) => b.getAttribute("type") === "submit") as HTMLButtonElement;
    fireEvent.click(submit);
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const call = api.post.mock.calls[0] as unknown[];
    expect(call[0]).toBe("/webhooks");
    expect(call[1]).toMatchObject({
      slug: "zap",
      direction: "outbound",
      event_types: ["session.completed"],
    });
  });
});

describe("Webhooks page: Remove", () => {
  it("every row has a Remove button; arm then confirm calls DELETE and reloads the list", async () => {
    seed([INBOUND, OUTBOUND_TYPED]);
    render(<WebhooksPage />);
    await screen.findByText("gh");
    const removes = screen.getAllByTitle(/remove webhook/i);
    expect(removes).toHaveLength(2);

    const ghRow = screen.getByText("gh").closest("tr") as HTMLElement;
    const btn = within(ghRow).getByTitle("Remove webhook gh");
    expect(btn.textContent).toBe("Remove");
    // First press only ARMS (the existing ConfirmButton pattern).
    fireEvent.click(btn);
    expect(api.del).not.toHaveBeenCalled();
    expect(btn.textContent).toBe("Confirm?");
    // Second press deletes and reloads.
    const getsBefore = api.getCalls.filter((p) => p === "/webhooks").length;
    fireEvent.click(btn);
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/webhooks/gh"));
    await waitFor(() =>
      expect(api.getCalls.filter((p) => p === "/webhooks").length).toBeGreaterThan(getsBefore),
    );
  });

  it("shows the daemon's refusal on the list when DELETE fails", async () => {
    seed([INBOUND]);
    api.del.mockRejectedValueOnce(new api.FakeApiError("no webhook named 'gh'", 404));
    render(<WebhooksPage />);
    await screen.findByText("gh");
    const btn = screen.getByTitle("Remove webhook gh");
    fireEvent.click(btn);
    fireEvent.click(btn);
    await screen.findByText(/no webhook named 'gh'/);
  });
});
