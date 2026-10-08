/**
 * v1.313.0 — UX wave 1 ("foundation & trust"), track T3: the first run.
 *
 * Findings (the verifier's fix_adjustment wins over the proposed fix):
 *  - first-run-connect-step-wrong-cta
 *  - overview-setup-says-it-twice
 *  - hero-status-contradicts-demo
 *  - opencode-press-no-privacy-line-raw-id
 *  - door-count-reads-as-unread
 *  - phone-checklist-rows-cramped
 *
 * Each `it` pins ONE behaviour a user sees, on the REAL component. Every
 * capability stays: each usable model keeps its own explicit press with its
 * where-your-words-go line, Connections stays reachable, the user's choice is
 * never made for them (no press posts /onboarding/use-model on its own).
 *
 * INTERFACE the implementer follows (decided here, pinned below):
 *  1. connect_ai row, when `step.action === "Choose it for answers"` AND the
 *     model card renders presses: the row's control is labelled with
 *     step.action (no "Connect a model", no /connections link as its CTA). A
 *     press SCROLLS TO and FOCUSES the model card ([data-testid=welcome-model]
 *     itself — give it tabIndex={-1} — or the first press inside it). It
 *     POSTS NOTHING (it never chooses for the user).
 *  2. ONE solid primary in the welcome card: exactly one button/link carries
 *     the bare `bg-accent` class token (tints like `bg-accent/[0.07]` are not
 *     solid). With presses on screen that is the connect_ai row's CTA; the
 *     offer presses are OUTLINED (a `border` token, no bare `bg-accent`) and
 *     ALL share one className — order is not a recommendation.
 *  3. Press labels keep the daemon's full label ("Use Claude (your Claude
 *     Code sign-in) for answers") — the disambiguator stays.
 *  4. The where-line beside a claude-cli / codex-cli press does not repeat the
 *     label; it names the company the words go to (Anthropic / OpenAI), worded
 *     from lib/onboarding's WHERE map. Custom / local rows keep their existing
 *     whereItGoes sentences (WHERE has no "custom" key).
 *  5. "scripted demo" is said ONCE in the card while presses render (the
 *     connect_ai row's daemon detail OR the card sentence — not both). When no
 *     one-press choice exists the row keeps its own detail and its
 *     /connections action (Grok/OpenCode, nothing usable, fresh install).
 *  6. Checklist <li>: `flex-wrap` + `sm:flex-nowrap` (the CTA drops under the
 *     text on a phone).
 *  7. Overview hero: the status line is a `<p role="status">` (PageHeader owns
 *     the page's only <h1>). Under a demo default (isDemoDefault) and nothing
 *     ranked ahead of it, it reads /choose a model/i and never "nominal"; a
 *     failing loop / running task still ranks AHEAD. A healthy real default
 *     reads "All good — ready when you are". The hero meta row drops its
 *     version (the header chip keeps it). Tiles: "Problems today" (title
 *     carries "Failures 24h") and "Disk space free" (title carries "Free
 *     disk").
 *  8. HealthCard labels: "Tasks reviewed", "Finished", "Tools worked",
 *     "Typical reply time", each with its raw metric name in a `title`.
 *     (components/overview/HealthCard.tsx — ownership to be granted.)
 *  9. ConnectDoors (chat empty state / wizard): a provider the one-press
 *     route refuses (opencode-cli, grok-cli — readiness.USE_MODEL_CLIS is
 *     claude-cli + codex-cli) gets NO press; it is named in plain words
 *     ("OpenCode") beside a link to /connections; no raw id is printed. Every
 *     press that remains has a non-empty where-line. claude-cli/codex-cli
 *     presses use checklist.py's labels, so Chat and the Overview say the
 *     same words.
 * 10. AppGrid doors: no corner number bubble; the count is a caption
 *     "<n> modules" (components/overview/AppGrid.tsx — ownership to be granted).
 *
 * Harness: lifted from overview-wave2-v1310 — `@/lib/api` is a PARTIAL mock
 * (network verbs replaced), `useApi` and DaemonProvider are REAL over it.
 */

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

