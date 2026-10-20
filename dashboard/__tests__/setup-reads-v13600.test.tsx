/**
 * v1.360.0 integrate (lane 10C note B): an agent's approval choices say what
 * Jarvis may READ, as the chat chip and the Access summary do.
 *
 * The final re-audit's G81 regression: after the user limited reading to
 * chosen folders, every approval level still said "Reads files anywhere on
 * this PC without asking". Lane 10C fixed the chip and the Access summary;
 * the agent setup's "Approval posture" titles still used the default. They
 * now read `lib/permissionLevels.readsLevelLine` over `lib/readFacts` (the
 * one decision), and focusing the list asks GET /settings/access the way the
 * chip does (never on mount).
 *
 * The REAL SetupCard (through the v1.352.0 harness); only the network is
 * stubbed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  gets: [] as string[],
  access: null as unknown,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
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
    get: (path: string) => {
      hooks.gets.push(path);
      if (path === "/settings/access") return Promise.resolve(hooks.access ?? {});
      return Promise.resolve(path === "/tools" ? { tools: [] } : {});
    },
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: () => Promise.resolve({}),
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

import { PERMISSION_LEVELS, permissionLevel, readsLevelLine } from "@/lib/permissionLevels";
import { __resetReadFacts, noteReadFacts } from "@/lib/readFacts";
import { SetupCardHarness } from "./helpers/setupCardHarness";

const CHOSEN = { limited: true, folders: ["D:\\Clients"], chosen: ["D:\\Clients"], install: false };
const CHOSEN_LINE = "Reads files only in the folders you chose, without asking";

beforeEach(() => {
  hooks.gets = [];
  hooks.access = null;
  __resetReadFacts();
});
afterEach(() => {
  cleanup();
  __resetReadFacts();
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

const titles = (select: HTMLSelectElement) => Array.from(select.options).map((o) => o.title);

describe("an agent's approval choices say what Jarvis may read", () => {
  it("once reading is limited to chosen folders, both real levels say so — the chip's sentence", () => {
    noteReadFacts(CHOSEN);
    const select = openDetails().getByLabelText("Approval posture") as HTMLSelectElement;
    const [, ask, risky] = titles(select);
    expect(ask).toBe(permissionLevel("always_ask", readsLevelLine(CHOSEN)).description);
    expect(risky).toBe(permissionLevel("approve_for_me", readsLevelLine(CHOSEN)).description);
    for (const t of [ask, risky]) {
      expect(t).toContain(CHOSEN_LINE);
      expect(t).not.toContain("anywhere");
    }
  });

  it("with nothing known it says exactly what it said before (no guess)", () => {
    const select = openDetails().getByLabelText("Approval posture") as HTMLSelectElement;
    const [, ask, risky] = titles(select);
    expect(ask).toBe(PERMISSION_LEVELS.always_ask.description);
    expect(risky).toBe(PERMISSION_LEVELS.approve_for_me.description);
  });

  it("reads GET /settings/access only when the user reaches for the list, then follows the answer", async () => {
    hooks.access = { reads: CHOSEN };
    const select = openDetails().getByLabelText("Approval posture") as HTMLSelectElement;
    expect(hooks.gets.filter((p) => p === "/settings/access")).toEqual([]);
    fireEvent.focus(select);
    await waitFor(() => expect(titles(select)[1]).toContain(CHOSEN_LINE));
    expect(hooks.gets.filter((p) => p === "/settings/access")).toEqual(["/settings/access"]);
  });
});
