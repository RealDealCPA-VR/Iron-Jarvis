/**
 * v1.310.0 wave 2, track C — the Overview's first five minutes.
 *
 * Findings: overview-buries-first-action, welcome-env-checks-dev-tools,
 * mock-default-trap-cli-ollama (the fix_adjustment wins where it differs).
 *
 * What is pinned, each on the REAL component:
 *
 *  1. Overview: while /onboarding reports next_step !== null (that ALONE is
 *     the gate, so it never flickers on sessions/threads), a first-run strip
 *     (data-testid="first-run-strip") sits directly under the page header,
 *     ABOVE the reactor hero and the module grid. It holds the welcome card,
 *     ONE "Ask Jarvis anything" input that routes to /chat?ask=<text>
 *     (suggest, never act: nothing is POSTed), and the "Try it now" cards,
 *     moved out of the collapsed "Systems & admin" card and pointing at
 *     /chat?ask=<task>. Once next_step is null the strip is gone and the page
 *     is as before (Try it now back in Systems & admin, still starting a run).
 *     A saved task that pins a provider/model keeps the session path + pin.
 *  2. The page subtitle no longer says "daemon".
 *  3. OnboardingWelcome: REQUIRED failures inline; RECOMMENDED rows folded
 *     into a collapsed "Optional extras (n)" line that shows the plain label
 *     (W2-2's `label`) once opened.
 *  4. OnboardingWelcome: model.is_mock + model.usable non-empty -> one press
 *     "Use <label> for answers" that POSTs /onboarding/use-model (W2-1, via
 *     W2-6's useModel) and then says which model now answers; a non-promotion
 *     shows the daemon's plain reason; a 409 shows its sentence. is_mock with
 *     nothing usable says replies are a scripted demo and links to
 *     /connections — never "working". Controls: a real default, and an older
 *     daemon with no `model` block, show none of it.
 *  5. The Handbook carries a "Getting started" section that matches (the
 *     Guide answers from docs/HANDBOOK.md).
 *
 * Harness: `@/lib/api` is a PARTIAL mock (the real module, network verbs
 * replaced) and `useApi` is the REAL hook over it — its cache is reset before
 * every test. `@/lib/onboarding` (track B) is NOT mocked: the press is pinned
 * at the network seam (the POST), whatever shape useModel takes.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const W = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
  postResponses: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = W.responses[path];
    // Unknown reads answer an empty object: the Overview carries many
    // optional panels, and this file pins the first-run surface.
    return r === undefined ? {} : r;
  },
  post: async (path: string, body?: unknown) => {
    W.posts.push({ path, body });
    const r = W.postResponses[path];
    if (typeof r === "function") return (r as (b: unknown) => unknown)(body);
    return r ?? {};
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: W.push,
    replace: W.replace,
    refresh: () => {},
    prefetch: () => {},
    back: () => {},
  }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, prefetch: _p, ...rest }: Record<string, unknown>) =>
      createElement("a", { href, ...rest }, children as never),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "layoutId",
    "whileHover",
    "whileTap",
    "whileFocus",
    "whileInView",
    "viewport",
    "drag",
  ]);
  const cache = new Map<string, (props: Record<string, unknown>) => unknown>();
  const tagFor = (tag: string) => {
    let c = cache.get(tag);
    if (!c) {
      c = (props: Record<string, unknown>) => {
        const rest: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
        return createElement(tag, rest);
      };
      cache.set(tag, c);
    }
    return c;
  };
  return {
    // Mock contract (v1.250.0): every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    LazyMotion: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    domAnimation: {},
    domMax: {},
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

import { ApiError } from "@/lib/api";
import { __resetApiCache } from "@/lib/apiCache";
import OverviewPage from "@/app/page";
import { OnboardingWelcome } from "@/components/OnboardingWelcome";
import { DaemonProvider } from "@/lib/daemon";

// ------------------------------------------------------------------ fixtures

type Step = { key: string; title: string; detail: string; done: boolean; action: string; optional?: boolean };

const step = (key: string, over: Partial<Step> = {}): Step => ({
  key,
  title: `Step ${key}`,
  detail: `Detail for ${key}`,
  done: false,
  action: "",
  ...over,
});

const CHECKLIST = [
  step("connect_ai", { done: true }),
  step("first_session"),
  step("work_with_document"),
  step("teach_style"),
  step("set_up_voice", { optional: true }),
];

type Check = { name: string; ok: boolean; detail: string; fix: string; level?: string; label?: string };

/** /onboarding as W2-2 shapes it. `model` defaults to a real, chosen default. */
function onboarding(over: Record<string, unknown> = {}) {
  return {
    version: "1.310.0",
    first_run: false,
    doctor: { ok: true, checks: [] as Check[] },
    checklist: CHECKLIST,
    next_step: CHECKLIST[1],
    model: {
      default_provider: "anthropic",
      default_model: "claude-opus-4-8",
      is_mock: false,
      usable: [{ provider: "anthropic", label: "Claude" }],
    },
    ...over,
  };
}