import { __resetApiCache } from "@/lib/apiCache";
import OverviewPage from "@/app/page";
import { OnboardingWelcome } from "@/components/OnboardingWelcome";
import { ConnectDoors } from "@/components/onboarding/ConnectDoors";
import { AppGrid } from "@/components/overview/AppGrid";
import { HealthCard } from "@/components/overview/HealthCard";
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

/** checklist.py's connect_ai row in the one-press state (verbatim). */
const CONNECT_ONE_PRESS = step("connect_ai", {
  title: "Connect your AI",
  detail:
    "A model is connected, but replies are still a scripted demo until you choose it for answers. One press does it.",
  action: "Choose it for answers",
});

/** checklist.py's connect_ai row when something is connected that the
 *  one-press door cannot choose (a Grok / OpenCode sign-in). */
const CONNECT_ON_CONNECTIONS = step("connect_ai", {
  title: "Connect your AI",
  detail:
    "A model is connected, but replies are still a scripted demo until it's chosen for answers. Pick which model answers you on the Connections page.",
  action: "Open the Connections page",
});

function checklist(connect: Step): Step[] {
  return [
    connect,
    step("first_session"),
    step("work_with_document"),
    step("teach_style"),
    step("set_up_voice", { optional: true }),
  ];
}

const CLAUDE = { provider: "claude-cli", label: "Claude (your Claude Code sign-in)", local: false };
const CHATGPT = { provider: "codex-cli", label: "ChatGPT (your Codex sign-in)", local: false };
const OLLAMA = { provider: "ollama", label: "Ollama (free, runs on this PC)", local: true };

/** /onboarding on a demo default with real models ready for one press. */
function onePress(usable = [CLAUDE, CHATGPT], over: Record<string, unknown> = {}) {
  const list = checklist(CONNECT_ONE_PRESS);
  return {
    version: "1.313.0",
    first_run: false,
    doctor: { ok: true, checks: [] },
    checklist: list,
    next_step: list[0],
    model: { default_provider: "mock", default_model: "claude-opus-4-8", is_mock: true, usable },
    ...over,
  };
}

/** The <li> of the checklist row titled `title`. */
function checklistRow(title: string): HTMLElement {
  const li = screen.getByText(title).closest("li");
  expect(li, `no checklist row titled ${title}`).not.toBeNull();
  return li as HTMLElement;
}

/** The whole getting-started card (the outermost element holding the dismiss X). */
function welcomeCard(): HTMLElement {
  const x = screen.getByRole("button", { name: /dismiss/i });
  // The card is the nearest ancestor that also holds the checklist <ol>.
  let el: HTMLElement | null = x.parentElement;
  while (el && !el.querySelector("ol")) el = el.parentElement;
  expect(el).not.toBeNull();
  return el as HTMLElement;
}

/** Interactive controls (button or link) whose class list has the bare
 *  `bg-accent` token: the solid cyan primary. Tints (`bg-accent/[0.07]`) are
 *  not solid. */
function solidPrimaries(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("button, a")).filter((el) =>
    (el.getAttribute("class") ?? "").split(/\s+/).includes("bg-accent"),
  );
}

const pressesIn = (root: HTMLElement) => within(root).queryAllByRole("button", { name: /^use .+ for answers$/i });

const useModelPosts = () => W.posts.filter((p) => p.path === "/onboarding/use-model");

function overviewResponses(onb: unknown, extra: Record<string, unknown> = {}) {
  W.responses = {
    "/onboarding": onb,
    "/health": {
      status: "ok",
      version: "1.310.0",
      providers: [],
      default_provider: "mock",
      default_model: "claude-opus-4-8",
    },
    "/metrics": {
      sessions_evaluated: 0,
      avg_completion: 0,
      avg_tool_success_rate: 0,
      avg_latency_s: 0,
      total_tool_invocations: 0,
      event_count: 0,
    },
    "/sessions?limit=50": { sessions: [] },
    "/vault": { providers: [] },
    "/templates": { templates: [] },
    "/reflex/rules": { rules: [] },
    "/diagnostics": { background_loops: {}, mcp_servers: [] },
    "/diagnostics/reliability": { disk: { free: 50_000_000_000, total: 100_000_000_000 }, recent_provider_failures: 0 },
    ...extra,
  };
}

