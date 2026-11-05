/**
 * v1.433.0 (Wave 1, W1) — the Webhooks page shows the daemon's refusal of an
 * event name Iron Jarvis never sends.
 *
 * Delivery matches an event's type exactly, so "sesion.completed" used to save
 * and then never fire. POST /webhooks now answers 400 with one sentence that
 * names the closest real names; the page must show that sentence where the
 * user is (the form's error line), keep the form filled, and not claim the
 * webhook was added.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const REFUSAL =
  "Iron Jarvis never sends an event called “sesion.completed”, so this webhook would never be sent it. Did you mean session.completed?";

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
    post: vi.fn(),
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    return r === undefined ? Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0)) : Promise.resolve(r);
  },
  post: api.post,
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
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
  api.post.mockReset();
});

function fillOutbound(types: string) {
  fireEvent.click(screen.getByRole("button", { name: /add webhook/i }));
  fireEvent.change(screen.getByPlaceholderText("github-push"), { target: { value: "zap" } });
  fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "outbound" } });
  fireEvent.change(screen.getByPlaceholderText("https://example.com/hook"), {
    target: { value: "https://example.com/hook" },
  });
  fireEvent.change(screen.getByPlaceholderText("session.completed, workflow.completed"), {
    target: { value: types },
  });
  const submit = screen
    .getAllByRole("button", { name: /add webhook/i })
    .find((b) => b.getAttribute("type") === "submit") as HTMLButtonElement;
  fireEvent.click(submit);
}

describe("Webhooks page: an unknown event name", () => {
  it("shows the daemon's sentence, keeps the form filled and does not say added", async () => {
    api.responses["/webhooks"] = { webhooks: [] };
    api.post.mockRejectedValue(new api.FakeApiError(REFUSAL, 400));
    render(<WebhooksPage />);
    fillOutbound("sesion.completed");
    expect(await screen.findByText(REFUSAL)).toBeTruthy();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect((screen.getByPlaceholderText("session.completed, workflow.completed") as HTMLInputElement).value).toBe(
      "sesion.completed",
    );
    expect(document.body.textContent).not.toMatch(/added\. Iron Jarvis will tell that app/);
  });

  it("CONTROL: a real name is added", async () => {
    api.responses["/webhooks"] = { webhooks: [] };
    api.post.mockResolvedValue({ ok: true });
    render(<WebhooksPage />);
    fillOutbound("session.completed");
    expect(await screen.findByText(/Webhook "zap" added/)).toBeTruthy();
    expect(screen.queryByText(REFUSAL)).toBeNull();
  });
});
