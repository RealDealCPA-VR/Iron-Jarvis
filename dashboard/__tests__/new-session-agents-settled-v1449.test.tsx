/**
 * The Sessions page's new-session form draws the agent list ONCE, after
 * GET /agents has answered (v1.449.0, readiness item 19).
 *
 * THE FAILURE: the form rendered a FALLBACK agent list first, in its own
 * order, and when the daemon's list arrived (a different order, more names)
 * the keyed <option>s under a controlled <select> were re-ordered in place —
 * a React error on about one Sessions-page load in four (found live by the
 * v1.447.0 re-audit). Now the select is a disabled placeholder until the
 * read settles; a read that FAILED falls back to the built-in list so the
 * form never waits forever. The old shape fails the first case here (it
 * draws "builder" before any answer).
 *
 * The useApi mock is a tiny store the test flips, so the hook really
 * re-renders the form the way a late answer does.
 */
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useEffect, useState } from "react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(m: string, status = 500) {
      super(m);
      this.status = status;
    }
  }
  const listeners = new Set<() => void>();
  const store: Record<string, { data: unknown; error: unknown; loading: boolean }> = {};
  return {
    FakeApiError,
    store,
    listeners,
    posts: [] as { path: string; body?: unknown }[],
    set(path: string, state: { data: unknown; error: unknown; loading: boolean }) {
      store[path] = state;
      for (const l of listeners) l();
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  sseUrl: (p: string) => p,
  get: () => Promise.resolve({}),
  post: (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    return Promise.resolve({ id: "s1" });
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => {
    const [, force] = useState(0);
    useEffect(() => {
      const l = () => force((n) => n + 1);
      H.listeners.add(l);
      return () => {
        H.listeners.delete(l);
      };
    }, []);
    if (!path) return { data: null, error: null, loading: false, reload: () => {} };
    return { reload: () => {}, ...(H.store[path] ?? { data: null, error: null, loading: true }) };
  },
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ data: { models: [] }, error: null, loading: false }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/sessions",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));

import { NewSessionForm } from "@/components/NewSessionForm";

// The daemon's own order (routes/agents.py: `_DEFINITIONS`), which differs from
// the form's fallback order on purpose.
const LIVE = {
  builtin: ["supervisor", "builder", "file_manager", "memory", "planner"],
  dynamic: [{ name: "ledger-checker" }],
};

const picker = () => screen.getByLabelText("Agent type") as HTMLSelectElement;
const values = () => Array.from(picker().options).map((o) => o.value);

beforeEach(() => {
  for (const k of Object.keys(H.store)) delete H.store[k];
  H.posts.length = 0;
  H.set("/health", {
    data: { default_provider: "mock", default_model: "mock-1", providers: [] },
    error: null,
    loading: false,
  });
});

describe("the agent list waits for GET /agents", () => {
  it("draws no agent before the answer, then the daemon's list in the daemon's order", async () => {
    render(<NewSessionForm />);
    // Before the answer: a disabled placeholder, no fallback names.
    expect(picker().disabled).toBe(true);
    expect(values()).not.toContain("builder");
    expect(values()).not.toContain("supervisor");

    act(() => H.set("/agents", { data: LIVE, error: null, loading: false }));
    await waitFor(() => expect(picker().disabled).toBe(false));
    expect(values()).toEqual(["supervisor", "builder", "file_manager", "memory", "planner", "ledger-checker"]);
    // The default pick is still Builder, whatever place it has in the list.
    expect(picker().value).toBe("builder");
  });

  it("a read that failed falls back to the built-in list, so the form never waits forever", async () => {
    render(<NewSessionForm />);
    expect(picker().disabled).toBe(true);
    act(() => H.set("/agents", { data: null, error: new H.FakeApiError("Iron Jarvis isn't responding", 0), loading: false }));
    await waitFor(() => expect(picker().disabled).toBe(false));
    expect(values()).toEqual(["builder", "supervisor", "planner", "researcher", "reviewer"]);
  });

  it("the list is drawn once: a settled answer is not re-ordered by a later equal read", async () => {
    render(<NewSessionForm />);
    act(() => H.set("/agents", { data: LIVE, error: null, loading: false }));
    await waitFor(() => expect(picker().disabled).toBe(false));
    const before = values();
    act(() => H.set("/agents", { data: { ...LIVE }, error: null, loading: false }));
    expect(values()).toEqual(before);
  });

  it("anti-vacuity: a pick made after the answer still posts the raw id", async () => {
    render(<NewSessionForm />);
    act(() => H.set("/agents", { data: LIVE, error: null, loading: false }));
    await waitFor(() => expect(picker().disabled).toBe(false));
    fireEvent.change(screen.getByPlaceholderText(/Describe what the agent should do/), {
      target: { value: "Sort the receipts" },
    });
    fireEvent.change(picker(), { target: { value: "file_manager" } });
    fireEvent.click(screen.getByRole("button", { name: /Run session/ }));
    await waitFor(() => expect(H.posts.length).toBe(1));
    expect(H.posts[0].path).toBe("/sessions");
    expect((H.posts[0].body as Record<string, unknown>).agent_type).toBe("file_manager");
  });
});
