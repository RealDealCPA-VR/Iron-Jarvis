/**
 * v1.360.0 integrate — Wave 10's seams, fed what the REAL daemon sends.
 *
 * `fixtures/wave10-seams-v13600.json` is written by
 * tests/test_integrate_wave10_v13600.py from the real app factory (a failed
 * scheduled workflow, a failed scheduled task, a parked scheduled workflow,
 * and a reply from the user's own model server). Here those answers go
 * through the real components:
 *
 *  (a) ONE NOTICE PER OUTCOME. While a window is open the bell is the one
 *      desktop notice: a failed scheduled workflow (its step's completion +
 *      the workflow's) is ONE row and ONE ping; a failed scheduled task is one
 *      row and one ping; a scheduled workflow parked on its question is one
 *      "N pending" ping (the daemon keeps "This PC" quiet for all three — the
 *      Python half). The tray half (main.js) reads the same audit answer.
 *  (c) ONE NAME FOR THE USER'S MODEL SERVER. The reply's route and the real
 *      GET /models rows say "DGX Spark" on the reply receipt, the helper
 *      card, the run page and the model menu — and "your model server" for a
 *      machine nobody named.
 *
 * Only the network, the event socket and the desktop notification hook are
 * stubbed; the bell, the model words and RunMeta are the real ones.
 */
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { getMock, notifyMock, eventsRef, modelsRef } = vi.hoisted(() => ({
  getMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
  modelsRef: { current: [] as unknown[] },
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

vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ models: modelsRef.current, loading: false, error: null, reload: () => {} }),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: null, refresh: () => {}, epoch: 0 }),
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
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
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
    AnimatePresence: ({ children }: { children?: ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { NotificationBell } from "@/components/NotificationBell";
import { RunMeta, type RunResult } from "@/components/chat/RunResultCard";
import { runModelName } from "@/components/chat/AgentRunReceipt";
import { modelText } from "@/components/ModelRowBits";
import { answeredModelName, type CatalogRow } from "@/lib/answeredModel";
import { ranOnWords } from "@/lib/ranOnWords";
import { runModelWords } from "@/components/sessions/runPageWords";
import { __resetApiCache } from "@/lib/apiCache";

/* ---- the fixture the real daemon wrote ---------------------------------- */

interface AuditEntry {
  id: string;
  ts: string;
  type: string;
  session_id: string | null;
  payload: Record<string, unknown>;
}
interface Machine {
  route: { provider: string; model?: string; label?: string };
  models: (CatalogRow & Record<string, unknown>)[];
}
interface Fixture {
  failed_workflow: { events: unknown[]; audit: AuditEntry[] };
  failed_task: { events: unknown[]; audit: AuditEntry[] };
  parked_workflow: { runs: Record<string, unknown>[] };
  machine: { named: Machine; unnamed: Machine };
}

const FIX = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "wave10-seams-v13600.json"), "utf-8").replace(/\r\n/g, "\n"),
) as Fixture;

let waitingRuns: Record<string, unknown>[] = [];

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  notifyMock.mockReset();
  eventsRef.current = [];
  modelsRef.current = [];
  waitingRuns = [];
  window.localStorage.clear();
  document.title = "Iron Jarvis";
  getMock.mockImplementation(async (p: unknown) => {
    const url = String(p);
    if (url.startsWith("/audit")) return { entries: [], next_cursor: null, total: 0 };
    if (url.startsWith("/workflows/runs")) return { runs: waitingRuns };
    if (url === "/chat/approvals/pending") return { approvals: [] };
    return {};
  });
});

afterEach(() => cleanup());

/* ============================================ (a) one notice per outcome */

