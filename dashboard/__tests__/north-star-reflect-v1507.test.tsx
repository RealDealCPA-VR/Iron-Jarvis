/**
 * North-star fix 7.3 (v1.507.0): "Reflect" leaves suggestions that can be
 * answered.
 *
 * POST /improvement/reflect used to answer suggestions the Lessons card drew
 * as plain text: no Keep, not in Improvements, gone on reload. The daemon now
 * stores each one (it waits in Improvements and on the Memory page) and sends
 * its `id` and `status`. The card draws a stored one with Keep and Not this
 * (its own routes, /memory/preferences/{id}/keep and /decline), says why one
 * was not added, says where they came from when no model answered, and links
 * to Improvements.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    get: {} as Record<string, unknown>,
    post: {} as Record<string, unknown>,
    posts: [] as { path: string; body: unknown }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  wsUrl: (p: string) => `ws://api.test${p}`,
  sseUrl: (p: string) => `http://api.test${p}`,
  get: async (path: string) => {
    const r = H.get[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    const r = H.post[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
  api: async () => ({}),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {}, back: () => {}, refresh: () => {} }),
  usePathname: () => "/memory",
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { Lessons } from "@/components/memory/Lessons";

const REFLECT = {
  reviewed: 2,
  threshold: 0.6,
  applied: false,
  made_by: "model",
  stored: 1,
  suggestions: [
    { kind: "lesson", target: "builder", suggestion: "Check each photo's date first.", status: "suggested", id: "lesson_r1" },
    { kind: "tool", target: "shell", suggestion: "Review how commands are run.", status: "full" },
  ],
};

beforeEach(() => {
  H.get["/lessons?limit=50"] = { lessons: [] };
  H.get["/improvement"] = { lessons: [], agents: [], outcomes: { count: 0 } };
  H.post["/improvement/reflect?limit=5"] = REFLECT;
  H.posts.length = 0;
});

afterEach(() => {
  cleanup();
  for (const k of Object.keys(H.post)) delete H.post[k];
});

async function reflect() {
  render(<Lessons />);
  fireEvent.click(await screen.findByRole("button", { name: /Reflect on recent/ }));
  return screen.findByTestId("reflect-suggestions");
}

describe("Reflect's suggestions can be answered (north-star 7.3)", () => {
  it("a stored suggestion has Keep and Not this on its own routes", async () => {
    const list = await reflect();
    const row = within(list).getByText("Check each photo's date first.").closest("li") as HTMLElement;
    H.post["/memory/preferences/lesson_r1/keep"] = { preference: { id: "lesson_r1", status: "confirmed" } };
    fireEvent.click(within(row).getByRole("button", { name: "Keep" }));
    expect(await within(row).findByText(/Kept\./)).toBeTruthy();
    expect(H.posts.map((p) => p.path)).toContain("/memory/preferences/lesson_r1/keep");
  });

  it("Not this declines it through its own route", async () => {
    const list = await reflect();
    const row = within(list).getByText("Check each photo's date first.").closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Not this" }));
    expect(await within(row).findByText(/won.t be suggested again/i)).toBeTruthy();
    expect(H.posts.map((p) => p.path)).toContain("/memory/preferences/lesson_r1/decline");
  });

  it("one that was not added says why and has no buttons", async () => {
    const list = await reflect();
    const row = within(list).getByText("Review how commands are run.").closest("li") as HTMLElement;
    expect(row.textContent).toMatch(/Not added: suggestions already wait in Improvements/);
    expect(within(row).queryByRole("button", { name: "Keep" })).toBeNull();
  });

  it("says that stored ones wait in Improvements", async () => {
    await reflect();
    const link = screen.getByRole("link", { name: /Improvements/ });
    expect(link.getAttribute("href")).toBe("/improvements");
    expect(document.body.textContent).not.toMatch(/low-scoring session/);
  });

  it("lines written from the scores alone say so, have no buttons and no Improvements link", async () => {
    // Lead review: the scores' own fallback is shown, never stored.
    H.post["/improvement/reflect?limit=5"] = {
      reviewed: 2,
      threshold: 0.6,
      applied: false,
      made_by: "scores",
      note: "No model answered, so these suggestions come from the scores alone.",
      stored: 0,
      suggestions: [{ kind: "lesson", target: "builder", suggestion: "Look at the builder runs.", status: "from_scores" }],
    };
    const list = await reflect();
    expect(screen.getByText(/No model answered, so these suggestions come from the scores alone\./)).toBeTruthy();
    const row = within(list).getByText("Look at the builder runs.").closest("li") as HTMLElement;
    expect(row.textContent).toMatch(/Shown only: it comes from the scores alone/);
    expect(within(row).queryByRole("button", { name: "Keep" })).toBeNull();
    expect(screen.queryByRole("link", { name: /Improvements/ })).toBeNull();
  });
});
