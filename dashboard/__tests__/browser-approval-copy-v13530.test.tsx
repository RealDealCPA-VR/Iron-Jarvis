/**
 * v1.353.0 lane 3D (G54, G71) — the agent browser's words match what
 * happens, and nothing promises desktop control.
 *
 *  G54  A sensitive website action ends the agent's step and files a request
 *       in the Approval queue; approving it resumes NOTHING — only the
 *       agent's next identical call (within 15 minutes) runs, once. The queue
 *       said "it pauses here and waits for you". It now says the step stopped
 *       and to ask the agent to repeat it; after an Approve it says
 *       "Approved. Ask the agent to try that step again." for a few seconds;
 *       and the never-filled "Recent runs" says where steps really are.
 *  G71  Settings › Connections › Browser said "The browser add-on and
 *       computer use" and "control a browser (and, later, the desktop)".
 *
 * Driven through the REAL BrowserPage and SettingsHome sections list; only
 * the network (lib/api), the event socket, the router and the motion
 * wrappers are stubbed (the ux-wave3-build-files-browser harness).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const api = vi.hoisted(() => ({
  posts: [] as { path: string; body: unknown }[],
  approvals: [] as Record<string, unknown>[],
  runs: [] as Record<string, unknown>[],
  failApprove: false,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    wsUrl: (p: string) => p,
    onUnauthorizedChange: () => () => {},
    onRequestErrorChange: () => () => {},
    onNetworkError: () => () => {},
    get: async (path: string) => {
      if (path === "/browser/status") throw new FakeApiError("Not Found", 404);
      if (path === "/computeruse")
        return { enabled: true, domain_allowlist: ["example.com"], action_allowlist: ["click"] };
      if (path === "/computeruse/approvals") return { approvals: api.approvals };
      if (path.startsWith("/computeruse/runs")) return { runs: api.runs };
      if (path.startsWith("/computeruse/screen")) return { screen: null, enabled: true };
      if (path === "/health") return { status: "ok", version: "1.353.0", providers: [], default_provider: "mock" };
      return {};
    },
    post: async (path: string, body?: unknown) => {
      api.posts.push({ path, body });
      if (api.failApprove && path.endsWith("/approve")) throw new FakeApiError("already answered", 409);
      // An answered request leaves the queue (the daemon's behaviour).
      if (path.startsWith("/computeruse/approvals/")) api.approvals = [];
      return {};
    },
    put: async () => ({}),
    patch: async () => ({}),
    del: async () => ({}),
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/settings",
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const BrowserPageModule = await import("@/components/settings/pages/BrowserPage");
const BrowserPage = BrowserPageModule.default;
const {
  RUNS_EMPTY_SENTENCE,
  WEBSITE_ANSWER_LINGER_MS,
  WEBSITE_APPROVED_SENTENCE,
  WEBSITE_QUEUE_SENTENCE,
} = BrowserPageModule;

const APPROVAL = {
  id: "ap_1",
  run_id: "run_7",
  reason: "Clicking 'Pay now' looks like a payment.",
  action_json: JSON.stringify({ kind: "click", selector: "#pay" }),
  status: "pending",
  created_at: new Date().toISOString(),
};

beforeEach(() => {
  api.posts = [];
  api.approvals = [];
  api.runs = [];
  api.failApprove = false;
  localStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("the Approval queue says what really happens (G54)", () => {
  it("the queue's sentence: the step stopped; approve, then ask the agent to do it again", async () => {
    render(<BrowserPage />);
    const line = await screen.findByText(WEBSITE_QUEUE_SENTENCE);
    expect(line.textContent).toMatch(/its step stops here/);
    expect(line.textContent).toMatch(/ask the agent to do that step again within 15 minutes; it will run once/);
    // The honesty qualifier stays: only sensitive or destructive actions queue.
    expect(line.textContent).toMatch(/sensitive or destructive/);
    // The old promise is gone from the page.
    expect(document.body.textContent ?? "").not.toMatch(/pauses here and waits/);
  });

  it("after Approve: 'Approved. Ask the agent to try that step again.' — for a few seconds, then quiet", async () => {
    api.approvals = [APPROVAL];
    render(<BrowserPage />);
    const approve = await screen.findByRole("button", { name: /Allow once/ });
    // The live region is on the page BEFORE its words change (a screen
    // reader hears the answer).
    const live = screen.getByTestId("cu-answered-note");
    expect(live).toHaveAttribute("role", "status");
    expect(live.textContent).toBe("");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(approve);
    await waitFor(() => expect(screen.getByTestId("cu-answered-note").textContent).toBe(WEBSITE_APPROVED_SENTENCE));
    expect(WEBSITE_APPROVED_SENTENCE).toBe("Approved. Ask the agent to try that step again.");
    expect(api.posts.map((p) => p.path)).toEqual(["/computeruse/approvals/ap_1/approve"]);
    // The answered card leaves the queue; the sentence stays where it was.
    await waitFor(() => expect(screen.queryByRole("button", { name: /Allow once/ })).not.toBeInTheDocument());
    expect(screen.getByTestId("cu-answered-note").textContent).toBe(WEBSITE_APPROVED_SENTENCE);
    await act(async () => {
      vi.advanceTimersByTime(WEBSITE_ANSWER_LINGER_MS + 50);
    });
    await waitFor(() => expect(screen.getByTestId("cu-answered-note").textContent).toBe(""));
  });

  it("after Deny: the app's one 'Denied' sentence", async () => {
    api.approvals = [APPROVAL];
    render(<BrowserPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Deny/ }));
    await waitFor(() =>
      expect(screen.getByTestId("cu-answered-note").textContent).toBe("Denied. That action will not run."),
    );
  });

  it("a refused Approve says why on the card and claims nothing", async () => {
    api.approvals = [APPROVAL];
    api.failApprove = true;
    render(<BrowserPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Allow once/ }));
    expect(await screen.findByText(/already answered/)).toBeInTheDocument();
    expect(screen.getByTestId("cu-answered-note").textContent).toBe("");
  });

  it("Recent runs, empty, says where the agent's steps are — and promises no history it will not fill", async () => {
    render(<BrowserPage />);
    expect(await screen.findByText(RUNS_EMPTY_SENTENCE)).toBeInTheDocument();
    expect(RUNS_EMPTY_SENTENCE).toMatch(/listed in the chat or run that asked for them/);
    expect(document.body.textContent ?? "").not.toMatch(/history appears after/);
    // The how-to-read-the-list line only shows when there is a list.
    expect(screen.queryByText(/Click a run to inspect its recorded trace/)).not.toBeInTheDocument();
  });

  it("anti-vacuity: with runs, the list and its how-to line still render", async () => {
    api.runs = [
      { id: "run_1", task: "Open the pricing page", status: "completed", ok: true, started_at: new Date().toISOString(), finished_at: null },
    ];
    render(<BrowserPage />);
    expect(await screen.findByText("Open the pricing page")).toBeInTheDocument();
    expect(screen.getByText(/Click a run to inspect its recorded trace/)).toBeInTheDocument();
    expect(screen.queryByText(RUNS_EMPTY_SENTENCE)).not.toBeInTheDocument();
  });
});

describe("v1.353.0 integrate: the queue card answers in the app's one approval language", () => {
  it("its two buttons are lib/approvalWords' Allow once / Deny — never 'Approve'", async () => {
    const words = await import("@/lib/approvalWords");
    api.approvals = [APPROVAL];
    render(<BrowserPage />);
    const allow = await screen.findByRole("button", { name: new RegExp(`^\\s*${words.ALLOW_ONCE}\\s*$`) });
    const deny = screen.getByRole("button", { name: new RegExp(`^\\s*${words.DENY}\\s*$`) });
    expect(allow.textContent?.trim()).toBe("Allow once");
    expect(deny.textContent?.trim()).toBe("Deny");
    expect(screen.queryByRole("button", { name: /^\s*Approve\s*$/ })).not.toBeInTheDocument();
    // The queue's sentence names the button the user will press.
    expect(WEBSITE_QUEUE_SENTENCE).toContain(`Press ${words.ALLOW_ONCE}, then ask the agent`);
    // The answered line keeps the bell's past-tense word ("Approved.").
    expect(WEBSITE_APPROVED_SENTENCE.startsWith(words.answeredLine("approved").split(" ")[0])).toBe(true);
    // The safety explainer no longer promises a pause that resumes.
    expect(document.body.textContent ?? "").not.toMatch(/pauses for your explicit/);
    expect(document.body.textContent ?? "").toMatch(/needs your explicit\s+approval first/);
  });
});

describe("computer-control wording says what exists (G71)", () => {
  it("Settings › Connections › Browser does not promise desktop control", async () => {
    render(<BrowserPage />);
    await screen.findByText(WEBSITE_QUEUE_SENTENCE);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/later, the desktop/i);
    expect(text).not.toMatch(/drive a browser or desktop/i);
    expect(text).toMatch(/it does not click around other desktop\s+apps/);
    expect(text).toMatch(/Jarvis can run commands with your OK/);
  });

  it("the Settings section's blurb names what is there: your add-on and the agent browser", () => {
    const src = readFileSync(join(process.cwd(), "components", "settings", "SettingsHome.tsx"), "utf8").replace(
      /\r\n/g,
      "\n",
    );
    const at = src.indexOf('id: "connections-browser"');
    expect(at).toBeGreaterThan(-1);
    const entry = src.slice(at, src.indexOf("Comp:", at));
    expect(entry).toContain('blurb: "Your browser add-on and the agent browser."');
    expect(entry).not.toMatch(/computer use/i);
  });
});