function renderOverview() {
  return render(
    <DaemonProvider>
      <OverviewPage />
    </DaemonProvider>,
  );
}

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

// ======================================= 1. the connect step leads to the press

describe("connect_ai: with a usable model, the row's action is the one-press choice in place", () => {
  it("the row is labelled with the daemon's step.action and never sends the user to /connections as 'Connect a model'", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    const row = checklistRow("Connect your AI");
    expect(within(row).queryByText(/connect a model/i)).toBeNull();
    expect(row.querySelector('a[href="/connections"]')).toBeNull();
    expect(within(row).getByRole("button", { name: /choose it for answers/i })).toBeInTheDocument();
  });

  it("pressing it scrolls to and focuses the choices — and chooses NOTHING for the user", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    const cta = within(checklistRow("Connect your AI")).getByRole("button", { name: /choose it for answers/i });
    const scroll = Element.prototype.scrollIntoView as unknown as ReturnType<typeof vi.fn>;
    scroll.mockClear();
    fireEvent.click(cta);

    const card = screen.getByTestId("welcome-model");
    await waitFor(() => expect(card.contains(document.activeElement)).toBe(true));
    const scrolled = scroll.mock.instances as unknown as Element[];
    expect(scrolled.some((el) => card === el || card.contains(el))).toBe(true);
    // The choice is the user's: no promotion is posted by this press.
    expect(useModelPosts()).toEqual([]);
    // Every offer is still there to be pressed.
    expect(pressesIn(card)).toHaveLength(2);
  });

  it("ONE solid primary in the card: the row's CTA; both offers outlined and styled alike (order is not a recommendation)", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    const solids = solidPrimaries(welcomeCard());
    expect(solids.map((e) => e.textContent?.trim())).toHaveLength(1);
    expect(solids[0].textContent ?? "").toMatch(/choose it for answers/i);

    const offers = pressesIn(screen.getByTestId("welcome-model"));
    expect(offers).toHaveLength(2);
    const [a, b] = offers.map((o) => o.getAttribute("class") ?? "");
    expect(a).toBe(b);
    expect(a.split(/\s+/)).not.toContain("bg-accent");
    expect(a.split(/\s+/)).toContain("border");
  });

  // v1.313.0 review (capability regression): the row's control scrolls to
  // the presses instead of linking to Connections, so the card itself must
  // keep Connections one click away (API key, OpenCode, Grok) — as a quiet
  // text link, never a second solid button.
  it("the one-press card still reaches Connections in one click, as a quiet link (not a second primary)", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    const card = screen.getByTestId("welcome-model");
    const link = card.querySelector<HTMLAnchorElement>('a[href="/connections"]');
    expect(link, "a /connections link inside the model card").not.toBeNull();
    expect(link!.textContent ?? "").toMatch(/other ways to connect/i);
    expect((link!.getAttribute("class") ?? "").split(/\s+/)).not.toContain("bg-accent");
    expect(solidPrimaries(welcomeCard())).toHaveLength(1);
  });

  it("'scripted demo' is said once in the card, not by the model box AND the row", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    const said = welcomeCard().textContent ?? "";
    expect(said.match(/scripted demo/gi) ?? []).toHaveLength(1);
  });

  it("control: every press keeps its full label (the sign-in disambiguator stays) and nothing is lost from the checklist", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    const card = screen.getByTestId("welcome-model");
    expect(pressesIn(card).map((b) => b.textContent?.trim())).toEqual([
      "Use Claude (your Claude Code sign-in) for answers",
      "Use ChatGPT (your Codex sign-in) for answers",
    ]);
    for (const t of ["Step first_session", "Step work_with_document", "Step teach_style", "Step set_up_voice"]) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: /dismiss/i })).toBeInTheDocument();
  });

  it("control: the press still posts exactly the provider pressed", async () => {
    W.responses["/onboarding"] = onePress();
    W.postResponses["/onboarding/use-model"] = () => ({
      promoted: { provider: "openai", model: "gpt-5.5" },
      reason: "",
    });
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    fireEvent.click(screen.getByRole("button", { name: /use chatgpt \(your codex sign-in\) for answers/i }));
    await waitFor(() => expect(useModelPosts()).toEqual([{ path: "/onboarding/use-model", body: { provider: "codex-cli" } }]));
  });
});

