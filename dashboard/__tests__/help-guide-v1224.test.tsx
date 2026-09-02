import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * v1.224.0 — "Ask the Guide" on the Help page.
 *
 * The built-in Iron Jarvis expert is an AGENT on the Agents page; the Help
 * page is its front door. What is pinned: the box lands on the Agents page
 * with `talk=guide` (open/start the 1:1 thread) and the question PREFILLED
 * (never sent — consent waits for Enter in the composer); Enter in the box is
 * the same navigation as the button; and the status line tells the truth
 * about what this install carries, naming a missing doc instead of implying
 * the Guide knows a chapter it does not have.
 */

const hooks = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    errors: {} as Record<string, unknown>,
    MockApiError,
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: path ? (hooks.errors[path] ?? null) : null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: hooks.MockApiError,
  ijToken: () => "",
  get: () => Promise.reject(new hooks.MockApiError("unmocked", 404)),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

import HelpPage from "@/app/help/page";

const STATUS = {
  docs: [
    { slug: "handbook", title: "The Handbook", sections: 40 },
    { slug: "vocabulary", title: "Vocabulary", sections: 6 },
  ],
  missing: [],
  doc_sections: 46,
  live_sections: 12,
};

afterEach(() => {
  cleanup();
  hooks.responses = {};
  hooks.errors = {};
});

describe("Ask the Guide (v1.223.0)", () => {
  it("lands on the Agents page talking to the Guide, the question prefilled, never sent", () => {
    hooks.responses["/guide/status"] = STATUS;
    hooks.responses["/helpdocs"] = { docs: [] };
    render(<HelpPage />);
    const link = screen.getByTestId("ask-guide-link");
    expect(link).toHaveAttribute("href", "/agents?talk=guide");
    fireEvent.change(screen.getByLabelText("Ask the Guide"), {
      target: { value: "How do updates install?" },
    });
    expect(link).toHaveAttribute(
      "href",
      "/agents?talk=guide&ask=How%20do%20updates%20install%3F",
    );
  });

  it("Enter in the box is the same navigation as the button", () => {
    hooks.responses["/guide/status"] = STATUS;
    hooks.responses["/helpdocs"] = { docs: [] };
    render(<HelpPage />);
    const clicked: string[] = [];
    const link = screen.getByTestId("ask-guide-link") as HTMLAnchorElement;
    link.addEventListener("click", (e) => {
      e.preventDefault();
      clicked.push(link.getAttribute("href") ?? "");
    });
    const box = screen.getByLabelText("Ask the Guide");
    fireEvent.change(box, { target: { value: "what is a memory base" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(clicked).toEqual(["/agents?talk=guide&ask=what%20is%20a%20memory%20base"]);
  });

  it("says what the Guide knows, and names a missing doc rather than hiding it", () => {
    hooks.responses["/guide/status"] = {
      ...STATUS,
      missing: [{ slug: "spec", file: "SPEC.MD" }],
    };
    hooks.responses["/helpdocs"] = { docs: [] };
    render(<HelpPage />);
    expect(
      screen.getByText(/Knows 2 reference docs \(46 sections\) plus 12 live catalogs/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Missing from this install: SPEC\.MD/)).toBeInTheDocument();
  });

  it("an unreachable daemon is UNKNOWN, not 'knows nothing'", () => {
    hooks.errors["/guide/status"] = new hooks.MockApiError("offline", 0);
    hooks.responses["/helpdocs"] = { docs: [] };
    render(<HelpPage />);
    expect(screen.getByText(/daemon looks offline/)).toBeInTheDocument();
    expect(screen.queryByText(/Knows /)).not.toBeInTheDocument();
  });
});
