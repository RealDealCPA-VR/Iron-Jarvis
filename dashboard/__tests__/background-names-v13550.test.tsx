/**
 * v1.355.0 (product-readiness G32): background problems are NAMED, say what
 * still works, and link to the fix.
 *
 * Before: a failing loop read "Background task slack_socket has been failing
 * for 12 min" in the bell (whose row opened the chat home), Everything ›
 * Status printed "slack_socket — SlackApiError: …" with no button, the home
 * line never offered to ask Jarvis, a failed pack never said what was not
 * affected and the background-refusal banner never named its job.
 *
 * ACCEPTANCE: a revoked Slack token appears as "Slack messages aren't
 * arriving · Open" leading to the channels page, and the home line offers
 * "Ask Jarvis", which opens a chat with a question that arms the health
 * readers (the arming itself is pinned in tests/test_background_names_v13550.py;
 * the chat page landing in chat-home-v13550.test.tsx).
 *
 * Everything is driven through the REAL components and the real
 * lib/loopWords table; only the network (lib/api), the event socket and the
 * desktop notifier are doubles.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { IJEvent } from "@/lib/types";

const H = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
  events: [] as IJEvent[],
  notify: vi.fn(),
  get: vi.fn(),
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  H.get.mockImplementation(async (path: string) => {
    if (path in H.responses) return H.responses[path];
    if (path.startsWith("/diagnostics")) return H.responses["/diagnostics"] ?? {};
    if (path.startsWith("/sessions")) return { sessions: [] };
    if (path.startsWith("/onboarding"))
      return { complete: true, done: true, dismissed: true, checklist: [], checks: [], next_step: null };
    if (path.startsWith("/workflows/runs")) return { runs: [] };
    if (path.startsWith("/chat/approvals")) return { approvals: [] };
    if (path.startsWith("/computeruse")) return { pending_approvals: 0 };
    return {};
  });
  return {
    ApiError: MockApiError,
    get: H.get,
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    wsUrl: (p: string) => p,
    onUnauthorizedChange: () => () => {},
    onRequestErrorChange: () => () => {},
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events, connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: H.notify,
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/settings",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import {
  CHAT_STILL_WORKS,
  HEALTH_QUESTION,
  LOOP_HREF,
  LOOP_LABELS,
  STATUS_HREF,
  loopSentence,
  loopWords,
} from "@/lib/loopWords";
import { chatAskHref, chatAskHrefFrom } from "@/lib/chatAsk";
import { chatAskHref as firstRunChatAskHref } from "@/components/onboarding/FirstRunStrip";
import { NotificationBell, PACK_STILL_WORKS, packFailedBody, toActivity } from "@/components/NotificationBell";
import OverviewPage from "@/components/overview/StatusOverview";
import { HomeLine } from "@/components/chat/HomeLine";
import { ProviderDowngradeBanner, REFUSED_STILL, downgradeNotice } from "@/components/ProviderDowngradeBanner";
import { EVERYTHING_TAB_EVENT } from "@/lib/surfaces";

const SLACK_ERR = "SlackApiError: invalid_auth";
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
const DASHBOARD = join(__dirname, "..");

beforeEach(() => {
  H.responses = {};
  H.events = [];
  H.notify.mockClear();
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => cleanup());

/* ------------------------------------------------------------- the table */