const MOCK_WITH_CLAUDE = {
  default_provider: "mock",
  default_model: "claude-opus-4-8",
  is_mock: true,
  usable: [{ provider: "claude-cli", label: "Claude (your subscription)" }],
};
const MOCK_WITH_NOTHING = {
  default_provider: "mock",
  default_model: "claude-opus-4-8",
  is_mock: true,
  usable: [],
};

const FIRST_WIN = {
  title: "Tidy my Downloads",
  task: "List the largest files in my Downloads folder and suggest what's safe to delete",
};

function overviewResponses(onb: unknown, extra: Record<string, unknown> = {}) {
  W.responses = {
    "/onboarding": onb,
    "/health": { status: "ok", version: "1.310.0", providers: [], default_provider: "anthropic", default_model: "claude-opus-4-8" },
    "/metrics": { sessions_evaluated: 0, avg_completion: 0, avg_tool_success_rate: 0, avg_latency_s: 0, total_tool_invocations: 0, event_count: 0 },
    "/sessions?limit=50": { sessions: [] },
    "/vault": { providers: [] },
    "/templates": { templates: [] },
    "/reflex/rules": { rules: [] },
    ...extra,
  };
}

// ------------------------------------------------------------------- helpers

/** An href as {path, q} so a pin does not depend on query-param ORDER. */
function route(href: string | null | undefined): { path: string; q: Record<string, string> } {
  const u = new URL(href ?? "", "http://ij.test");
  return { path: u.pathname, q: Object.fromEntries(u.searchParams) };
}