describe("(a) the bell, fed the daemon's real events: one row and one ping per outcome", () => {
  it("a failed scheduled WORKFLOW (its step + the workflow): ONE row, ONE ping, the workflow's words", async () => {
    // The live frames, newest first, as the audit answer holds them.
    eventsRef.current = FIX.failed_workflow.audit;
    render(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    expect(notifyMock.mock.calls[0][0]).toBe("Your workflow Weekly report could not finish");
    await screen.findByRole("button", { name: "1 notifications" });
    fireEvent.click(screen.getByRole("button", { name: /notification/i }));
    await screen.findByText("Your workflow Weekly report could not finish");
    expect(screen.queryByText(/A step of your workflow/)).toBeNull();
    expect(notifyMock).toHaveBeenCalledTimes(1);
  });

  it("a failed scheduled TASK: one row, one ping, the run's own sentence", async () => {
    eventsRef.current = FIX.failed_task.audit;
    render(<NotificationBell />);
    const summary = String(FIX.failed_task.audit[0].payload.summary ?? "");
    await waitFor(() =>
      expect(notifyMock).toHaveBeenCalledWith(
        "Your schedule morning-brief could not finish",
        summary,
        expect.any(Function),
      ),
    );
    expect(notifyMock).toHaveBeenCalledTimes(1);
    await screen.findByRole("button", { name: "1 notifications" });
  });

  it("a scheduled workflow PARKED on its question: one 'N pending' ping (This PC is quiet for it)", async () => {
    waitingRuns = FIX.parked_workflow.runs;
    render(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    const [title, body] = notifyMock.mock.calls[0];
    expect(title).toBe("Iron Jarvis: 1 pending");
    expect(body).toContain("1 workflow question waiting");
  });

  it("a popped-out window shows the count but never pings it: the main window speaks for the app", async () => {
    const w = window as unknown as { ironjarvis?: unknown };
    w.ironjarvis = { popout: { open: () => {}, isPopout: true } };
    try {
      waitingRuns = FIX.parked_workflow.runs;
      render(<NotificationBell />);
      await screen.findByRole("button", { name: "1 notifications" });
      await waitFor(() => expect(document.title).toContain("(1)"));
      expect(notifyMock).not.toHaveBeenCalled();
    } finally {
      delete w.ironjarvis;
    }
  });
});

/* ===================================== (c) one name for the model server */

function result(provider: string, model: string): RunResult {
  return {
    found: true,
    session_id: "session_x",
    status: "completed",
    task: "t",
    summary: "",
    steps: 0,
    tools_used: [],
    tools_failed: [],
    files_created: [],
    files_changed: [],
    errors: [],
    revertable: 0,
    provider,
    model,
  } as unknown as RunResult;
}

describe("(c) the daemon's route and catalog say ONE name on every surface", () => {
  it("a named machine: the reply, the helper card and row, the run page and the menu all read 'DGX Spark'", () => {
    const { route, models } = FIX.machine.named;
    modelsRef.current = models;
    // The reply receipt reads the route (with its label).
    expect(answeredModelName(route, models)).toBe("DGX Spark");
    // A helper run and the run page read the stamp (no label) + the catalog.
    expect(answeredModelName({ provider: route.provider, model: route.model }, models)).toBe("DGX Spark");
    expect(runModelName({ provider: route.provider, model: route.model }, models)).toBe("DGX Spark");
    expect(ranOnWords(route.provider, route.model, models).text).toBe("DGX Spark");
    // The run page's Model field (integrate: it said "DGX Spark · fleet").
    expect(runModelWords(route.provider, route.model, models)).toBe("DGX Spark");
    // The model menu's row for the same pick.
    const row = models.find((m) => m.provider === route.provider && m.model === route.model)!;
    expect(row, "the catalog lists the pick").toBeTruthy();
    expect(modelText(row, models)).toBe("DGX Spark");
    // The helper card's line, rendered.
    render(<RunMeta result={result(route.provider, route.model ?? "")} />);
    expect(screen.getByTestId("run-result-model").textContent).toBe("answered by DGX Spark");
  });

  it("a machine nobody named: 'your model server' everywhere, never 'fleet'", () => {
    const { route, models } = FIX.machine.unnamed;
    modelsRef.current = models;
    expect(route.label ?? "").toBe("");
    expect(answeredModelName(route, models)).toBe("your model server");
    expect(ranOnWords(route.provider, route.model, models).text).toBe("your model server");
    expect(runModelWords(route.provider, route.model, models)).toBe("Your model server");
    const row = models.find((m) => m.provider === route.provider && m.model === route.model)!;
    expect(modelText(row, models)).toBe("Your model server");
    render(<RunMeta result={result(route.provider, route.model ?? "")} />);
    expect(screen.getByTestId("run-result-model").textContent).toBe("answered by your model server");
  });
});
