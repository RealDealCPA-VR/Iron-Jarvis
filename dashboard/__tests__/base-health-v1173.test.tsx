import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * The Memory page tells you which bases it can actually read (v1.173.0).
 *
 * v1.172.0 taught the daemon to judge a base's availability; nothing rendered
 * the verdict, so a vault that had moved and a remote brain that had gone dark
 * still looked exactly like a base with no matching notes.
 *
 * The three rules this pins, all of them honesty rules:
 *
 *  - THREE states, never two. `available` is true / false / unknown, and the
 *    unknown one must NOT be painted as either of the others. A truthy test
 *    would fold "not checked" into "unavailable" (a false alarm); a falsy one
 *    would fold it into "reachable" (the original lie).
 *  - the REASON is on screen without a click. The detail is the only thing
 *    that tells the user which fix to apply.
 *  - an older/unreachable daemon renders NOTHING, rather than a box claiming
 *    a check that never ran.
 */

const apiState = vi.hoisted(() => ({
  calls: [] as string[],
  /** Reject GET /ltm/sources (older daemon / daemon offline). */
  fail: false,
  payload: {} as Record<string, unknown>,
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
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  wsUrl: (p: string) => `ws://127.0.0.1:8787${p}`,
  sseUrl: (p: string) => `http://127.0.0.1:8787${p}`,
  get: (path: string) => {
    if (!path.startsWith("/ltm/sources")) return Promise.resolve({});
    apiState.calls.push(path);
    if (apiState.fail) return Promise.reject(new FakeApiError("nope", 404));
    return Promise.resolve(apiState.payload);
  },
  post: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  api: () => Promise.resolve({}),
}));

// The page owns no named exports (an App Router page file may not have any —
// see the note in page.tsx), so the whole PAGE is mounted. That is the
// stronger test anyway: it proves the card is WIRED IN, the mutation that
// v1.163.0 got caught by (a component nobody renders passes every test of
// itself).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/memory",
}));

// Imported AFTER the mocks so the page binds to the fakes.
const MemoryPage = (await import("@/app/memory/page")).default;

const BASES = [
  { name: "brain", kind: "markdown", available: true, detail: "", path: "C:/notes" },
  {
    name: "team-wiki",
    kind: "markdown",
    available: false,
    detail:
      "folder not found: D:/wiki — it was moved, renamed, or is on a drive that isn't available right now",
    path: "D:/wiki",
  },
  {
    name: "hermes-brain",
    kind: "mcp",
    available: null,
    detail: "could not check in time (waited 4s) — availability is unknown",
    path: "http://127.0.0.1:9/mcp",
  },
  // A remote kind with no cheap probe: the daemon sends no `available` at all.
  { name: "notion", kind: "notion", detail: "" },
];

beforeEach(() => {
  cleanup();
  apiState.calls = [];
  apiState.fail = false;
  apiState.payload = { bases: BASES, sources: [], active: [] };
});

describe("memory base availability", () => {
  it("renders every base with its OWN state, and unknown is neither of the others", async () => {
    render(<MemoryPage />);
    await screen.findByText("brain");

    const row = (name: string) =>
      screen.getByText(name).closest("li") as HTMLElement;

    expect(row("brain")).toHaveTextContent("Reachable");
    expect(row("team-wiki")).toHaveTextContent("Unavailable");
    // The two shapes of "we don't know": an explicit null and a missing field.
    // Neither may borrow another row's label.
    expect(row("hermes-brain")).toHaveTextContent("Not checked");
    expect(row("hermes-brain")).not.toHaveTextContent("Unavailable");
    expect(row("hermes-brain")).not.toHaveTextContent("Reachable");
    expect(row("notion")).toHaveTextContent("Not checked");
    expect(row("notion")).not.toHaveTextContent("Reachable");
  });

  it("colours the dot green / amber / grey — and never amber for unknown", async () => {
    render(<MemoryPage />);
    await screen.findByText("brain");
    const dot = (name: string) =>
      (screen.getByText(name).closest("li") as HTMLElement).querySelector(
        "span.rounded-full",
      )?.className ?? "";

    expect(dot("brain")).toContain("bg-emerald-400");
    expect(dot("team-wiki")).toContain("bg-amber-400");
    expect(dot("hermes-brain")).toContain("bg-zinc-500");
    expect(dot("notion")).toContain("bg-zinc-500");
    expect(screen.getByText("1 unavailable")).toBeInTheDocument();
  });

  it("shows the reason WITHOUT an expand, for both the failure and the unknown", async () => {
    render(<MemoryPage />);
    await screen.findByText("brain");
    expect(
      screen.getByText(/folder not found: D:\/wiki/),
    ).toBeInTheDocument();
    expect(screen.getByText(/could not check in time/)).toBeInTheDocument();
    // The path is shown too — it is what the user checks against reality.
    expect(screen.getByText("http://127.0.0.1:9/mcp")).toBeInTheDocument();
  });

  it("asks for a network check, because it is the surface that wants one", async () => {
    // `probe=true` is opt-in per request: the SAME endpoint feeds the
    // Long-term tab's source list and the project page's LTM chip, and neither
    // of those should start waiting on a dead remote brain. This card is the
    // one caller whose entire job is the verdict, so it is the one that asks.
    render(<MemoryPage />);
    await screen.findByText("brain");
    expect(apiState.calls).toEqual(["/ltm/sources?probe=true"]);
  });

  it("re-check asks the daemon to drop its cached verdicts", async () => {
    render(<MemoryPage />);
    await screen.findByText("brain");

    fireEvent.click(screen.getByRole("button", { name: /re-check/i }));
    await waitFor(() =>
      expect(apiState.calls).toEqual([
        "/ltm/sources?probe=true",
        "/ltm/sources?refresh=true&probe=true",
      ]),
    );
  });

  it("renders no card at all when the daemon cannot answer", async () => {
    apiState.fail = true;
    render(<MemoryPage />);
    await waitFor(() => expect(apiState.calls.length).toBe(1));
    expect(screen.queryByText("Memory bases")).toBeNull();
    expect(screen.queryByText("Not checked")).toBeNull();
  });

  it("renders no card on a daemon whose listing has no bases field", async () => {
    apiState.payload = { sources: [], active: ["brain"] };
    render(<MemoryPage />);
    await waitFor(() => expect(apiState.calls.length).toBe(1));
    expect(screen.queryByText("Memory bases")).toBeNull();
  });
});
