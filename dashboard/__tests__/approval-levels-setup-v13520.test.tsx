/**
 * v1.352.0 (product-readiness G24): an agent's setup names the approval levels
 * exactly as the chat's chip does (lib/permissionLevels), driven through the
 * REAL SetupCard (the employee details' "Approval posture" select).
 *
 * Before: "Ask me as usual" / "Approve for me" / "Always ask me" here,
 * "Ask for approval" / "Approve for me" / "Auto-approve" in Settings, and
 * "Ask first" / "Ask when risky" / "Don't ask" on the chip — three label sets
 * for one setting. Now one: the chip's. "Don't ask" is listed (so the three
 * words are the same everywhere) and cannot be picked for an agent — it is
 * consent to one watched chat turn, never a standing posture (the daemon
 * stores only "", approve_for_me or always_ask for an employee).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> }>,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    get: (path: string) => Promise.resolve(path === "/tools" ? { tools: [] } : {}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      return Promise.resolve({});
    },
    patch: () => Promise.resolve({}),
  };
});

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    // The v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

import { PERMISSION_LEVELS } from "@/lib/permissionLevels";
import { SetupCardHarness } from "./helpers/setupCardHarness";

beforeEach(() => {
  hooks.api = {};
  hooks.posts = [];
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

function openDetails() {
  render(
    <SetupCardHarness
      initialOpen
      builtin={["supervisor", "builder", "planner", "reviewer"]}
      dynamic={[]}
      remotes={[]}
      models={[{ provider: "openai", model: "gpt-5" }]}
      onAgentsChanged={() => {}}
      onRemotesChanged={() => {}}
    />,
  );
  fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "skeptic" } });
  fireEvent.change(screen.getByLabelText("Persona prompt"), { target: { value: "You are a skeptic." } });
  fireEvent.click(screen.getByTestId("employee-details-toggle"));
  return within(screen.getByTestId("employee-details"));
}

describe("an agent's setup names the levels as the chat chip does", () => {
  it("offers the usual, then the chip's three levels in its order, 'Don't ask' shown but not choosable", () => {
    const details = openDetails();
    const select = details.getByLabelText("Approval posture") as HTMLSelectElement;
    const options = Array.from(select.options);
    expect(options.map((o) => o.textContent)).toEqual([
      `As usual (${PERMISSION_LEVELS.approve_for_me.label})`,
      // v1.406.0 part 2: an agent keeps its three postures — Accept edits and
      // Auto are a watched chat's or coding session's (an agent's own run is
      // landed as Ask first; runtime.inherited_approval_mode).
      ...(["always_ask", "approve_for_me", "yolo"] as const).map((m) =>
        m === "yolo" ? `${PERMISSION_LEVELS.yolo.label} (chat only)` : PERMISSION_LEVELS[m].label,
      ),
    ]);
    // The chip's words, literally — the acceptance a new user reads.
    expect(options.map((o) => o.textContent)).toEqual([
      "As usual (Ask first)",
      "Ask before every action",
      "Ask first",
      "Don't ask (chat only)",
    ]);
    // The posted VALUES are unchanged; "Don't ask" can never be picked.
    expect(options.map((o) => o.value)).toEqual(["", "always_ask", "approve_for_me", "yolo"]);
    expect(options.map((o) => o.disabled)).toEqual([false, false, false, true]);
    // Each level says what it does, in the chip's own sentence.
    expect(options[1].title).toBe(PERMISSION_LEVELS.always_ask.description);
    expect(options[2].title).toBe(PERMISSION_LEVELS.approve_for_me.description);
  });

  it("a pick still posts the daemon's value", async () => {
    const details = openDetails();
    fireEvent.change(details.getByLabelText("Approval posture"), { target: { value: "always_ask" } });
    fireEvent.click(screen.getByRole("button", { name: /^create agent$/i }));
    await vi.waitFor(() => expect(hooks.posts.find((p) => p.path === "/agents")).toBeTruthy());
    expect(hooks.posts.find((p) => p.path === "/agents")!.body.approval_mode).toBe("always_ask");
  });
});