/** True when `a` comes before `b` in document order. */
function before(a: Element, b: Element): boolean {
  return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

/** Where pressing `el` goes: its <a href>, else the router.push it makes. */
function destinationOf(el: HTMLElement): string | undefined {
  const a = el.closest("a");
  if (a) return a.getAttribute("href") ?? undefined;
  const target = (el.closest("button") as HTMLElement | null) ?? el;
  fireEvent.click(target);
  const call = W.push.mock.calls.at(-1);
  return call ? String(call[0]) : undefined;
}

/** Absent, or present but hidden (collapsed with `hidden` / display:none). */
function expectNotShown(text: string | RegExp) {
  const found = screen.queryAllByText(text);
  for (const el of found) expect(el).not.toBeVisible();
}

const sessionPosts = () => W.posts.filter((p) => p.path === "/sessions" || p.path.startsWith("/chat"));
const useModelPosts = () => W.posts.filter((p) => p.path === "/onboarding/use-model");

beforeEach(() => {
  __resetApiCache();
  W.responses = {};
  W.postResponses = {};
  W.posts.length = 0;
  W.push.mockReset();
  W.replace.mockReset();
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

// ============================================== 1. the first-run strip

describe("Overview: a first-run strip leads while setup is unfinished", () => {
  it("sits under the header, ABOVE the reactor hero and the module grid, holding the welcome card", async () => {
    overviewResponses(onboarding());
    render(<OverviewPage />);
    const strip = await screen.findByTestId("first-run-strip", undefined, { timeout: 4000 });

    // The welcome card lives IN the strip (and only once on the page).
    await within(strip).findByText("Finish setting up");
    expect(screen.getAllByText("Finish setting up")).toHaveLength(1);

    const hero = screen.getByText("All systems nominal");
    const grid = screen.getByTestId("app-desk");
    const title = screen.getByRole("heading", { name: "Overview" });
    expect(before(title, strip)).toBe(true);
    expect(before(strip, hero)).toBe(true);
    expect(before(strip, grid)).toBe(true);
  });

  it("the press that finishes setup keeps its confirmation on the page (the strip is latched, review v1.310.0)", async () => {
    // Every step done but connect_ai, demo default, a signed-in Claude: the
    // upgrade cohort. The press ticks the last step, so the shared re-read
    // answers next_step null — an unlatched gate unmounted the strip and the
    // "now answers your questions" line went with it.
    const lastOpen = CHECKLIST.map((s) => ({ ...s, done: s.key !== "connect_ai" }));
    let reads = 0;
    let current: unknown = onboarding({ checklist: lastOpen, next_step: lastOpen[0], model: MOCK_WITH_CLAUDE });
    overviewResponses(null);
    Object.defineProperty(W.responses, "/onboarding", {
      enumerable: true,
      configurable: true,
      get: () => {
        reads += 1;
        return current;
      },
    });
    W.postResponses["/onboarding/use-model"] = () => {
      current = onboarding({
        checklist: lastOpen.map((s) => ({ ...s, done: true })),
        next_step: null,
        model: { ...MOCK_WITH_CLAUDE, default_provider: "anthropic", is_mock: false },
      });
      return { promoted: { provider: "anthropic", model: "claude-opus-4-8" }, reason: "" };
    };
    render(<OverviewPage />);
    const press = /use claude \(your subscription\) for answers/i;
    const strip = await screen.findByTestId("first-run-strip", undefined, { timeout: 4000 });
    await within(strip).findByRole("button", { name: press });
    const readsBefore = reads;
    // Re-find the press on each try: the page settles its first reads in a
    // few renders, and a click on a node a render just replaced dispatches
    // nothing (the CLAUDE.md swallowed-click trap). Clicks only while nothing
    // has been posted, so the press is made exactly once.
    await waitFor(() => {
      if (useModelPosts().length === 0) {
        fireEvent.click(within(screen.getByTestId("first-run-strip")).getByRole("button", { name: press }));
      }
      expect(useModelPosts()).toHaveLength(1);
    });
    await waitFor(() => expect(reads).toBeGreaterThan(readsBefore));
    await waitFor(() => expect(screen.getByText(/now answers your questions/i)).toBeInTheDocument());
    expect(screen.getByTestId("first-run-strip")).toBeInTheDocument();
  });

  it("'Ask Jarvis anything' routes to /chat?ask=<text> and starts nothing (suggest, never act)", async () => {
    overviewResponses(onboarding());
    render(<OverviewPage />);
    const strip = await screen.findByTestId("first-run-strip", undefined, { timeout: 4000 });
    const input = within(strip).getByRole("textbox", { name: /ask jarvis anything/i });

    // Blank submit goes nowhere.
    const form = input.closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    expect(W.push).not.toHaveBeenCalled();

    const text = "Plan Q4 taxes & budget";
    fireEvent.change(input, { target: { value: `  ${text}  ` } });
    fireEvent.submit(form!);
    await waitFor(() => expect(W.push).toHaveBeenCalledTimes(1));
    expect(route(String(W.push.mock.calls[0][0]))).toEqual({ path: "/chat", q: { ask: text } });
    expect(sessionPosts()).toEqual([]);
  });

  it("'Try it now' moved into the strip: a card opens /chat?ask=<task>, posts no session, and is not also in Systems & admin", async () => {
    overviewResponses(onboarding());
    // Open "Systems & admin" so a copy left behind in it would render.
    window.localStorage.setItem("ij_ov_admin", "1");
    render(<OverviewPage />);
    const strip = await screen.findByTestId("first-run-strip", undefined, { timeout: 4000 });
    const card = within(strip).getByText(FIRST_WIN.title);

    expect(route(destinationOf(card))).toEqual({ path: "/chat", q: { ask: FIRST_WIN.task } });
    expect(sessionPosts()).toEqual([]);
    expect(screen.getAllByText(FIRST_WIN.title)).toHaveLength(1);
  });

  it("the gate is next_step ALONE: not first_run, sessions on the list, welcome dismissed — the strip still leads", async () => {
    overviewResponses(onboarding({ first_run: false }), {
      "/sessions?limit=50": {
        sessions: [
          { id: "s1", task: "an old run", status: "completed", agent_type: "builder", created_at: "2026-10-06T10:00:00", finished_at: "2026-10-06T10:01:00" },
        ],
      },
    });
    window.localStorage.setItem("ij_onboarding_dismissed", "1");
    render(<OverviewPage />);
    const strip = await screen.findByTestId("first-run-strip", undefined, { timeout: 4000 });
    expect(within(strip).getByRole("textbox", { name: /ask jarvis anything/i })).toBeInTheDocument();
  });

  it("control: next_step null -> no strip, no ask box; 'Try it now' is back in Systems & admin and still starts a run", async () => {
    overviewResponses(onboarding({ next_step: null, checklist: CHECKLIST.map((s) => ({ ...s, done: true })) }));
    W.postResponses["/sessions"] = { id: "s-new" };
    window.localStorage.setItem("ij_ov_admin", "1");
    render(<OverviewPage />);
    const card = await screen.findByText(FIRST_WIN.title, undefined, { timeout: 4000 });

    expect(screen.queryByTestId("first-run-strip")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /ask jarvis anything/i })).toBeNull();
    // The page is as before: hero, then grid; the card still runs a session.
    expect(before(screen.getByText("All systems nominal"), screen.getByTestId("app-desk"))).toBe(true);
    fireEvent.click(card.closest("button")!);
    await waitFor(() => expect(W.push).toHaveBeenCalledWith("/sessions/s-new"));
    expect(W.posts.find((p) => p.path === "/sessions")?.body).toMatchObject({ task: FIRST_WIN.task });
  });

  it("control: a saved task that pins a provider/model keeps the session path and carries the pin", async () => {
    overviewResponses(onboarding(), {
      "/templates": {
        templates: [
          { id: "t1", name: "Weekly recap", agent_type: "builder", task: "Recap my week", provider: "ollama", model: "llama3.2", created_at: "2026-10-01T00:00:00" },
        ],
      },
    });
    W.postResponses["/sessions"] = { id: "s-pinned" };
    window.localStorage.setItem("ij_ov_admin", "1");
    render(<OverviewPage />);
    const tpl = await screen.findByText("Weekly recap", undefined, { timeout: 4000 });
    fireEvent.click(tpl.closest("button")!);
    await waitFor(() => expect(W.push).toHaveBeenCalledWith("/sessions/s-pinned"));
    expect(W.posts.find((p) => p.path === "/sessions")?.body).toMatchObject({
      task: "Recap my week",
      provider: "ollama",
      model: "llama3.2",
    });
  });
});

// ============================================== 2. plain words in the subtitle

describe("Overview subtitle speaks plainly", () => {
  it("the header description no longer says 'daemon'", async () => {
    overviewResponses(onboarding({ next_step: null }));
    render(<OverviewPage />);
    const h1 = await screen.findByRole("heading", { name: "Overview" });
    const describedBy =
      h1.getAttribute("aria-describedby") ??
      h1.closest("[aria-describedby]")?.getAttribute("aria-describedby") ??
      h1.querySelector("[aria-describedby]")?.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const tip = document.getElementById(describedBy!);
    expect(tip?.textContent?.trim()).toBeTruthy();
    expect(tip!.textContent!).not.toMatch(/daemon/i);
  });
});

// ============================================== 3. required inline, extras folded

const REQUIRED_FAIL: Check = {
  name: "data_dir",
  ok: false,
  level: "required",
  label: "Where your work is saved",
  detail: "the data folder is not writable",
  fix: "Choose a folder you can write to in Settings",
};
const OLD_DOC: Check = {
  name: "antiword",
  ok: false,
  level: "recommended",
  label: "Reading old .doc files",
  detail: "antiword not found",
  fix: "Install Word or antiword",
};
const SCANS: Check = {
  name: "tesseract",
  ok: false,
  level: "recommended",
  label: "Reading scanned pages",
  detail: "tesseract not found",
  fix: "Install Tesseract OCR",
};
const PASSING: Check = { name: "database", ok: true, level: "required", label: "Your database", detail: "ok", fix: "" };

describe("OnboardingWelcome: required failures inline, recommended ones folded", () => {
  it("a REQUIRED failure shows with its fix; RECOMMENDED rows hide behind 'Optional extras (2)' and open with plain labels", async () => {
    W.responses["/onboarding"] = onboarding({
      doctor: { ok: false, checks: [REQUIRED_FAIL, OLD_DOC, SCANS, PASSING] },
    });
    render(<OnboardingWelcome />);
    await screen.findByText("Finish setting up");

    expect(screen.getByText(/the data folder is not writable/)).toBeVisible();
    expect(screen.getByText(/Choose a folder you can write to/)).toBeVisible();

    const extras = screen.getByRole("button", { name: /optional extras \(2\)/i });
    expect(extras).toHaveAttribute("aria-expanded", "false");
    expectNotShown(/Reading old \.doc files/);
    expectNotShown(/antiword not found/);
    expectNotShown(/Reading scanned pages/);

    fireEvent.click(extras);
    expect(extras).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Reading old \.doc files/)).toBeVisible();
    expect(screen.getByText(/Reading scanned pages/)).toBeVisible();
  });

  it("only RECOMMENDED failures -> no amber 'Environment checks' list, just the folded line", async () => {
    W.responses["/onboarding"] = onboarding({ doctor: { ok: true, checks: [OLD_DOC, PASSING] } });
    render(<OnboardingWelcome />);
    await screen.findByText("Finish setting up");

    expect(screen.getByRole("button", { name: /optional extras \(1\)/i })).toBeInTheDocument();
    expect(screen.queryByText(/Environment checks/i)).toBeNull();
    expectNotShown(/antiword not found/);
    expectNotShown(/Install Word or antiword/);
  });

  it("control: nothing failing -> no extras line and no checks block", async () => {
    W.responses["/onboarding"] = onboarding({ doctor: { ok: true, checks: [PASSING] } });
    render(<OnboardingWelcome />);
    await screen.findByText("Finish setting up");
    expect(screen.queryByRole("button", { name: /optional extras/i })).toBeNull();
    expect(screen.queryByText(/Environment checks/i)).toBeNull();
  });
});