describe("connect_ai: no one-press choice -> the row keeps its own words and the Connections door", () => {
  it("Grok/OpenCode-style state (action 'Open the Connections page', nothing usable): detail + /connections link stay", async () => {
    const list = checklist(CONNECT_ON_CONNECTIONS);
    W.responses["/onboarding"] = onePress([], { checklist: list, next_step: list[0] });
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    const row = checklistRow("Connect your AI");
    expect(row).toHaveTextContent(/Pick which model answers you on the Connections page/);
    expect(row.querySelector('a[href="/connections"]')).not.toBeNull();
  });

  it("an older daemon / fresh install with no action keeps the /connections fallback", async () => {
    const list = checklist(step("connect_ai"));
    W.responses["/onboarding"] = {
      version: "1.313.0",
      first_run: true,
      doctor: { ok: true, checks: [] },
      checklist: list,
      next_step: list[1],
    };
    render(<OnboardingWelcome />);
    await screen.findByText("Step connect_ai");
    expect(checklistRow("Step connect_ai").querySelector('a[href="/connections"]')).not.toBeNull();
  });
});

// ======================================= 2. where the words go, said plainly

/** The <li> holding the press for `label`. */
function pressRow(label: string): HTMLElement {
  const btn = screen.getByRole("button", { name: new RegExp(`use ${label.replace(/[()]/g, "\\$&")} for answers`, "i") });
  const li = btn.closest("li");
  expect(li).not.toBeNull();
  return li as HTMLElement;
}

describe("each press says where the words go without repeating its own label", () => {
  it("Claude's line names Anthropic and does not repeat 'Claude (your Claude Code sign-in)'", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByTestId("welcome-model");
    const row = pressRow(CLAUDE.label);
    const line = (row.textContent ?? "").replace(`Use ${CLAUDE.label} for answers`, "");
    expect(line).toMatch(/Anthropic/);
    expect(line).not.toContain(CLAUDE.label);
  });

  it("ChatGPT's line names OpenAI and does not repeat 'ChatGPT (your Codex sign-in)'", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByTestId("welcome-model");
    const row = pressRow(CHATGPT.label);
    const line = (row.textContent ?? "").replace(`Use ${CHATGPT.label} for answers`, "");
    expect(line).toMatch(/OpenAI/);
    expect(line).not.toContain(CHATGPT.label);
  });

  it("control: Ollama still says the words stay on this PC, and a custom server keeps its existing sentence", async () => {
    W.responses["/onboarding"] = onePress([
      OLLAMA,
      { provider: "custom", label: "Your own model server", local: false },
    ]);
    render(<OnboardingWelcome />);
    await screen.findByTestId("welcome-model");
    expect(pressRow(OLLAMA.label)).toHaveTextContent(/stays? (here|on this PC)/i);
    expect(pressRow("Your own model server")).toHaveTextContent(
      "Your questions will be sent to your own model server to be answered.",
    );
  });
});

// ======================================= 3. phone: checklist rows wrap

describe("phone: a checklist row's action drops under its text below sm", () => {
  it("every checklist <li> wraps on narrow screens and stays one line from sm up", async () => {
    W.responses["/onboarding"] = onePress();
    render(<OnboardingWelcome />);
    await screen.findByText("Connect your AI");
    const rows = Array.from(welcomeCard().querySelectorAll("ol > li"));
    expect(rows).toHaveLength(5);
    for (const li of rows) {
      const cls = (li.getAttribute("class") ?? "").split(/\s+/);
      expect(cls).toContain("flex-wrap");
      expect(cls).toContain("sm:flex-nowrap");
    }
  });
});

// ======================================= 4. the Overview hero tells the truth

