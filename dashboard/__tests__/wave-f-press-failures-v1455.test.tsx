/**
 * v1.455.0 (readiness Wave F, items 8 + 9): "did it work?" when the server
 * itself broke.
 *
 * Item 8 — a PRESS whose request answers the daemon's 500 envelope
 * ("internal error [err_1a2b3c4d]: KeyError: 'agent_type'") said it word for
 * word, and raised the app-wide "Part of this page didn't load. It will try
 * again in a moment." banner, which is untrue for a button. Now lib/api.ts:
 *   - rewords the envelope (or a 500 with no words) to PRESS_PROBLEM_WORDS,
 *     keeping the raw text as `technical`;
 *   - raises the banner only for a page READ (GET/HEAD);
 *   - keeps a 5xx the daemon wrote itself (a sentence for the user).
 * ErrorNote folds the raw text under Details.
 *
 * Item 9 — reads that failed printed the server's text (Autonomy ×6, the
 * Model team, Standing grants, DataError). Now each is the one load-failed
 * line ("Proposals couldn't load. Try again.") with the raw text on hover.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, ApiError, onRequestErrorChange } from "@/lib/api";
import {
  _resetServerProblem,
  PRESS_DETAILS_WORDS,
  PRESS_PROBLEM_WORDS,
} from "@/lib/pressFailure";
import { DETAILS_WORDS } from "@/lib/failureActions";
import { DataError, ErrorNote } from "@/components/ui";

const ENVELOPE = "internal error [err_1a2b3c4d]: KeyError: 'agent_type'";

function mockResponse(status: number, body: unknown = {}, statusText = ""): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  _resetServerProblem();
});

async function failOf(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error("the request did not fail");
}

describe("item 8: lib/api.ts says a server error on a press plainly", () => {
  it("rewords the 500 envelope and keeps the raw text as technical", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => mockResponse(500, { detail: ENVELOPE })));
    const err = await failOf(api("/skills/x/apply", { method: "POST" }));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe(PRESS_PROBLEM_WORDS);
    expect(err.message).not.toMatch(/err_|KeyError|internal error/);
    expect(err.technical).toBe(ENVELOPE);
    expect(err.status).toBe(500);
  });

  it("a 500 with no words at all reads the same way", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => mockResponse(500, null, "Internal Server Error")));
    const err = await failOf(api("/fleet/nodes", { method: "PUT" }));
    expect(err.message).toBe(PRESS_PROBLEM_WORDS);
    expect(err.technical).toBe("500 Internal Server Error");
  });

  it("keeps a sentence the daemon wrote itself (a 503, a 500 detail that is not the envelope)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mockResponse(503, { detail: "The account manager is not answering." })),
    );
    const err = await failOf(api("/iron-proxy/accounts", { method: "POST" }));
    expect(err.message).toBe("The account manager is not answering.");
    expect(err.technical).toBeUndefined();
  });

  it("raises the page banner for a READ that failed, never for a PRESS", async () => {
    let failing = false;
    const off = onRequestErrorChange((v) => (failing = v));
    vi.stubGlobal("fetch", vi.fn(async () => mockResponse(500, { detail: ENVELOPE })));
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      await api("/press", { method }).catch(() => {});
      expect(failing, method).toBe(false);
    }
    await api("/read").catch(() => {});
    expect(failing).toBe(true);
    off();
  });
});

describe("item 8: ErrorNote folds the raw text under Details", () => {
  it("shows the plain words and the err id behind Details", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => mockResponse(500, { detail: ENVELOPE })));
    const err = await failOf(api("/press", { method: "POST" }));
    render(<ErrorNote>{err.message}</ErrorNote>);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(PRESS_PROBLEM_WORDS);
    const details = screen.getByTestId("error-note-details");
    expect(details.tagName).toBe("DETAILS");
    expect((details as HTMLDetailsElement).open).toBe(false);
    expect(details.textContent).toContain(PRESS_DETAILS_WORDS);
    expect(details.textContent).toContain("err_1a2b3c4d");
  });

  it("a note that is not the press-problem sentence has no Details", () => {
    render(<ErrorNote>Couldn&apos;t save: the name is taken.</ErrorNote>);
    expect(screen.queryByTestId("error-note-details")).toBeNull();
  });

  it("the fold's word is the app's one Details word", () => {
    expect(PRESS_DETAILS_WORDS).toBe(DETAILS_WORDS);
  });

  it("api.ts keeps its helpers outside the module (it is mocked wholesale)", () => {
    const src = readFileSync(join(__dirname, "..", "lib", "api.ts"), "utf8");
    expect(src).toMatch(/from "\.\/pressFailure"/);
    expect(src).not.toMatch(/Iron Jarvis hit a problem/);
  });
});

describe("item 9: DataError is the one load-failed line", () => {
  it("names the thing and hides the server text in the title", () => {
    render(
      <DataError
        error={{ status: 500, message: "detection scan failed: OperationalError" }}
        what="the rules"
      />,
    );
    const line = screen.getByTestId("data-error");
    expect(line.textContent).toBe("The rules couldn't load. Try again.");
    expect(line.getAttribute("title")).toBe("HTTP 500: detection scan failed: OperationalError");
  });

  it("a press-problem error shows its raw envelope on hover, not the plain words", () => {
    render(
      <DataError
        error={{ status: 500, message: PRESS_PROBLEM_WORDS, technical: ENVELOPE }}
        what="run history"
      />,
    );
    expect(screen.getByTestId("data-error").getAttribute("title")).toBe(`HTTP 500: ${ENVELOPE}`);
  });

  it("offers Try again as a press when it can re-read", () => {
    const retry = vi.fn();
    render(<DataError error={{ status: 500, message: "x" }} what="safety checks" onRetry={retry} />);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});

describe("item 9: pages never print the server's raw read failure", () => {
  const SOURCES = [
    "app/autonomy/page.tsx",
    "components/StandingGrants.tsx",
    "components/settings/ModelTeam.tsx",
  ];
  for (const file of SOURCES) {
    it(`${file} has no ErrorNote of a read's error.message`, () => {
      const src = readFileSync(join(__dirname, "..", file), "utf8");
      expect(src).not.toMatch(/<ErrorNote>\{\s*\w+\.error\.message\s*\}<\/ErrorNote>/);
      expect(src).toMatch(/<LoadFailedLine/);
    });
  }
});

// ---------------------------------------------------------------------------
// The real Autonomy page, every read failing.
// ---------------------------------------------------------------------------

const live = vi.hoisted(() => ({ responses: {} as Record<string, unknown> }));

vi.mock("@/lib/useApi", async () => {
  const { useState, useEffect, useCallback } = await import("react");
  function useFake(path: string | null) {
    const [state, setState] = useState<{ data: unknown; error: unknown; loading: boolean }>({
      data: null,
      error: null,
      loading: true,
    });
    const [n, setN] = useState(0);
    useEffect(() => {
      if (!path) return;
      const v = live.responses[path];
      if (v instanceof Error) setState({ data: null, error: v, loading: false });
      else setState({ data: v ?? {}, error: null, loading: false });
    }, [path, n]);
    const reload = useCallback(() => setN((x) => x + 1), []);
    return { ...state, reload };
  }
  return {
    useApi: (path: string | null) => useFake(path),
    usePolledApi: (path: string | null) => useFake(path),
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/autonomy",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

beforeAll(() => {
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
});
beforeEach(() => {
  live.responses = {};
});

function serverError(text: string): Error {
  const e = new ApiError(PRESS_PROBLEM_WORDS, 500, false, undefined, text);
  return e;
}

describe("item 9: the Autonomy page's failed reads", () => {
  it("every failed read says its own load-failed line; no raw server text", async () => {
    const { default: AutonomyPage } = await import("@/app/autonomy/page");
    live.responses["/autonomy"] = serverError("internal error [err_00000001]: KeyError: 'level'");
    live.responses["/autonomy/goals"] = serverError("internal error [err_00000002]: boom");
    live.responses["/proposals?status=pending"] = new ApiError(
      "OperationalError: database is locked",
      500,
    );
    live.responses["/autonomy/briefing"] = new ApiError("briefing failed: TypeError", 500);
    live.responses["/goals"] = new ApiError("goal store failed: ValueError", 500);
    live.responses["/goals/digest?hours=24"] = new ApiError("digest failed: KeyError", 500);
    live.responses["/grants?live=1"] = new ApiError("grant store failed: KeyError", 500);
    render(<AutonomyPage />);
    await waitFor(() =>
      expect(screen.getByTestId("autonomy-proposals-failed").textContent).toBe(
        "Proposals couldn't load. Try again.",
      ),
    );
    expect(screen.getByTestId("autonomy-status-failed").textContent).toBe(
      "Autonomy couldn't load. Try again.",
    );
    expect(screen.getByTestId("autonomy-briefing-failed").textContent).toBe(
      "The briefing couldn't load. Try again.",
    );
    expect(screen.getByTestId("autonomy-goals-failed").textContent).toBe(
      "Simple goals couldn't load. Try again.",
    );
    expect(screen.getByTestId("autonomy-chat-goals-failed").textContent).toBe(
      "Goals from Chat couldn't load. Try again.",
    );
    expect(screen.getByTestId("standing-grants-failed").textContent).toBe(
      "Standing grants couldn't load. Try again.",
    );
    // The raw server text is on the hover title only.
    expect(screen.getByTestId("autonomy-status-failed").getAttribute("title")).toMatch(/err_00000001/);
    const page = document.body.textContent ?? "";
    for (const raw of ["OperationalError", "TypeError", "ValueError", "KeyError", "err_000"]) {
      expect(page, raw).not.toContain(raw);
    }
  });
});