// ============================================== 4. the honest model card

describe("OnboardingWelcome: the mock default is named, and one press fixes it", () => {
  it("is_mock + a usable model -> 'Use <label> for answers' posts /onboarding/use-model, then names the model that answers", async () => {
    W.responses["/onboarding"] = onboarding({ model: MOCK_WITH_CLAUDE });
    W.postResponses["/onboarding/use-model"] = () => {
      // The daemon really switched: a re-read of /onboarding now reports the
      // real default, so the confirmation must survive that reload (it is
      // the press's own result, not derived from the refreshed block).
      // (implementer, v1.310.0: the re-read carries a step title the first
      // read did not, so the test can WAIT for the reload to land — without
      // it the assertion below ran before the re-read and the "survives"
      // claim was unchecked; a mutation hiding the card once is_mock turned
      // false stayed green.)
      W.responses["/onboarding"] = onboarding({
        checklist: CHECKLIST.map((s) => (s.key === "connect_ai" ? { ...s, title: "Model chosen" } : s)),
      });
      return { promoted: { provider: "anthropic", model: "claude-opus-4-8" }, reason: "" };
    };
    render(<OnboardingWelcome />);
    const btn = await screen.findByRole("button", { name: /use claude \(your subscription\) for answers/i });
    const card = screen.getByTestId("welcome-model");
    expect(card.textContent ?? "").not.toMatch(/\bworking\b/i);

    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId("welcome-model")).toHaveTextContent(/claude-opus-4-8/));
    // The re-read has landed (is_mock is false now) and the card still says it.
    await screen.findByText("Model chosen");
    expect(screen.getByTestId("welcome-model")).toHaveTextContent(/claude-opus-4-8/);
    expect(useModelPosts()).toEqual([{ path: "/onboarding/use-model", body: { provider: "claude-cli" } }]);
  });

  it("the daemon declines (the user already chose) -> its plain reason is shown, nothing claims a switch", async () => {
    W.responses["/onboarding"] = onboarding({ model: MOCK_WITH_CLAUDE });
    const reason = "You already chose Ollama for answers, so nothing changed.";
    W.postResponses["/onboarding/use-model"] = () => ({ promoted: null, reason });
    render(<OnboardingWelcome />);
    fireEvent.click(await screen.findByRole("button", { name: /for answers/i }));
    await waitFor(() => expect(screen.getByTestId("welcome-model")).toHaveTextContent(reason));
    expect(screen.getByTestId("welcome-model").textContent ?? "").not.toMatch(/now answer/i);
  });

  it("a 409 (provider unavailable) shows the daemon's sentence", async () => {
    W.responses["/onboarding"] = onboarding({ model: MOCK_WITH_CLAUDE });
    const msg = "Claude (your subscription) is not available right now.";
    W.postResponses["/onboarding/use-model"] = () => {
      throw new ApiError(msg, 409);
    };
    render(<OnboardingWelcome />);
    fireEvent.click(await screen.findByRole("button", { name: /for answers/i }));
    await waitFor(() => expect(screen.getByTestId("welcome-model")).toHaveTextContent(msg));
    // The press is still offered (nothing changed) — no claim of a switch.
    expect(screen.getByTestId("welcome-model").textContent ?? "").not.toMatch(/now answer/i);
  });

  it("is_mock with nothing usable -> says replies are a scripted demo and links to /connections; never 'working'", async () => {
    W.responses["/onboarding"] = onboarding({ model: MOCK_WITH_NOTHING });
    render(<OnboardingWelcome />);
    const card = await screen.findByTestId("welcome-model");
    expect(card.textContent ?? "").toMatch(/scripted demo/i);
    expect(card.textContent ?? "").not.toMatch(/\bworking\b/i);
    const link = within(card).getByRole("link");
    expect(link).toHaveAttribute("href", "/connections");
    expect(within(card).queryByRole("button", { name: /for answers/i })).toBeNull();
  });

  it("control: a real default (is_mock false) -> no model card, no press, no demo warning", async () => {
    W.responses["/onboarding"] = onboarding();
    render(<OnboardingWelcome />);
    await screen.findByText("Finish setting up");
    expect(screen.queryByTestId("welcome-model")).toBeNull();
    expect(screen.queryByRole("button", { name: /for answers/i })).toBeNull();
    expect(screen.queryByText(/scripted demo/i)).toBeNull();
    expect(useModelPosts()).toEqual([]);
  });

  it("control: an older daemon with no `model` block renders the checklist and no model card", async () => {
    const legacy = onboarding();
    delete (legacy as Record<string, unknown>).model;
    W.responses["/onboarding"] = legacy;
    render(<OnboardingWelcome />);
    await screen.findByText("Finish setting up");
    expect(screen.getByText("Step first_session")).toBeInTheDocument();
    expect(screen.queryByTestId("welcome-model")).toBeNull();
  });
});