describe("lib/loopWords: each background task in the user's words", () => {
  it("names the tasks the audit found by what they do for the user", () => {
    expect(LOOP_LABELS.slack_socket).toBe("Slack messages");
    expect(LOOP_LABELS.iron_proxy).toBe("Account switcher");
    expect(LOOP_LABELS.scheduler).toBe("Schedules");
    expect(LOOP_LABELS.inbound).toBe("Phone messages");
    expect(LOOP_LABELS.fleet).toBe("Model servers");
    expect(LOOP_LABELS.dispatcher).toBe("Queued tasks");
    expect(loopWords("slack_socket").problem).toBe("Slack messages aren't arriving");
  });

  it("opens the page each fix lives on", () => {
    expect(LOOP_HREF.scheduler).toBe("/schedules");
    for (const k of ["slack_socket", "inbound", "calendar"]) expect(LOOP_HREF[k]).toBe("/channels");
    expect(LOOP_HREF.fleet).toBe("/fleet");
    expect(LOOP_HREF.iron_proxy).toBe("/connections");
    expect(LOOP_HREF.dispatcher).toBe("/agents?view=team");
    expect(LOOP_HREF.reconcile_media_jobs).toBe("/creative");
    expect(STATUS_HREF).toBe("/everything#status");
  });

  it("an unknown name reads as a background task, opens Status, and is never echoed", () => {
    for (const odd of ["brand_new_loop", "", "   ", undefined, null, 42, "__proto__", "constructor", "toString"]) {
      const w = loopWords(odd);
      expect(w).toEqual({
        label: "A background task",
        problem: "A background task isn't working",
        href: STATUS_HREF,
        still: CHAT_STILL_WORKS,
      });
    }
    expect(JSON.stringify(loopWords("brand_new_loop"))).not.toContain("brand_new_loop");
  });

  it("every door is a page the dashboard serves (a link to nowhere is no fix)", () => {
    const hrefs = new Set([...Object.values(LOOP_HREF), STATUS_HREF]);
    for (const href of hrefs) {
      const route = href.split(/[?#]/)[0];
      const file = route === "/" ? join(DASHBOARD, "app", "page.tsx") : join(DASHBOARD, "app", route, "page.tsx");
      expect(existsSync(file), `${href} → ${file}`).toBe(true);
    }
  });

  it("the words are plain: no engine names, no dashes, a sentence for what still works", () => {
    for (const key of Object.keys(LOOP_LABELS)) {
      const w = loopWords(key);
      for (const s of [w.label, w.problem, w.still]) {
        expect(s).not.toMatch(/_|daemon|loop|[—–]/i);
      }
      expect(w.problem).not.toMatch(/[.!?]$/);
      expect(w.still === "" || /\.$/.test(w.still)).toBe(true);
    }
    // A model-server or account problem can stop chat itself: no promise.
    expect(loopWords("fleet").still).toBe("");
    expect(loopWords("iron_proxy").still).toBe("");
    expect(loopSentence("fleet")).toBe("Your model servers can't be checked.");
    expect(loopSentence("slack_socket")).toBe(
      "Slack messages aren't arriving. Chat and your other channels keep working.",
    );
  });
});

/* ------------------------------------------------------------- the bell */

describe("the bell names a failing task and opens its fix", () => {
  async function openBell() {
    render(<NotificationBell />);
    await waitFor(() => expect(H.get).toHaveBeenCalledWith("/diagnostics"));
    const btn = await screen.findByLabelText(/^\d+ notifications$/);
    fireEvent.click(btn);
  }

  it("a revoked Slack token: 'Slack messages aren't arriving', what still works, and /channels", async () => {
    H.responses["/diagnostics"] = {
      pending_reviews: 0,
      background_loops: { slack_socket: { ok: false, last_error: SLACK_ERR, at: minutesAgo(12) } },
    };
    await openBell();
    const [row] = await screen.findAllByTestId("bell-loop-failing");
    expect(row.textContent).toContain("Slack messages aren't arriving");
    expect(row.textContent).toContain("For 12 min · Chat and your other channels keep working.");
    const link = row.querySelector("a")!;
    expect(link.getAttribute("href")).toBe("/channels");
    // The engine name and the raw error only on the hover.
    expect(link.getAttribute("title")).toBe(`slack_socket: ${SLACK_ERR}`);
    expect(row.textContent).not.toContain("slack_socket");
    expect(row.textContent).not.toContain(SLACK_ERR);
    // The desktop ping says it the same way.
    await waitFor(() => expect(H.notify).toHaveBeenCalled());
    expect(String(H.notify.mock.calls[0][1])).toContain("Slack messages aren't arriving");
  });

  it("two tasks: each row its own door; the ping counts them", async () => {
    H.responses["/diagnostics"] = {
      pending_reviews: 0,
      background_loops: {
        fleet: { ok: false, last_error: "RuntimeError: x", at: minutesAgo(9) },
        scheduler: { ok: false, last_error: "RuntimeError: y", at: minutesAgo(30) },
      },
    };
    await openBell();
    const rows = await screen.findAllByTestId("bell-loop-failing");
    const byLoop = Object.fromEntries(rows.map((r) => [r.getAttribute("data-loop"), r]));
    expect(byLoop.fleet.querySelector("a")!.getAttribute("href")).toBe("/fleet");
    expect(byLoop.fleet.textContent).toContain("Your model servers can't be checked");
    // No promise about chat for a model-server problem.
    expect(byLoop.fleet.textContent).not.toContain("keep");
    expect(byLoop.scheduler.querySelector("a")!.getAttribute("href")).toBe("/schedules");
    expect(byLoop.scheduler.textContent).toContain("Schedules aren't starting");
    await waitFor(() => expect(H.notify).toHaveBeenCalled());
    expect(String(H.notify.mock.calls[0][1])).toContain("2 background tasks failing");
  });

  it("an unknown task opens Everything › Status and switches its tab", async () => {
    H.responses["/diagnostics"] = {
      pending_reviews: 0,
      background_loops: { brand_new_loop: { ok: false, last_error: "boom", at: minutesAgo(6) } },
    };
    const seen: unknown[] = [];
    const onTab = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(EVERYTHING_TAB_EVENT, onTab);
    try {
      await openBell();
      const [row] = await screen.findAllByTestId("bell-loop-failing");
      expect(row.textContent).toContain("A background task isn't working");
      expect(row.textContent).not.toContain("brand_new_loop");
      const link = row.querySelector("a")!;
      expect(link.getAttribute("href")).toBe(STATUS_HREF);
      fireEvent.click(link);
      expect(seen).toEqual(["status"]);
    } finally {
      window.removeEventListener(EVERYTHING_TAB_EVENT, onTab);
    }
  });
});

describe("a failed pack says what keeps working", () => {
  it("after the pack's own fix, with its full stop", () => {
    expect(packFailedBody("Add BRAVE_API_KEY in Settings > Tools, then Retry")).toBe(
      `Add BRAVE_API_KEY in Settings > Tools, then Retry. ${PACK_STILL_WORKS}`,
    );
    expect(packFailedBody("Press Retry.")).toBe(`Press Retry. ${PACK_STILL_WORKS}`);
    expect(packFailedBody("")).toBe(PACK_STILL_WORKS);
    expect(packFailedBody(undefined)).toBe(PACK_STILL_WORKS);
    expect(PACK_STILL_WORKS).toBe("Chat and your other tools keep working.");
  });

  it("on the bell's row", () => {
    const item = toActivity({
      id: "e1",
      type: "mcp.pack_failed",
      session_id: null,
      ts: "2026-10-19T09:00:00",
      payload: { name: "brave", reason: "It needs a key", fix: "Add it in Settings > Tools" },
    } as unknown as IJEvent)!;
    expect(item.body).toBe(`Add it in Settings > Tools. ${PACK_STILL_WORKS}`);
    expect(item.href).toBe("/tools");
  });
});

/* -------------------------------------------------------- Everything › Status */

describe("Everything › Status: one line per task, in words, with its door", () => {
  it("'Slack messages aren't arriving. … Open' → /channels; the error on the hover", async () => {
    H.responses["/ui/system-map"] = {
      areas: [],
      status: { failing_loops: [{ name: "slack_socket", error: SLACK_ERR }] },
    };
    render(<OverviewPage />);
    const note = await screen.findByTestId("loop-failing-note");
    expect(note.textContent).toContain(
      "Slack messages aren't arriving. Chat and your other channels keep working.",
    );
    expect(note.textContent).not.toContain("slack_socket");
    expect(note.textContent).not.toContain(SLACK_ERR);
    const open = within(note).getByRole("link", { name: "Open: Slack messages" });
    expect(open.getAttribute("href")).toBe("/channels");
    expect(open.textContent).toBe("Open");
    const [row] = within(note).getAllByTestId("loop-failing-row");
    expect(row.querySelector("[title]")!.getAttribute("title")).toBe(`slack_socket: ${SLACK_ERR}`);
    // The status line keeps its words.
    expect(screen.getByText("1 background task failing")).toBeTruthy();
    // Look and feel: the same amber note; a row adds no box of its own.
    const cls = (note.getAttribute("class") ?? "").split(/\s+/);
    expect(cls).toEqual(expect.arrayContaining(["rounded-xl", "border-amber-500/25", "bg-amber-500/[0.06]"]));
    const rowCls = (row.getAttribute("class") ?? "").split(/\s+/);
    expect(rowCls.filter((c) => /^(border|bg-|rounded)/.test(c))).toEqual([]);
  });

  it("a task whose door IS Status gets no Open (it would go nowhere)", async () => {
    H.responses["/ui/system-map"] = {
      areas: [],
      status: {
        failing_loops: [
          { name: "auto_backup", error: "OSError: disk full" },
          { name: "brand_new_loop", error: "boom" },
        ],
      },
    };
    render(<OverviewPage />);
    const note = await screen.findByTestId("loop-failing-note");
    expect(within(note).getAllByTestId("loop-failing-row")).toHaveLength(2);
    expect(note.textContent).toContain("Backups aren't being made. Chat keeps working.");
    expect(note.textContent).toContain("A background task isn't working. Chat keeps working.");
    expect(within(note).queryByRole("link")).toBeNull();
  });
});

/* ------------------------------------------------------------ the home line */

describe("the home line offers 'Ask Jarvis' for a failing task", () => {
  const failing = { interrupted: 0, failing_loops: 1, failing_packs: 0, running: 0, short_schedules: 0 };
  const ASKED = encodeURIComponent(HEALTH_QUESTION);

  it("on /chat it opens a fresh chat at '/' with the health question waiting", async () => {
    window.history.replaceState({}, "", "/chat?thread=t9");
    H.responses["/ui/status-line"] = failing;
    render(<HomeLine />);
    const line = await screen.findByTestId("home-line");
    expect(line.getAttribute("data-kind")).toBe("failing");
    const ask = within(line).getByRole("link", { name: "Ask Jarvis" });
    await waitFor(() => expect(ask.getAttribute("href")).toBe(`/?ask=${ASKED}`));
    // Open still goes to Status, where the task is named.
    expect(within(line).getByRole("link", { name: "Open" }).getAttribute("href")).toBe(STATUS_HREF);
  });

  it("on '/' it opens /chat with the question (the other address of the same chat)", async () => {
    window.history.replaceState({}, "", "/");
    H.responses["/ui/status-line"] = failing;
    render(<HomeLine />);
    const ask = await screen.findByRole("link", { name: "Ask Jarvis" });
    expect(ask.getAttribute("href")).toBe(`/chat?ask=${ASKED}`);
  });

  it("only for a failing task at the top: an interrupted job leads without it", async () => {
    H.responses["/ui/status-line"] = { ...failing, interrupted: 1 };
    render(<HomeLine />);
    const line = await screen.findByTestId("home-line");
    expect(line.getAttribute("data-kind")).toBe("interrupted");
    expect(within(line).queryByRole("link", { name: "Ask Jarvis" })).toBeNull();
  });

  it("the question URL has ONE definition shape: lib/chatAsk == FirstRunStrip's", () => {
    for (const q of [HEALTH_QUESTION, "a & b?", "café / naïve #1"]) {
      expect(chatAskHref(q)).toBe(firstRunChatAskHref(q));
    }
    expect(chatAskHrefFrom("/chat", "q?")).toBe("/?ask=q%3F");
    expect(chatAskHrefFrom("/chat/", "q")).toBe("/?ask=q");
    expect(chatAskHrefFrom("/", "q")).toBe("/chat?ask=q");
    expect(chatAskHrefFrom("/everything", "q")).toBe("/chat?ask=q");
    expect(chatAskHrefFrom("", "q")).toBe("/chat?ask=q");
  });

  it("the question is the one the Python pin checks arms the health readers", () => {
    const src = readFileSync(join(DASHBOARD, "lib", "loopWords.ts"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toContain(`export const HEALTH_QUESTION = "${HEALTH_QUESTION}";`);
  });
});

/* -------------------------------------------------------- the refusal banner */

describe("the background-refusal banner names its job and what was not affected", () => {
  const ev = (id: string, payload: Record<string, unknown>, session_id: string | null): IJEvent =>
    ({ id, type: "provider.downgraded", session_id, ts: `2026-10-19T00:00:0${id}Z`, payload }) as IJEvent;
  const REFUSED = ev("2", { requested: "fleet-x", used: "none", reason: "not connected", label: "Office Spark" }, "s-42");

  it("a refused run: 'Nothing was changed in that step.', See the job → its page, Connections kept", () => {
    const n = downgradeNotice(REFUSED)!;
    expect(n.detail).toBe("Office Spark: not connected.");
    expect(n.still).toBe(REFUSED_STILL);
    expect(n.sessionId).toBe("s-42");
    H.events = [REFUSED];
    render(<ProviderDowngradeBanner />);
    const line = screen.getByTestId("provider-downgrade");
    expect(line).toHaveTextContent(
      "Nothing answered a background task. Office Spark: not connected. Nothing was changed in that step.",
    );
    expect(screen.getByRole("link", { name: "See the task" })).toHaveAttribute("href", "/sessions/s-42");
    expect(screen.getByRole("link", { name: "Open Connections" })).toHaveAttribute("href", "/connections");
  });

  it("a mock answer never claims nothing changed (the mock can write files)", () => {
    const mock = ev("1", { requested: "mock", used: "mock", reason: "a run used the demo" }, "s-7");
    const n = downgradeNotice(mock)!;
    expect(n.still).toBe("");
    H.events = [mock];
    render(<ProviderDowngradeBanner />);
    expect(screen.getByTestId("provider-downgrade").textContent).not.toContain("Nothing was changed");
    expect(screen.queryByTestId("provider-downgrade-still")).toBeNull();
    expect(screen.getByRole("link", { name: "See the task" })).toHaveAttribute("href", "/sessions/s-7");
  });

  it("no job to see without a run: a chat refusal stays silent, a sessionless mock has no link", () => {
    expect(downgradeNotice(ev("3", { used: "none", reason: "x" }, "chat"))).toBeNull();
    H.events = [ev("4", { used: "mock", reason: "demo" }, null)];
    render(<ProviderDowngradeBanner />);
    expect(screen.getByTestId("provider-downgrade")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "See the task" })).toBeNull();
  });
});
