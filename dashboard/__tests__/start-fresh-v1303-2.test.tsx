/**
 * v1.303.2 — when the next account cannot pick a carried conversation up.
 *
 * "Continue on the next account" (v1.303.0) copies the conversation into the
 * other account's history and resumes it there; that account may refuse it.
 * The daemon's activity row then carries `resume_failed: {line, since}` and
 * the pane offers the way out. These tests guard:
 *
 *  - the strip `#pane-resume-failed-<id>` shows only with `resume_failed`, says
 *    which account could not pick it up with the CLI's own sentence (a sign-in
 *    variant rendered as it comes), offers "Start fresh with what we were
 *    doing", names `/clear` as the empty start, and says BEFORE the press what
 *    the summary holds and where it goes;
 *  - the press: POST /terminals/{id}/start-fresh-with-handoff, the strip hides,
 *    the "Continued from …" line now says it started fresh with a summary plus
 *    the answer's note; a different pane in the answer is handed to the page;
 *    a 409 sentence is shown and nothing changes;
 *  - Dismiss is keyed on the failure's `since`, never its line;
 *  - the page hands each pane its activity row's `resume_failed` (driven
 *    through the real page) and TerminalPane mounts the strip (source pin).
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------- */

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    calls: [] as Array<{ method: string; path: string; body: unknown }>,
    results: {} as Record<string, unknown>,
    fail: {} as Record<string, { status: number; message: string }>,
  };
});