// ============================================== 4b. review round (v1.310.0)

/** The row (its <li>) that holds a "Use <label> for answers" press. */
function rowOf(name: RegExp): HTMLElement {
  const li = screen.getByRole("button", { name }).closest("li");
  expect(li).not.toBeNull();
  return li as HTMLElement;
}

describe("OnboardingWelcome: every usable model is offered, each saying where the words go", () => {
  const BOTH = {
    default_provider: "mock",
    default_model: "claude-opus-4-8",
    is_mock: true,
    // The daemon's order: CLIs first, then local servers (readiness.py).
    usable: [
      { provider: "claude-cli", label: "Claude (your Claude Code sign-in)", local: false },
      { provider: "ollama", label: "Ollama on this PC", local: true },
    ],
  };

  it("a cloud sign-in AND Ollama -> BOTH presses; the one pressed is the one posted", async () => {
    W.responses["/onboarding"] = onboarding({ model: BOTH });
    W.postResponses["/onboarding/use-model"] = () => ({
      promoted: { provider: "ollama", model: "llama3.2" },
      reason: "",
    });
    render(<OnboardingWelcome />);
    const card = await screen.findByTestId("welcome-model");
    const presses = within(card).getAllByRole("button", { name: /for answers/i });
    expect(presses.map((b) => b.textContent?.trim())).toEqual([
      "Use Claude (your Claude Code sign-in) for answers",
      "Use Ollama on this PC for answers",
    ]);

    fireEvent.click(within(card).getByRole("button", { name: /use ollama on this pc for answers/i }));
    await waitFor(() => expect(screen.getByTestId("welcome-model")).toHaveTextContent(/llama3\.2/));
    expect(useModelPosts()).toEqual([{ path: "/onboarding/use-model", body: { provider: "ollama" } }]);
  });

  it("the privacy line is said BEFORE the press, per row: Ollama stays here, Claude is sent to Claude", async () => {
    W.responses["/onboarding"] = onboarding({ model: BOTH });
    render(<OnboardingWelcome />);
    await screen.findByTestId("welcome-model");

    const local = rowOf(/use ollama on this pc for answers/i);
    expect(local).toHaveTextContent(/stays? here/i);
    expect(local.textContent ?? "").not.toMatch(/sent to/i);

    const cloud = rowOf(/use claude \(your claude code sign-in\) for answers/i);
    expect(cloud).toHaveTextContent(/sent to Claude \(your Claude Code sign-in\)/);
    expect(cloud.textContent ?? "").not.toMatch(/stays? here|stay with you/i);
    expect(useModelPosts()).toEqual([]);
  });

  it("the daemon's `local` flag decides, not the provider name; a label reads right mid-sentence", async () => {
    W.responses["/onboarding"] = onboarding({
      model: {
        ...MOCK_WITH_NOTHING,
        usable: [{ provider: "custom", label: "Your own model server", local: false }],
      },
    });
    render(<OnboardingWelcome />);
    await screen.findByTestId("welcome-model");
    const row = rowOf(/use your own model server for answers/i);
    expect(row).toHaveTextContent("Your questions will be sent to your own model server to be answered.");
    expect(row.textContent ?? "").not.toMatch(/stays? here|stay with you/i);
  });
});