describe("Overview hero: never 'nominal' while the demo answers", () => {
  it("demo default, nothing ranked ahead -> a status line that says to choose a model; never 'nominal'", async () => {
    overviewResponses({ ...onePress(), next_step: null });
    renderOverview();
    await screen.findByText("No model chosen yet", undefined, { timeout: 4000 });
    await waitFor(() =>
      expect(screen.getAllByRole("status").some((s) => /choose a model/i.test(s.textContent ?? ""))).toBe(true),
    );
    expect(document.body.textContent ?? "").not.toMatch(/nominal/i);
  });

  it("the page has ONE <h1> (PageHeader's) — the hero status is not a heading", async () => {
    overviewResponses({ ...onePress(), next_step: null });
    renderOverview();
    await screen.findByText("No model chosen yet", undefined, { timeout: 4000 });
    const h1s = document.querySelectorAll("h1");
    expect(h1s).toHaveLength(1);
    expect(h1s[0].textContent).toMatch(/Overview/);
  });

  it("the version is shown once (the header chip), not again in the hero", async () => {
    overviewResponses({ ...onePress(), next_step: null });
    renderOverview();
    await screen.findByText("No model chosen yet", undefined, { timeout: 4000 });
    await waitFor(() => expect(document.body.textContent ?? "").toMatch(/v1\.310\.0/));
    expect((document.body.textContent ?? "").match(/v1\.310\.0/g) ?? []).toHaveLength(1);
  });

  it("hero tiles speak plainly; the raw names ride in a title", async () => {
    overviewResponses({ ...onePress(), next_step: null });
    renderOverview();
    await screen.findByText("No model chosen yet", undefined, { timeout: 4000 });
    expect(screen.getByText("Problems today")).toBeInTheDocument();
    expect(screen.getByText("Disk space free")).toBeInTheDocument();
    expect(screen.queryByText("Failures 24h")).toBeNull();
    expect(screen.queryByText("Free disk")).toBeNull();
    expect(document.querySelector('[title*="Failures 24h"]')).not.toBeNull();
    expect(document.querySelector('[title*="Free disk"]')).not.toBeNull();
  });

  it("control: a failing background loop still ranks AHEAD of the demo line", async () => {
    overviewResponses(
      { ...onePress(), next_step: null },
      { "/diagnostics": { background_loops: { fleet: { ok: false, last_error: "boom" } }, mcp_servers: [] } },
    );
    renderOverview();
    await screen.findByText("1 background task failing", undefined, { timeout: 4000 });
    expect(screen.getAllByRole("status").some((s) => /choose a model/i.test(s.textContent ?? ""))).toBe(false);
  });

  // v1.313.0 review: while /health is still on its way the page cannot know
  // whether replies are the demo, so the hero says neither — a demo install
  // used to flash "All good" before the demo line.
  it("before /health answers, the hero says neither 'All good' nor the demo line; then the demo line", async () => {
    overviewResponses(onePress());
    let answer: (v: unknown) => void = () => {};
    const healthBody = W.responses["/health"];
    W.responses["/health"] = new Promise((r) => {
      answer = r;
    });
    renderOverview();
    await screen.findByText("Checking in…", undefined, { timeout: 4000 });
    expect(screen.queryByText("All good — ready when you are")).toBeNull();
    W.responses["/health"] = healthBody;
    answer(healthBody);
    await waitFor(
      () => expect(screen.getAllByRole("status").some((s) => /choose a model/i.test(s.textContent ?? ""))).toBe(true),
      { timeout: 4000 },
    );
    expect(screen.queryByText("All good — ready when you are")).toBeNull();
  });

  it("control: a real, healthy default reads 'All good — ready when you are' and still names its model", async () => {
    overviewResponses(
      {
        ...onePress(),
        next_step: null,
        model: { default_provider: "anthropic", default_model: "claude-opus-4-8", is_mock: false, usable: [] },
      },
      {
        "/health": {
          status: "ok",
          version: "1.310.0",
          providers: [],
          default_provider: "anthropic",
          default_model: "claude-opus-4-8",
        },
      },
    );
    renderOverview();
    const id = await screen.findByText("anthropic/claude-opus-4-8", undefined, { timeout: 4000 });
    expect(id.className).toMatch(/font-mono/);
    await screen.findByText("All good — ready when you are");
    expect(document.body.textContent ?? "").not.toMatch(/choose a model/i);
  });
});