vi.mock("@/lib/api", () => {
  const send = (method: string, path: string, body?: unknown) => {
    hooks.calls.push({ method, path, body });
    const key = `${method} ${path}`;
    const f = hooks.fail[key];
    if (f) return Promise.reject(new hooks.FakeApiError(f.message, f.status));
    return Promise.resolve(key in hooks.results ? hooks.results[key] : {});
  };
  return {
    ApiError: hooks.FakeApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: (path: string) => {
      const r = hooks.responses[path];
      if (r === undefined) {
        return Promise.reject(new hooks.FakeApiError(`unmocked GET ${path}`, 404));
      }
      return Promise.resolve(r);
    },
    post: (path: string, body?: unknown) => send("POST", path, body),
    patch: (path: string, body?: unknown) => send("PATCH", path, body),
    put: (path: string, body?: unknown) => send("PUT", path, body),
    del: (path: string) => send("DELETE", path),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/terminals",
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

/* ---- the Build page: the pane stub renders the REAL strip with the props the
 * page hands TerminalPane. -------------------------------------------------- */

vi.mock("next/dynamic", async () => {
  const { ResumeFailedStrip, ContinuedFromLine } = await import("@/components/terminal/ContinueStrip");
  const { continuedFromOf } = await import("@/lib/paneAccounts");
  function TerminalPaneStub(props: {
    info: { id: string; shell: string };
    onOpenedPane?: (p: { id: string; [k: string]: unknown }) => void;
    liveAccounts?: import("@/lib/paneAccounts").PaneAccounts | null;
    paneResumeFailed?: import("@/lib/paneAccounts").ResumeFailed | null;
    paneSignInNeeded?: import("@/lib/paneAccounts").SignInNeeded | null;
  }) {
    return (
      <div data-testid={`terminal-pane-${props.info.id}`}>
        <ResumeFailedStrip
          paneId={props.info.id}
          failed={props.paneResumeFailed}
          signIn={props.paneSignInNeeded}
          accounts={props.liveAccounts}
          continuedFrom={continuedFromOf(props.info)}
          onOpened={(p) => props.onOpenedPane?.(p)}
        />
        <ContinuedFromLine paneId={props.info.id} />
      </div>
    );
  }
  return {
    default: () => {
      function DynamicStub(props: Record<string, unknown>) {
        if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
        return <TerminalPaneStub {...(props as unknown as Parameters<typeof TerminalPaneStub>[0])} />;
      }
      return DynamicStub;
    },
  };
});

vi.mock("react-rnd", () => ({
  Rnd: ({ children }: { children?: React.ReactNode }) => <div data-testid="rnd">{children}</div>,
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
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div /> }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

import TerminalsPage from "@/app/terminals/page";
import {
  ContinuedFromLine,
  ResumeFailedStrip,
  rememberContinued,
  resetContinueMemory,
} from "@/components/terminal/ContinueStrip";
import {
  continuedFromOf,
  handoffNote,
  paneAccountsOf,
  resumeFailedKey,
  resumeFailedOf,
  resumeFailedWords,
  signInNeededKey,
  signInNeededOf,
  type PaneAccounts,
  type ResumeFailed,
  type SignInNeeded,
} from "@/lib/paneAccounts";

/* ---- fixtures -------------------------------------------------------------- */

const ON_PERSONAL: PaneAccounts = paneAccountsOf({
  accounts: { anthropic: { id: "b", title: "Personal", source: "iron-proxy", state: "active" } },
})!;

const FAILED: ResumeFailed = {
  line: "No conversation found with session ID: 1f2e3d.",
  since: "2026-10-03T20:00:00Z",
};

const FROM = continuedFromOf({
  continued_from: {
    from_id: "a",
    from_title: "Work Max",
    session_id: "1f2e3d",
    carried_path: "C:\\Users\\VR\\.iron-proxy\\homes\\b\\projects\\C--proj\\1f2e3d.jsonl",
    at: "2026-10-03T19:59:00Z",
  },
});

const HANDOFF = {
  pane: { id: "t1" },
  session_id: "9a8b7c",
  handoff_path: "C:\\proj\\.ironjarvis\\handoff-9a8b7c.md",
  note: "The summary is in .ironjarvis\\handoff-9a8b7c.md.",
};

const SIGN_IN: SignInNeeded = {
  line: "Login expired · Please run /login",
  since: "2026-10-03T20:05:00Z",
  account: { id: "b", title: "Personal" },
};

const MANUAL =
  "Could not write the summary: the conversation file is locked.\nType /exit in the pane, then run: claude --resume 1f2e3d";

const HOW =
  "Iron Jarvis writes a short summary of the conversation so far (your first request, the last few messages, the files involved) and starts a new Claude session on “Personal” with it.";

const callsTo = (method: string, path: string) =>
  hooks.calls.filter((c) => c.method === method && c.path === path);
const src = (...parts: string[]) =>
  readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");
const strip = (id = "t1") => document.getElementById(`pane-resume-failed-${id}`);
const line = (id = "t1") => document.getElementById(`pane-continued-${id}`);
const go = (id = "t1") => screen.getByTestId(`pane-start-fresh-${id}`) as HTMLButtonElement;

beforeEach(() => {
  localStorage.clear();
  resetContinueMemory();
  hooks.responses = {};
  hooks.calls = [];
  hooks.results = {};
  hooks.fail = {};
});

afterEach(cleanup);

/* ---- 1. the seams ---------------------------------------------------------- */

describe("helpers", () => {
  it("reads the activity row's resume_failed — its line, or the sentence it carries", () => {
    expect(resumeFailedOf({ resume_failed: FAILED })).toEqual(FAILED);
    expect(resumeFailedOf({ resume_failed: { sentence: "Not logged in · Please run /login", since: "S" } })).toEqual({
      line: "Not logged in · Please run /login",
      since: "S",
    });
    expect(resumeFailedOf({ resume_failed: { line: "" } })).toBeNull();
    expect(resumeFailedOf({ id: "t1" })).toBeNull();
    expect(resumeFailedOf(null)).toBeNull();
  });

  it("Dismiss keys on the episode; the line loses a trailing full stop", () => {
    expect(resumeFailedKey(FAILED)).toBe(FAILED.since);
    expect(resumeFailedKey({ line: "x" })).toBe("x");
    expect(resumeFailedWords(FAILED)).toBe("No conversation found with session ID: 1f2e3d");
  });

  it("reads the activity row's sign_in_needed (needs the account to act on)", () => {
    expect(signInNeededOf({ sign_in_needed: SIGN_IN })).toEqual(SIGN_IN);
    expect(signInNeededOf({ sign_in_needed: { line: "Login expired", since: "S" } })).toBeNull();
    expect(signInNeededOf({ id: "t1" })).toBeNull();
    expect(signInNeededKey(SIGN_IN)).not.toBe(resumeFailedKey({ line: SIGN_IN.line, since: SIGN_IN.since }));
  });

  it("reads the pane row's continued_from", () => {
    expect(FROM).toMatchObject({ from_id: "a", from_title: "Work Max", session_id: "1f2e3d" });
    expect(continuedFromOf({ id: "t1" })).toBeNull();
  });

  it("the handoff note: what happened, then the daemon's words", () => {
    expect(handoffNote(HANDOFF)).toBe(
      "Started fresh with a summary of what you were doing. The summary is in .ironjarvis\\handoff-9a8b7c.md.",
    );
    expect(handoffNote({ note: null })).toBe("Started fresh with a summary of what you were doing.");
  });
});

/* ---- 2. the strip ---------------------------------------------------------- */

function mount(failed: ResumeFailed | null = FAILED, onOpened = vi.fn(), signIn: SignInNeeded | null = null) {
  const utils = render(
    <>
      <ResumeFailedStrip
        paneId="t1"
        failed={failed}
        signIn={signIn}
        accounts={ON_PERSONAL}
        continuedFrom={FROM}
        onOpened={onOpened}
      />
      <ContinuedFromLine paneId="t1" />
      <ContinuedFromLine paneId="t-other" />
    </>,
  );
  const rerender = (f: ResumeFailed | null) =>
    utils.rerender(
      <>
        <ResumeFailedStrip
          paneId="t1"
          failed={f}
          signIn={signIn}
          accounts={ON_PERSONAL}
          continuedFrom={FROM}
          onOpened={onOpened}
        />
        <ContinuedFromLine paneId="t1" />
        <ContinuedFromLine paneId="t-other" />
      </>,
    );
  return { onOpened, rerender };
}

describe("the resume-failed strip", () => {
  it("does not show without resume_failed", () => {
    mount(null);
    expect(strip()).toBeNull();
    expect(hooks.calls).toEqual([]);
  });

  it("says who could not pick it up, offers the fresh start and /clear, and what the summary holds", () => {
    mount();
    const s = strip()!;
    expect(s.textContent).toContain(
      "“Personal” could not pick up this conversation (No conversation found with session ID: 1f2e3d).",
    );
    expect(go().textContent).toBe("Start fresh with what we were doing");
    expect(s.textContent).toContain("or type /clear to start empty");
    expect(screen.getByTestId("pane-start-fresh-how-t1").textContent).toBe(HOW);
    expect(hooks.calls).toEqual([]); // nothing was pressed
  });

  it("renders a sign-in variant as the daemon carries it", () => {
    mount({ line: "Not logged in · Please run /login", since: "S2" });
    expect(strip()!.textContent).toContain(
      "“Personal” could not pick up this conversation (Not logged in · Please run /login).",
    );
  });

  it("the press POSTs start-fresh-with-handoff, hides the strip, and the continued line says so", async () => {
    rememberContinued("t1", "Work Max", "Carried your conversation over.");
    hooks.results["POST /terminals/t1/start-fresh-with-handoff"] = HANDOFF;
    const { onOpened } = mount();
    expect(line()!.textContent).toBe("Continued from “Work Max” — Carried your conversation over.");
    fireEvent.click(go());
    await waitFor(() => expect(strip()).toBeNull());
    expect(callsTo("POST", "/terminals/t1/start-fresh-with-handoff")).toHaveLength(1);
    expect(line()!.textContent).toBe(
      "Continued from “Work Max” — Started fresh with a summary of what you were doing. The summary is in .ironjarvis\\handoff-9a8b7c.md.",
    );
    expect(onOpened).not.toHaveBeenCalled(); // same pane: nothing to adopt
  });

  it("after a reload (no window memory) the line names the account from the pane row", async () => {
    hooks.results["POST /terminals/t1/start-fresh-with-handoff"] = { ...HANDOFF, note: null };
    mount();
    expect(line()).toBeNull();
    fireEvent.click(go());
    await waitFor(() =>
      expect(line()?.textContent).toBe(
        "Continued from “Work Max” — Started fresh with a summary of what you were doing.",
      ),
    );
  });

  it("an answer naming ANOTHER pane hands it to the page", async () => {
    hooks.results["POST /terminals/t1/start-fresh-with-handoff"] = { ...HANDOFF, pane: { id: "t-other", cwd: "C:\\proj" } };
    const { onOpened } = mount();
    fireEvent.click(go());
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(onOpened.mock.calls[0][0]).toMatchObject({ id: "t-other" });
    expect(line("t-other")!.textContent).toMatch(/^Continued from “Work Max” — Started fresh with a summary/);
  });

  it("a refusal shows the daemon's sentence; the strip stays and nothing else changes", async () => {
    hooks.fail["POST /terminals/t1/start-fresh-with-handoff"] = {
      status: 409,
      message: "There is no conversation to summarise in this pane.",
    };
    mount();
    fireEvent.click(go());
    const err = await screen.findByTestId("pane-start-fresh-error-t1");
    expect(err.textContent).toBe("There is no conversation to summarise in this pane.");
    expect(strip()).not.toBeNull();
    expect(line()).toBeNull();
  });

  it("a 409 with a manual fallback is shown VERBATIM, as one paragraph", async () => {
    hooks.fail["POST /terminals/t1/start-fresh-with-handoff"] = { status: 409, message: MANUAL };
    mount();
    fireEvent.click(go());
    const err = await screen.findByTestId("pane-start-fresh-error-t1");
    expect(err.textContent).toBe(MANUAL);
    expect(err.tagName).toBe("SPAN");
    expect(err.className).toContain("whitespace-pre-line");
  });

  it("Dismiss hides it for this failure — a repainted line does not bring it back; a new one does", () => {
    const { rerender } = mount();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(strip()).toBeNull();
    rerender({ ...FAILED, line: "No conversation found with session ID: 1f2e3d (retrying)" });
    expect(strip()).toBeNull();
    rerender({ ...FAILED, since: "2026-10-03T21:00:00Z" });
    expect(strip()).not.toBeNull();
  });
});

/* ---- 2b. the sign-in variant ----------------------------------------------- */

describe("the sign-in variant", () => {
  it("says which account must sign in again and offers Sign in — NO fresh-start button", () => {
    mount(null, vi.fn(), SIGN_IN);
    const s = strip()!;
    expect(s.getAttribute("data-variant")).toBe("sign-in");
    expect(s.textContent).toContain("“Personal” needs to sign in again (Login expired · Please run /login).");
    expect(screen.getByTestId("pane-sign-in-t1").textContent).toBe("Sign in");
    expect(screen.queryByTestId("pane-start-fresh-t1")).toBeNull();
    expect(s.textContent).not.toContain("/clear");
  });

  it("wins over a resume failure at the same time; dismissing it leaves the failure strip", () => {
    mount(FAILED, vi.fn(), SIGN_IN);
    expect(strip()!.getAttribute("data-variant")).toBe("sign-in");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(strip()!.getAttribute("data-variant")).toBeNull();
    expect(screen.getByTestId("pane-start-fresh-t1")).toBeTruthy();
  });

  it("Sign in POSTs that account's signin route and hands the sign-in pane to the page", async () => {
    hooks.results["POST /iron-proxy/accounts/b/signin"] = { terminal_id: "t-signin", name: "Sign in: Personal" };
    hooks.responses["/terminals"] = { terminals: [{ id: "t1" }, { id: "t-signin", cwd: "C:\\Users\\VR", shell: "pwsh" }] };
    const onOpened = vi.fn();
    mount(null, onOpened, SIGN_IN);
    fireEvent.click(screen.getByTestId("pane-sign-in-t1"));
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(onOpened.mock.calls[0][0]).toMatchObject({ id: "t-signin", shell: "pwsh" });
    expect(callsTo("POST", "/iron-proxy/accounts/b/signin")).toHaveLength(1);
    expect(callsTo("POST", "/terminals/t1/start-fresh-with-handoff")).toHaveLength(0);
  });

  it("a pane the list does not show yet: the page is opened on /terminals?focus=<id>", async () => {
    hooks.results["POST /iron-proxy/accounts/b/signin"] = { terminal_id: "t 9" };
    hooks.responses["/terminals"] = { terminals: [] };
    const assign = vi.fn();
    const real = window.location;
    Object.defineProperty(window, "location", { configurable: true, value: { ...real, assign } });
    try {
      const onOpened = vi.fn();
      mount(null, onOpened, SIGN_IN);
      fireEvent.click(screen.getByTestId("pane-sign-in-t1"));
      await waitFor(() => expect(assign).toHaveBeenCalledWith("/terminals?focus=t%209"));
      expect(onOpened).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: real });
    }
  });

  it("a refused sign-in shows the daemon's sentence verbatim", async () => {
    hooks.fail["POST /iron-proxy/accounts/b/signin"] = { status: 409, message: "Iron-Proxy is off.\nTurn it on from Connections." };
    mount(null, vi.fn(), SIGN_IN);
    fireEvent.click(screen.getByTestId("pane-sign-in-t1"));
    const err = await screen.findByTestId("pane-sign-in-error-t1");
    expect(err.textContent).toBe("Iron-Proxy is off.\nTurn it on from Connections.");
    expect(err.className).toContain("whitespace-pre-line");
  });
});

/* ---- 3. through the Build page --------------------------------------------- */

const term = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  cwd: `C:\\proj\\${id}`,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-03T00:00:00Z",
  ...extra,
});

describe("the Build page", () => {
  it("an activity row's resume_failed shows the strip on THAT pane; the press updates its line", async () => {
    const accounts = { anthropic: { id: "b", title: "Personal", source: "iron-proxy", state: "active" } };
    const continued_from = { from_id: "a", from_title: "Work Max", session_id: "1f2e3d" };
    hooks.responses = {
      "/terminals": { terminals: [term("t1", { accounts, continued_from }), term("t2")] },
      "/terminals/shells": { shells: [] },
      "/models": { models: [] },
      "/terminals/ai-clis": { clis: [] },
      "/skills": { skills: [] },
      "/terminals/activity": {
        panes: [{ id: "t1", agent_cli: "claude", accounts, resume_failed: FAILED }, { id: "t2" }],
      },
    };
    hooks.results["POST /terminals/t1/start-fresh-with-handoff"] = HANDOFF;
    render(<TerminalsPage />);
    await waitFor(() => expect(strip("t1")).not.toBeNull());
    expect(strip("t2")).toBeNull();
    fireEvent.click(go("t1"));
    await waitFor(() => expect(strip("t1")).toBeNull());
    expect(line("t1")!.textContent).toMatch(/^Continued from “Work Max” — Started fresh with a summary/);
  });
});

describe("the Build page — sign-in", () => {
  it("an activity row's sign_in_needed shows the variant; Sign in adds and FOCUSES the sign-in pane", async () => {
    const accounts = { anthropic: { id: "b", title: "Personal", source: "iron-proxy", state: "needs-sign-in" } };
    hooks.responses = {
      "/terminals": { terminals: [term("t1", { accounts }), term("t2")] },
      "/terminals/shells": { shells: [] },
      "/models": { models: [] },
      "/terminals/ai-clis": { clis: [] },
      "/skills": { skills: [] },
      "/terminals/activity": { panes: [{ id: "t1", agent_cli: "claude", accounts, sign_in_needed: SIGN_IN }, { id: "t2" }] },
    };
    hooks.results["POST /iron-proxy/accounts/b/signin"] = { terminal_id: "t-signin", name: "Sign in: Personal" };
    render(<TerminalsPage />);
    await waitFor(() => expect(strip("t1")?.getAttribute("data-variant")).toBe("sign-in"));
    // The daemon opened the sign-in pane: the next list read includes it.
    hooks.responses["/terminals"] = { terminals: [term("t1", { accounts }), term("t2"), term("t-signin")] };
    fireEvent.click(screen.getByTestId("pane-sign-in-t1"));
    await waitFor(() => expect(screen.getByTestId("rail-pane-t-signin").style.visibility).toBe("visible"));
    expect(screen.getByTestId("rail-pane-t1").style.visibility).toBe("hidden");
  });
});

/* ---- 4. call sites (source pins) ------------------------------------------- */

describe("TerminalPane and the page wire it", () => {
  it("the pane mounts the strip with the activity row's failure and its own continued_from", () => {
    const pane = src("components", "terminal", "TerminalPane.tsx");
    expect(pane).toMatch(
      /<ResumeFailedStrip\n\s+paneId=\{info\.id\}\n\s+failed=\{paneResumeFailed\}\n\s+signIn=\{paneSignInNeeded\}\n\s+accounts=\{paneAccounts\}\n\s+continuedFrom=\{continuedFromOf\(info\)\}\n\s+onOpened=\{openedPane\}\n\s+\/>/,
    );
  });

  it("the page hands each pane its activity row's resume_failed", () => {
    const page = src("app", "terminals", "page.tsx");
    expect(page).toContain("paneResumeFailed={resumeFailedOf(act)}");
    expect(page).toContain("paneSignInNeeded={signInNeededOf(act)}");
  });
});