describe("OnboardingWelcome: the confirmation names what was pressed (review round 2)", () => {
  const CLAUDE_SIGNIN = {
    ...MOCK_WITH_NOTHING,
    usable: [{ provider: "claude-cli", label: "Claude (your Claude Code sign-in)", local: false }],
  };

  it("a CLI promoted to itself (model 'subscription') is named by its label, never 'subscription now answers'", async () => {
    W.responses["/onboarding"] = onboarding({ model: CLAUDE_SIGNIN });
    // settings.py's _CLI_INHERITS branch when the user also holds an API key.
    W.postResponses["/onboarding/use-model"] = () => ({
      promoted: { provider: "claude-cli", model: "subscription" },
      reason: "",
    });
    render(<OnboardingWelcome />);
    fireEvent.click(await screen.findByRole("button", { name: /use claude \(your claude code sign-in\) for answers/i }));
    await waitFor(() =>
      expect(screen.getByTestId("welcome-model")).toHaveTextContent(
        /Claude \(your Claude Code sign-in\) now answers your questions/,
      ),
    );
    const said = screen.getByTestId("welcome-model").textContent ?? "";
    expect(said).not.toMatch(/subscription now answers/i);
    expect(said).not.toMatch(/\bsubscription\b/i);
  });

  it("a real model id rides beside the label in the code font", async () => {
    W.responses["/onboarding"] = onboarding({ model: CLAUDE_SIGNIN });
    W.postResponses["/onboarding/use-model"] = () => ({
      promoted: { provider: "anthropic", model: "claude-opus-4-8" },
      reason: "",
    });
    render(<OnboardingWelcome />);
    fireEvent.click(await screen.findByRole("button", { name: /for answers/i }));
    const id = await screen.findByText("claude-opus-4-8");
    expect(id.className).toMatch(/font-mono/);
    expect(screen.getByTestId("welcome-model")).toHaveTextContent(
      /Claude \(your Claude Code sign-in\) now answers your questions \(claude-opus-4-8\)\./,
    );
  });

  it("a dismissal stored by an earlier version does not hide the one press while the demo answers", async () => {
    window.localStorage.setItem("ij_onboarding_dismissed", "1");
    W.responses["/onboarding"] = onboarding({ model: CLAUDE_SIGNIN });
    render(<OnboardingWelcome />);
    // The checklist stays put away (the chip), the press stays offered.
    expect(await screen.findByRole("button", { name: /use claude \(your claude code sign-in\) for answers/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /finish setup/i })).toBeInTheDocument();
    expect(screen.queryByText("Step first_session")).toBeNull();
  });

  it("control: dismissed with a real default -> only the chip", async () => {
    window.localStorage.setItem("ij_onboarding_dismissed", "1");
    W.responses["/onboarding"] = onboarding();
    render(<OnboardingWelcome />);
    await screen.findByRole("button", { name: /finish setup/i });
    expect(screen.queryByTestId("welcome-model")).toBeNull();
  });
});