describe("HealthCard speaks plainly (raw metric names kept in titles)", () => {
  it("labels read as a daily tool, not a server console", () => {
    render(
      <HealthCard
        loading={false}
        metrics={{
          sessions_evaluated: 5,
          avg_completion: 0.8,
          avg_tool_success_rate: 0.9,
          avg_latency_s: 0.1,
          total_tool_invocations: 12,
          event_count: 55,
        } as never}
      />,
    );
    for (const label of ["Tasks reviewed", "Finished", "Tools worked", "Typical reply time"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    for (const raw of ["Sessions evaluated", "Avg completion", "Tool success", "Avg latency"]) {
      expect(screen.queryByText(raw)).toBeNull();
      expect(document.querySelector(`[title*="${raw}"]`), `title carrying ${raw}`).not.toBeNull();
    }
    // Control: every number is still on the card.
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
    expect(screen.getByText("0.1s")).toBeInTheDocument();
  });
});

// ======================================= 5. the doors: no press the route refuses

type Row = Record<string, unknown>;

const BASE_ROWS: Row[] = [
  { provider: "anthropic", available: true, class: "api", inherited_from: "claude-cli" },
  { provider: "claude-cli", available: true, class: "cli", installed: true, signed_in: true, sign_in_fix: "" },
  { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "ollama", available: false, class: "local", inherited_from: null },
];

function doorsHealth(extra: Row[]) {
  W.responses["/health"] = {
    status: "ok",
    version: "1.313.0",
    default_provider: "mock",
    default_model: "claude-opus-4-8",
    providers: [...BASE_ROWS, ...extra],
  };
}

async function renderDoors() {
  const view = render(
    <DaemonProvider>
      <ConnectDoors />
    </DaemonProvider>,
  );
  await screen.findByRole("button", { name: /^use .*claude.* for answers$/i }, { timeout: 4000 });
  return view;
}

describe("ConnectDoors: a CLI the one-press route refuses gets a link, not a broken press", () => {
  const OPENCODE: Row = { provider: "opencode-cli", available: true, class: "cli", inherited_from: null };
  const GROK: Row = { provider: "grok-cli", available: true, class: "cli", inherited_from: null };

  it("OpenCode signed in: no 'Use … for answers' press for it, and the raw id 'opencode-cli' is never printed", async () => {
    doorsHealth([OPENCODE]);
    const { container } = await renderDoors();
    expect(screen.queryByRole("button", { name: /opencode.*for answers/i })).toBeNull();
    expect(container.textContent ?? "").not.toMatch(/opencode-cli/);
  });

  it("OpenCode is named in plain words beside a way to choose it on Connections", async () => {
    doorsHealth([OPENCODE]);
    const { container } = await renderDoors();
    expect(container.textContent ?? "").toMatch(/OpenCode/);
    // The link sits in the SAME sentence/line as the plain name (its own
    // <p>/<li>, or the link text itself names OpenCode) — not just any
    // /connections link elsewhere in the doors.
    const link = Array.from(container.querySelectorAll<HTMLAnchorElement>('a[href="/connections"]')).find(
      (a) => /OpenCode/.test(a.textContent ?? "") || /OpenCode/.test(a.closest("p, li")?.textContent ?? ""),
    );
    expect(link, "a /connections link beside the word OpenCode").toBeDefined();
  });

  it("Grok signed in: no press for it either (the route answers 409 for it)", async () => {
    doorsHealth([GROK]);
    await renderDoors();
    expect(screen.queryByRole("button", { name: /grok.*for answers/i })).toBeNull();
  });

  it("every press that remains has a non-empty where-your-words-go line naming it", async () => {
    doorsHealth([OPENCODE]);
    const { container } = await renderDoors();
    const presses = screen.getAllByRole("button", { name: /^use .+ for answers$/i });
    expect(presses.length).toBeGreaterThan(0);
    // The page's text WITHOUT any button text, cut into sentences, so a
    // press label running into a neighbour's where-line cannot satisfy it.
    const clone = container.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("button").forEach((b) => b.remove());
    const sentences = Array.from(clone.querySelectorAll("p, li, span, div"))
      .filter((el) => el.children.length === 0 || el.tagName === "P" || el.tagName === "LI")
      .flatMap((el) => (el.textContent ?? "").split(/(?<=[.!?])\s+/));
    for (const b of presses) {
      const label = /^use (.+) for answers$/i.exec(b.textContent?.trim() ?? "")?.[1] ?? "";
      expect(label).not.toBe("");
      const WHERE_VERB = /\b(goes to|go to|stays?|go only)\b/i;
      // Either a sentence that names the press (a shared "With X, what you
      // type goes to Y." line), or a line in the press's OWN row.
      const own = b.parentElement?.cloneNode(true) as HTMLElement | undefined;
      own?.querySelectorAll("button").forEach((x) => x.remove());
      const said =
        sentences.some((s) => s.includes(label) && WHERE_VERB.test(s)) ||
        WHERE_VERB.test(own?.textContent ?? "");
      expect(said, `a where-your-words-go sentence naming ${label}`).toBe(true);
    }
  });

  it("the Claude press uses the Overview's words, so Chat and the Overview name it the same", async () => {
    doorsHealth([]);
    await renderDoors();
    expect(
      screen.getByRole("button", { name: "Use Claude (your Claude Code sign-in) for answers" }),
    ).toBeInTheDocument();
  });

  // v1.313.0 review: "too" only when a one-press offer sits above the line.
  it("OpenCode as the ONLY connected model reads 'is connected —' with no 'too'", async () => {
    W.responses["/health"] = {
      status: "ok",
      version: "1.313.0",
      default_provider: "mock",
      default_model: "claude-opus-4-8",
      providers: [
        { provider: "claude-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
        { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
        { provider: "ollama", available: false, class: "local", inherited_from: null },
        OPENCODE,
      ],
    };
    const { container } = render(
      <DaemonProvider>
        <ConnectDoors />
      </DaemonProvider>,
    );
    await waitFor(() => expect(container.textContent ?? "").toMatch(/OpenCode is connected —/), { timeout: 4000 });
    expect(screen.queryByRole("button", { name: /^use .+ for answers$/i })).toBeNull();
    expect(container.textContent ?? "").not.toMatch(/connected too/);
  });

  it("Claude ready plus OpenCode reads 'OpenCode is connected too —'", async () => {
    doorsHealth([OPENCODE]);
    const { container } = await renderDoors();
    expect(container.textContent ?? "").toMatch(/OpenCode is connected too —/);
  });

  it("control: the Claude press still posts claude-cli (the inherited API row collapses into it)", async () => {
    doorsHealth([OPENCODE]);
    W.postResponses["/onboarding/use-model"] = () => ({ promoted: { provider: "anthropic", model: "claude-opus-4-8" }, reason: "" });
    await renderDoors();
    fireEvent.click(screen.getByRole("button", { name: /^use .*claude.* for answers$/i }));
    await waitFor(() =>
      expect(useModelPosts()).toEqual([{ path: "/onboarding/use-model", body: { provider: "claude-cli" } }]),
    );
  });
});

// ======================================= 6. the doors' count is a caption

describe("Overview doors: the module count is a caption, not an unread badge", () => {
  it("each door says '<n> modules' and carries no bare-number bubble", () => {
    render(<AppGrid />);
    const doors = Array.from(document.querySelectorAll<HTMLElement>('[data-testid^="group-"]')).filter(
      (d) => d.tagName === "BUTTON" && d.getAttribute("data-testid") !== "group-back",
    );
    expect(doors.length).toBeGreaterThanOrEqual(3);
    for (const door of doors) {
      // The caption is its own element ("10 modules"); textContent of the whole
      // door runs words together ("Office10 modules"), so find the element.
      const caption = Array.from(door.querySelectorAll("*")).filter(
        (el) => el.children.length === 0 && /^\d+ modules?$/.test((el.textContent ?? "").trim()),
      );
      expect(caption, `no '<n> modules' caption in ${door.getAttribute("data-testid")}`).toHaveLength(1);
      const bare = Array.from(door.querySelectorAll("*")).filter(
        (el) => el.children.length === 0 && /^\d+$/.test((el.textContent ?? "").trim()),
      );
      expect(bare, `bare number bubble in ${door.getAttribute("data-testid")}`).toEqual([]);
    }
  });

  it("control: a door still opens its group", () => {
    render(<AppGrid />);
    fireEvent.click(screen.getByTestId("group-office"));
    expect(screen.getByTestId("group-back")).toBeInTheDocument();
  });
});
