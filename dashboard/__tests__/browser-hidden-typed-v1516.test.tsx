/**
 * v1.516.0 (second review, group 16 part D): the Browser page's approval card
 * says "typed text: hidden" for text typed into a sensitive field.
 *
 * Since v1.515.0 the daemon never keeps such text: the approval row holds a
 * stand-in, `***REDACTED*** (sha256:…)` (`computeruse/approvals.TYPED_MARKER`
 * plus a digest), and the card printed it as it stood:
 * `value="***REDACTED*** (sha256:…)"`. What is stored does not change
 * (`tests/test_second_review_browser_at_rest_v1515.py`); only what a person
 * reads does, in `lib/browserQueueWords.typedValueWords`, which the queue card
 * and a run's trace both use.
 *
 * Driven through the REAL BrowserPage (the browser-approval-copy-v13530
 * harness): only the network, the event socket, the router and the motion
 * wrappers are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  approvals: [] as Record<string, unknown>[],
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
        return { enabled: true, domain_allowlist: ["example.com"], action_allowlist: ["type"] };
      if (path === "/computeruse/approvals") return { approvals: api.approvals };
      if (path.startsWith("/computeruse/runs")) return { runs: [] };
      if (path.startsWith("/computeruse/screen")) return { screen: null, enabled: true };
      if (path === "/health") return { status: "ok", version: "1.516.0", providers: [], default_provider: "mock" };
      return {};
    },
    post: async () => ({}),
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

const BrowserPage = (await import("@/components/settings/pages/BrowserPage")).default;
const { HIDDEN_TYPED_WORDS, TYPED_MARKER, typedValueWords } = await import("@/lib/browserQueueWords");

// The shape the daemon stores (marker, then a short digest); the digest here
// is a plain word, never anything shaped like a real key.
const STAND_IN = `${TYPED_MARKER} (sha256:digest-word)`;

const typingApproval = (value: unknown) => ({
  id: "ap_1",
  run_id: "run_7",
  reason: "Typing into a password field needs your OK.",
  action_json: JSON.stringify({ kind: "type", selector: { css: "#password" }, value, fallback: false }),
  status: "pending",
  created_at: new Date().toISOString(),
});

beforeEach(() => {
  api.approvals = [];
  localStorage.clear();
});
afterEach(() => cleanup());

describe("typedValueWords: the stand-in reads as plain words", () => {
  it("the stored stand-in (marker plus digest) and the bare marker read 'typed text: hidden'", () => {
    expect(HIDDEN_TYPED_WORDS).toBe("typed text: hidden");
    expect(typedValueWords(STAND_IN)).toBe(HIDDEN_TYPED_WORDS);
    expect(typedValueWords(TYPED_MARKER)).toBe(HIDDEN_TYPED_WORDS);
  });

  it("no value is nothing; any other value is quoted as before", () => {
    expect(typedValueWords(undefined)).toBe("");
    expect(typedValueWords(null)).toBe("");
    expect(typedValueWords("")).toBe("");
    expect(typedValueWords("a report")).toBe('value="a report"');
  });

  it("the marker is the daemon's (computeruse/approvals.TYPED_MARKER)", () => {
    expect(TYPED_MARKER).toBe("***REDACTED***");
  });
});

describe("the Browser page's approval card", () => {
  it("shows 'typed text: hidden', never the marker or the digest", async () => {
    api.approvals = [typingApproval(STAND_IN)];
    render(<BrowserPage />);
    await waitFor(() => expect(screen.getByTestId("cu-queue-target").textContent).toBe("the page element #password"));
    const card = screen.getByTestId("cu-queue-target").closest(".card-surface");
    expect(card).not.toBeNull();
    const text = card?.textContent ?? "";
    expect(text).toContain("typed text: hidden");
    expect(text).not.toContain("REDACTED");
    expect(text).not.toContain("sha256");
    expect(text).not.toContain("digest-word");
    expect(text).toContain("css=#password");
  });

  it("anti-vacuity: a click card (no typed text) shows no hidden-text words", async () => {
    api.approvals = [
      {
        ...typingApproval(null),
        action_json: JSON.stringify({ kind: "click", selector: { role: "button", name: "Delete account" }, value: null }),
      },
    ];
    render(<BrowserPage />);
    await waitFor(() => expect(screen.getByTestId("cu-queue-target").textContent).toBe("the “Delete account” button"));
    const card = screen.getByTestId("cu-queue-target").closest(".card-surface");
    expect(card?.textContent ?? "").not.toContain(HIDDEN_TYPED_WORDS);
  });
});
