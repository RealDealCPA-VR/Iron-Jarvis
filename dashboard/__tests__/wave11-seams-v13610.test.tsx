/**
 * v1.361.0 integrate — Wave 11 ("the final hunt's regressions closed"), the
 * dashboard half of the seams.
 *
 *  (a) ONE NOTICE PER OUTCOME, ACROSS THE BELL AND "THIS PC". Lane 11A made
 *      ONE window speak — the main window while it is open, else the pop-out
 *      the desktop app names — and the integrator made the "This PC" bridge
 *      follow the same rule (it was always quiet in a pop-out, so with the
 *      main window in the tray a clean schedule's "done" reached no toast).
 *      Four REAL scheduled fires (fixtures/wave11-seams-v13610.json, written
 *      by tests/test_integrate_wave11_v13610.py from the real daemon) go
 *      through the real NotificationBell AND the real DesktopNotifyBridge
 *      together, one event at a time as the socket delivers them: exactly
 *      the outcome's one notice in the main window and in a pop-out that
 *      speaks, none in a quiet pop-out — and the one notice names the
 *      workflow and its reason.
 *  (b) the bridge's speaker switch never repeats a line the main window said.
 *  (c) THE PLACE WORDS ARE ONE: for each pick, the reply's receipt, the run
 *      page's model field, "Ran on" (mission screen) and the model menu name
 *      the same machine, once — and (integrate, lane 11C's note) an Ollama
 *      run is never placed "on llama3" because a detected row names its
 *      model.
 *
 * Real components and helpers; only the network, the event socket, the
 * polled hooks and the bell's desktop-notification hook are stubbed.
 */
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