describe("OnboardingWelcome: the developer toolchain never reaches this card", () => {
  it("a failing `uv` row whose fix pastes a remote script shows neither inline nor under Optional extras", async () => {
    const UV: Check = {
      name: "uv",
      ok: false,
      level: "required",
      label: "uv",
      detail: "uv not found on PATH",
      fix: 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"',
    };
    const UV_REC: Check = { ...UV, name: "pnpm", level: "recommended", detail: "pnpm not found", fix: "irm get.pnpm.io/install.ps1 | iex" };
    W.responses["/onboarding"] = onboarding({ doctor: { ok: false, checks: [UV, UV_REC, OLD_DOC, PASSING] } });
    render(<OnboardingWelcome />);
    await screen.findByText("Finish setting up");

    // Only the real extra is counted, and nothing needs the user's attention.
    const extras = screen.getByRole("button", { name: /optional extras \(1\)/i });
    expect(screen.queryByText(/Needs your attention/i)).toBeNull();
    fireEvent.click(extras);
    expect(screen.getByText(/Reading old \.doc files/)).toBeVisible();
    expect(screen.queryByText(/uv not found/)).toBeNull();
    expect(screen.queryByText(/pnpm not found/)).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(/\birm\b|\biex\b/);
  });
});

describe("Overview hero: the offline demo is not named as a model", () => {
  it("default_provider 'mock' -> 'No model chosen yet', never 'mock/<model>'", async () => {
    overviewResponses(onboarding({ next_step: null }), {
      "/health": { status: "ok", version: "1.310.0", providers: [], default_provider: "mock", default_model: "claude-opus-4-8" },
    });
    // The hero reads the app's ONE shared /health poll (the real provider
    // over the mocked get).
    render(
      <DaemonProvider>
        <OverviewPage />
      </DaemonProvider>,
    );
    const said = await screen.findByText("No model chosen yet", undefined, { timeout: 4000 });
    // Prose, not an id: not set in the code font.
    expect(said.className).not.toMatch(/font-mono/);
    expect(document.body.textContent ?? "").not.toMatch(/mock\//);
  });

  it("an EMPTY default_provider is untouched too -> 'No model chosen yet', never '/<model>'", async () => {
    overviewResponses(onboarding({ next_step: null }), {
      "/health": { status: "ok", version: "1.310.0", providers: [], default_provider: "", default_model: "claude-opus-4-8" },
    });
    render(
      <DaemonProvider>
        <OverviewPage />
      </DaemonProvider>,
    );
    await screen.findByText("No model chosen yet", undefined, { timeout: 4000 });
    expect(screen.queryByText("/claude-opus-4-8")).toBeNull();
  });

  it("control: a real default still reads provider/model in the code font", async () => {
    overviewResponses(onboarding({ next_step: null }));
    render(
      <DaemonProvider>
        <OverviewPage />
      </DaemonProvider>,
    );
    const id = await screen.findByText("anthropic/claude-opus-4-8", undefined, { timeout: 4000 });
    expect(id.className).toMatch(/font-mono/);
    expect(screen.queryByText("No model chosen yet")).toBeNull();
  });
});

// ============================================== 5. the Handbook matches

describe("the Handbook's Getting started section matches the first five minutes", () => {
  // CRLF normalised at the reader (this checkout is autocrlf).
  const handbook = readFileSync(join(process.cwd(), "..", "docs", "HANDBOOK.md"), "utf8").replace(/\r\n/g, "\n");

  function section(): string {
    const start = handbook.search(/^## Getting started\b/m);
    expect(start).toBeGreaterThan(-1);
    const rest = handbook.slice(start + 3);
    const end = rest.search(/^## /m);
    return end === -1 ? rest : rest.slice(0, end);
  }

  it("exists before the surfaces tour and describes the strip, the one press and the folded extras", () => {
    const start = handbook.search(/^## Getting started\b/m);
    const surfaces = handbook.search(/^## The surfaces/m);
    expect(start).toBeGreaterThan(-1);
    expect(start).toBeLessThan(surfaces);
    const s = section();
    expect(s).toMatch(/Ask Jarvis anything/);
    expect(s).toMatch(/for answers/i);
    expect(s).toMatch(/scripted demo/i);
    expect(s).toMatch(/Optional extras/i);
  });

  it("never tells an end user to install developer tools or paste a remote script", () => {
    const s = section();
    expect(s).not.toMatch(/\birm\b|\biex\b/);
    expect(s).not.toMatch(/\b(pnpm|uv)\b/);
  });
});