const { getMock, notifyMock, eventsRef, H } = vi.hoisted(() => ({
  getMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
  H: { responses: {} as Record<string, unknown> },
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
  };
});
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: eventsRef.current, connected: true }),
}));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: notifyMock,
  }),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: (p: string | null) => ({ data: p ? (H.responses[p] ?? null) : null, error: null, loading: false, reload: () => {} }),
  usePolledApi: (p: string | null) => ({ data: p ? (H.responses[p] ?? null) : null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/chat",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { NotificationBell } from "@/components/NotificationBell";
import { DesktopNotifyBridge } from "@/components/DesktopNotifyBridge";
import { TurnReceipt, type TurnRoute } from "@/components/chat/TurnReceipt";
import { runModelWords, runWhere } from "@/components/sessions/runPageWords";
import { answeredModelName } from "@/lib/answeredModel";
import { auditEntriesToEvents } from "@/lib/bellAway";
import { __resetApiCache } from "@/lib/apiCache";
import { modelPickWords, modelRowName } from "@/lib/onboarding";
import { ranOnWords } from "@/lib/ranOnWords";
import type { IJEvent } from "@/lib/types";

/* ---- the REAL daemon's four scheduled fires ---------------------------- */

interface Fire {
  row: { last_status: string; last_detail: string };
  phone: string[];
  desktop: { title: string; message: string }[];
  audit: unknown[];
}
function readFixture(): Record<string, Fire> {
  const file = path.join(__dirname, "fixtures", "wave11-seams-v13610.json");
  return JSON.parse(readFileSync(file, "utf-8").replace(/\r\n/g, "\n")) as Record<string, Fire>;
}
const FIX = readFixture();
const TYPES = ["session.completed", "workflow.completed"];
const INBOX = "The inbox could not be reached.";
const STEP_FAILED = "A step of your workflow Nightly could not finish";

/** One fire's events in the order the socket delivers them: the runs and
 *  the workflow as they ended (the audit, oldest first), then the
 *  schedule's own "This PC" line, which the daemon sends after the run. */
function delivered(key: string): IJEvent[] {
  const runs = auditEntriesToEvents(FIX[key].audit, TYPES).reverse();
  const lines: IJEvent[] = FIX[key].desktop.map((d, i) => ({
    id: `cd_${key}_${i}`,
    type: "comm.desktop",
    ts: "2026-10-20T03:00:59Z",
    session_id: null,
    payload: { title: d.title, message: d.message },
  }));
  return [...runs, ...lines];
}

const bridgeNotify = vi.fn(async () => true);

/** The window this test runs in: the main window (no pop-out bridge), or a
 *  pop-out that speaks / stays quiet as the desktop app says. */
function windowIs(popout: null | (() => boolean)) {
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
    isDesktop: true,
    notify: bridgeNotify,
    ...(popout ? { popout: { open: () => {}, isPopout: true, path: "/workflows", speaks: popout } } : {}),
  };
}

const tree = () => (
  <>
    <NotificationBell />
    <DesktopNotifyBridge />
  </>
);

async function mount() {
  const view = render(tree());
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
  return view;
}

/** Each event as its own socket frame (one render each). */
async function deliver(view: ReturnType<typeof render>, events: IJEvent[]) {
  for (const ev of events) {
    eventsRef.current = [ev, ...eventsRef.current];
    await act(async () => {
      view.rerender(tree());
    });
  }
}

/** Every desktop notice this window raised: the bell's and This PC's. */
const notices = () => [
  ...notifyMock.mock.calls.map((c) => ["bell", String(c[0]), String(c[1])]),
  ...bridgeNotify.mock.calls.map((c) => ["this-pc", String((c as unknown[])[0]), String((c as unknown[])[1])]),
];

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  getMock.mockImplementation(async (p: unknown) =>
    String(p).startsWith("/audit") ? { entries: [], next_cursor: null, total: 0 } : {},
  );
  notifyMock.mockReset();
  bridgeNotify.mockClear();
  eventsRef.current = [];
  H.responses = {};
  window.localStorage.clear();
  document.title = "Iron Jarvis";
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

/* ====================================== (a) one notice per outcome */

/** The notices each fire's outcomes earn while a window that speaks is open. */
const EXPECTED: Record<string, string[][]> = {
  // The bell rings the failed step's own run (its reason is the run's
  // summary); This PC is quiet for it.
  skipped_agent_step: [["bell", STEP_FAILED, INBOX]],
  // A tool step has no run, so no bell row: This PC says the schedule's line.
  skipped_tool_step: [["this-pc", "Iron Jarvis", FIX.skipped_tool_step.phone[0]]],
  // The step that ENDED the run rang; the workflow's row stands in for it.
  halted_by_agent_step: [["bell", STEP_FAILED, INBOX]],
  // Two outcomes: the attempt that failed (the bell) and the run that
  // finished (This PC's "done").
  clean_after_a_retried_step: [
    ["bell", STEP_FAILED, INBOX],
    ["this-pc", "Iron Jarvis", "Schedule “nightly”: done — workflow “Nightly” ran"],
  ],
};

const MODES: { name: string; popout: null | (() => boolean); speaks: boolean }[] = [
  { name: "the main window", popout: null, speaks: true },
  { name: "a pop-out that speaks (the main window is in the tray)", popout: () => true, speaks: true },
  { name: "a quiet pop-out (the main window is open and says it)", popout: () => false, speaks: false },
];

describe("(a) one notice per outcome across the bell and This PC, in every window", () => {
  for (const mode of MODES) {
    for (const key of Object.keys(EXPECTED)) {
      it(`${key} — ${mode.name}`, async () => {
        windowIs(mode.popout);
        const view = await mount();
        await deliver(view, delivered(key));
        expect(notices()).toEqual(mode.speaks ? EXPECTED[key] : []);
      });
    }
  }

  it("the one notice names the thing: the workflow and its reason, or the schedule's step", () => {
    // The bell's words come from the run (title names the workflow, body is
    // the run's own reason); This PC's from the schedule (names the step).
    expect(FIX.skipped_agent_step.phone[0]).toContain("“Fetch”");
    expect(FIX.skipped_tool_step.desktop.map((d) => d.message)).toEqual(FIX.skipped_tool_step.phone);
    expect(FIX.skipped_tool_step.phone[0]).toContain("“Check”");
    expect(FIX.halted_by_agent_step.phone[0]).toMatch(/stopped at its step “Fetch”: The inbox could not be reached\./);
  });
});

/* ====================================== (b) the bridge's speaker switch */

describe("(b) a pop-out that becomes the one that speaks never repeats a line", () => {
  it("a This PC line that arrived while the main window spoke is not said again after it closes", async () => {
    let speaks = false;
    windowIs(() => speaks);
    const view = await mount();
    const line = (id: string, message: string): IJEvent => ({
      id,
      type: "comm.desktop",
      ts: "2026-10-20T03:00:00Z",
      session_id: null,
      payload: { title: "Iron Jarvis", message },
    });
    await deliver(view, [line("cd_a", "Schedule “a”: done")]);
    expect(bridgeNotify).not.toHaveBeenCalled(); // the main window said it
    speaks = true; // the main window closes to the tray
    await deliver(view, [line("cd_b", "Schedule “b”: done")]);
    // MUTATION: a quiet window that skips recording (v1.360.1's `break`)
    // says "a" again here.
    expect(bridgeNotify.mock.calls).toEqual([["Iron Jarvis", "Schedule “b”: done"]]);
  });

  it("the bell and the bridge switch together on a real fire: only what came after is said", async () => {
    let speaks = false;
    windowIs(() => speaks);
    const view = await mount();
    const events = delivered("clean_after_a_retried_step");
    // The failed attempt arrives while the main window is open (it says it).
    await deliver(view, events.slice(0, 1));
    speaks = true;
    await deliver(view, events.slice(1));
    expect(notices()).toEqual([["this-pc", "Iron Jarvis", "Schedule “nightly”: done — workflow “Nightly” ran"]]);
  });
});

/* ====================================== (c) the place words are one */

/** The live /models shape: config slots carry no name; fleet nodes carry the
 *  label the user gave the machine; a detected Ollama row's `name` is its
 *  own model id. */
const CATALOG = [
  { provider: "custom", model: "fleet" },
  { provider: "fleet-custom", model: "fleet", name: "DGX Spark" },
  { provider: "fleet-spark", model: "fleet", name: "Spark B" },
  { provider: "ollama", model: "fleet" },
  { provider: "ollama", model: "llama3", name: "llama3" },
  { provider: "ollama", model: "qwen3", name: "qwen3" },
  { provider: "opencode-cli", model: "spark/fleet" },
];

function receiptText(route: TurnRoute): string {
  const view = render(<TurnReceipt inline route={route} modelName={answeredModelName(route, CATALOG)} />);
  const text = (view.container.textContent ?? "").replace(/\s+/g, " ").trim();
  cleanup();
  return text;
}
const count = (hay: string, needle: string) => hay.toLowerCase().split(needle.toLowerCase()).length - 1;

describe("(c) one pick, one place name: the reply, the run page, 'Ran on' and the menu", () => {
  const PICKS: { what: string; route: TurnRoute; place: string; never: string[] }[] = [
    {
      what: "the user's own server, named DGX Spark",
      route: { provider: "custom", model: "fleet", label: "DGX Spark", reason: "default", on_this_network: true },
      place: "DGX Spark",
      never: ["fleet", "Custom endpoint"],
    },
    {
      what: "a substitute named Spark B (asked for Claude Code)",
      route: {
        provider: "fleet-spark",
        model: "fleet",
        label: "Spark B",
        reason: "explicit",
        requested: "claude-cli",
        on_this_network: true,
      },
      place: "Spark B",
      never: ["fleet"],
    },
    {
      what: "an Ollama 'fleet' pick beside detected models",
      route: { provider: "ollama", model: "fleet", reason: "default", on_this_network: true },
      place: "your model server",
      never: ["llama3", "qwen3"],
    },
  ];

  for (const pick of PICKS) {
    it(pick.what, () => {
      const { provider, model } = pick.route as { provider: string; model: string };
      const surfaces: Record<string, string> = {
        reply: receiptText(pick.route),
        "run page": runModelWords(provider, model, CATALOG),
        "Ran on": ranOnWords(provider, model, CATALOG).text,
        "menu row": modelRowName({ provider, model }, CATALOG),
      };
      for (const [surface, text] of Object.entries(surfaces)) {
        expect(count(text, pick.place), `${surface}: ${text}`).toBe(1);
        for (const wrong of pick.never) expect(count(text, wrong), `${surface}: ${text}`).toBe(0);
      }
      // And the run page's local / cloud word is the reply's.
      render(<TurnReceipt inline route={pick.route} modelName={answeredModelName(pick.route, CATALOG)} />);
      const replyTail = screen.getByTestId("turn-where").textContent;
      const page = runWhere(provider, model, CATALOG, { provider, on_this_network: pick.route.on_this_network });
      expect(page?.tail ?? null, pick.what).toBe(replyTail);
    });
  }

  it("OpenCode on '<server>/fleet': the run page is the model list's words, 'Ran on' names the program", () => {
    expect(runModelWords("opencode-cli", "spark/fleet", CATALOG)).toBe(
      modelPickWords({ provider: "opencode-cli", model: "spark/fleet" }, CATALOG),
    );
    expect(ranOnWords("opencode-cli", "spark/fleet", CATALOG).text).toBe("your model server on OpenCode");
  });

  it("(integrate) an Ollama run is never placed on another model's row: 'Ran on' says Ollama, not llama3", () => {
    const ran = ranOnWords("ollama", "qwen3", CATALOG).text;
    expect(ran).toMatch(/^qwen3 on /);
    expect(count(ran, "llama3"), ran).toBe(0);
    expect(count(runModelWords("ollama", "qwen3", CATALOG), "llama3")).toBe(0);
    expect(count(receiptText({ provider: "ollama", model: "qwen3", reason: "default", on_this_network: true }), "llama3")).toBe(0);
    // OTHER DIRECTION: a name the user gave the endpoint still places a run.
    expect(ranOnWords("fleet-spark", "glm", [{ provider: "fleet-spark", model: "fleet", name: "Spark B" }]).text).toBe(
      "glm on Spark B",
    );
    expect(ranOnWords("custom", "glm", CATALOG).text).toBe("glm on DGX Spark");
  });
});
