/**
 * v1.449.0 (plan 0h.5 item 13, Wave B "Did it work?, everywhere") — the orb's
 * "Running now" list shows EVERY kind of background work the daemon lists,
 * not only agent runs and chats:
 *
 *   - WORKFLOW runs still live (running, resuming, waiting, cancelling) from
 *     GET /workflows/runs (slim), worded by the ONE status table
 *     ("Waiting for you" first and amber; "Stopping" after a Stop press),
 *     opened on /workflows;
 *   - MEDIA jobs queued or running from GET /media/jobs ("Making an image"),
 *     opened on their chat, else the Studio; a queued one sits with the
 *     queued runs;
 *   - NIGHT tasks at work from GET /build/night (state running only), opened
 *     on their coding session, else the Code view.
 *
 * Each kind is one row in the same shape; a finished or terminal row is not
 * listed; an older daemon without a route (404) lists none of that kind and
 * never hides the rest. The sidebar's REAL orb is driven; only the network
 * (lib/api), the router and the /events socket are doubles.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: [] as string[],
  replies: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    API_BASE: "",
    ApiError,
    ijToken: () => "",
    wsUrl: (p: string) => `ws://daemon${p}`,
    get: async (path: string) => {
      H.gets.push(path);
      if (path in H.replies) {
        const r = H.replies[path];
        if (r instanceof Error) throw r;
        return r;
      }
      throw new ApiError(`unmocked GET ${path}`, 404);
    },
    post: async () => ({}),
    put: async () => ({}),
  };
});

const nav = vi.hoisted(() => ({ pathname: "/usage", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push, replace: vi.fn(), prefetch: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import { AppSidebar } from "@/components/AppSidebar";
import { RUN_EDGE_TYPES } from "@/components/RunningNow";
import { EventsHub, EventsProvider } from "@/lib/useEvents";
import { __resetChatList } from "@/lib/chatList";
import { __resetApiCache } from "@/lib/apiCache";
import { buildSessionHref, CODE_VIEW_LINK } from "@/lib/buildLinks";
import {
  LIVE_MEDIA_STATUSES,
  LIVE_WORKFLOW_STATUSES,
  RUNNING_MEDIA_PATH,
  RUNNING_NIGHT_PATH,
  RUNNING_SESSIONS_PATH,
  RUNNING_WORKFLOWS_PATH,
  mediaJobItems,
  nightTaskItems,
  runningItems,
  workflowRunItems,
} from "@/lib/runningNow";
import { statusWords } from "@/lib/statusWords";
import { WORKFLOW_RUN_TERMINAL } from "@/lib/types";

const W = { timeout: 5_000 };

class FakeWS {
  static instances: FakeWS[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
  }
  close() {}
  open() {
    this.onopen?.();
  }
  frame(id: string, type: string, payload: Record<string, unknown> = {}, session_id: string | null = null) {
    this.onmessage?.({ data: JSON.stringify({ id, type, session_id, ts: "2026-11-07T00:00:00Z", payload }) });
  }
}
const socket = () => FakeWS.instances[FakeWS.instances.length - 1];
const count = (path: string) => H.gets.filter((g) => g === path).length;

function ago(ms: number): string {
  return new Date(Date.now() - ms).toISOString().replace("Z", "");
}
const MIN = 60_000;

function wrap(ui: React.ReactElement) {
  return render(<EventsProvider hub={new EventsHub()}>{ui}</EventsProvider>);
}

const orb = () => screen.getByTestId("mood-orb");

function seed() {
  H.replies[RUNNING_SESSIONS_PATH] = { sessions: [] };
  H.replies["/chat/threads"] = { threads: [] };
  H.replies["/projects"] = { projects: [] };
  H.replies[RUNNING_WORKFLOWS_PATH] = {
    runs: [
      { id: "r-run", workflow_name: "nightly brief", status: "running", started_at: ago(3 * MIN + 5_000) },
      { id: "r-wait", workflow_name: "intake", status: "waiting", started_at: ago(12 * MIN + 5_000) },
      { id: "r-done", workflow_name: "old one", status: "completed", started_at: ago(60 * MIN) },
      { id: "r-stop", workflow_name: "stopping one", status: "cancelling", started_at: ago(MIN + 5_000) },
    ],
  };
  H.replies[RUNNING_MEDIA_PATH] = {
    jobs: [
      { id: "m-run", kind: "image", status: "running", prompt: "A lighthouse at dusk\nin oils", thread_id: "t-art", created_at: ago(2 * MIN + 5_000) },
      { id: "m-queue", kind: "video", status: "queued", prompt: "A short clip", thread_id: "", created_at: ago(MIN) },
      { id: "m-done", kind: "image", status: "succeeded", prompt: "Finished", thread_id: "t-art", created_at: ago(9 * MIN) },
    ],
  };
  H.replies[RUNNING_NIGHT_PATH] = {
    tasks: [
      { id: "n-run", folder: "C:\\work\\shop", words: "Add the invoice export", state: "running", thread_id: "b-night", started_at: ago(25 * MIN + 5_000) },
      { id: "n-queue", folder: "C:\\work\\shop", words: "Later tonight", state: "queued", thread_id: "", started_at: null },
      { id: "n-done", folder: "C:\\work\\shop", words: "Done at dawn", state: "done", thread_id: "b-old", started_at: ago(8 * 60 * MIN) },
    ],
    window: { start: "23:00", end: "07:00", open: false },
  };
}

beforeEach(() => {
  H.gets.length = 0;
  H.replies = {};
  FakeWS.instances = [];
  nav.pathname = "/usage";
  nav.push.mockReset();
  window.localStorage.clear();
  __resetChatList();
  __resetApiCache();
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
});

const NOW = Date.now();

describe("lib/runningNow: the three new kinds, in words", () => {
  it("a workflow run: live statuses only, the ONE table's words, opened on /workflows", () => {
    const rows = [
      { id: "r-run", workflow_name: "nightly brief", status: "running", started_at: ago(3 * MIN + 5_000) },
      { id: "r-wait", workflow_name: "intake", status: "waiting", started_at: ago(12 * MIN + 5_000) },
      { id: "r-stop", workflow_name: "stopping one", status: "cancelling", started_at: ago(MIN + 5_000) },
      { id: "r-resume", workflow_name: "picked up", status: "resuming", started_at: ago(MIN + 5_000) },
      { id: "r-done", workflow_name: "old", status: "completed", started_at: ago(60 * MIN) },
      { id: "r-part", workflow_name: "part", status: "completed_with_failures", started_at: ago(60 * MIN) },
      { id: "r-int", workflow_name: "cut", status: "interrupted", started_at: ago(60 * MIN) },
    ];
    const items = workflowRunItems(rows, NOW);
    // Newest start first; the finished, part-failed and cut-off runs are left out.
    expect(items.map((i) => i.key)).toEqual(["r-stop", "r-resume", "r-run", "r-wait"].map((k) => `w:${k}`));
    const run = items.find((i) => i.key === "w:r-run")!;
    expect(run).toMatchObject({ kind: "workflow", who: "Workflow", when: "3 min", detail: "nightly brief", href: "/workflows", waiting: false, queued: false });
    expect(run.line).toBe("Workflow · 3 min");
    const wait = items.find((i) => i.key === "w:r-wait")!;
    expect(wait.waiting).toBe(true);
    expect(wait.line).toBe(`Workflow · 12 min · ${statusWords("waiting").word}`);
    expect(wait.line).toContain("Waiting for you");
    const stop = items.find((i) => i.key === "w:r-stop")!;
    expect(stop.line).toBe(`Workflow · 1 min · ${statusWords("cancelling").word}`);
    expect(stop.line).toContain("Stopping");
    // The live set is exactly what the terminal set is not.
    for (const s of WORKFLOW_RUN_TERMINAL) expect(LIVE_WORKFLOW_STATUSES.has(s)).toBe(false);
    for (const s of LIVE_WORKFLOW_STATUSES) expect(WORKFLOW_RUN_TERMINAL.has(s)).toBe(false);
  });

  it("a media job: queued or running only, named by its kind, opened on its chat else the Studio", () => {
    const rows = [
      { id: "m-run", kind: "image", status: "running", prompt: "A lighthouse at dusk\nin oils", thread_id: "t-art", created_at: ago(2 * MIN + 5_000) },
      { id: "m-queue", kind: "video", status: "queued", prompt: "", model: "pixio/clip", thread_id: "", created_at: ago(MIN) },
      { id: "m-done", kind: "image", status: "succeeded", prompt: "Finished", thread_id: "t-art", created_at: ago(9 * MIN) },
      { id: "m-fail", kind: "image", status: "failed", prompt: "Broke", thread_id: "t-art", created_at: ago(9 * MIN) },
    ];
    const items = mediaJobItems(rows, NOW);
    expect(items.map((i) => i.key)).toEqual(["m:m-queue", "m:m-run"]);
    expect(items[1]).toMatchObject({ kind: "media", who: "Making an image", when: "2 min", detail: "A lighthouse at dusk", href: "/chat?thread=t-art", queued: false, waiting: false });
    expect(items[1].line).toBe("Making an image · 2 min");
    expect(items[0]).toMatchObject({ who: "Making a video", when: statusWords("queued").word, detail: "pixio/clip", href: "/creative", queued: true });
    expect(LIVE_MEDIA_STATUSES.has("succeeded")).toBe(false);
  });

  it("a night task: running only, its words under it, opened on its coding session else the Code view", () => {
    const rows = [
      { id: "n-run", folder: "C:\\work\\shop", words: "Add the invoice export", state: "running", thread_id: "b-night", started_at: ago(25 * MIN + 5_000) },
      { id: "n-bare", folder: "C:\\work\\shop", words: "", state: "running", thread_id: "", started_at: ago(5 * MIN + 5_000) },
      { id: "n-queue", folder: "C:\\work\\shop", words: "Later", state: "queued", thread_id: "", started_at: null },
      { id: "n-done", folder: "C:\\work\\shop", words: "Done", state: "done", thread_id: "b-old", started_at: ago(8 * 60 * MIN) },
      { id: "n-need", folder: "C:\\work\\shop", words: "Needs you", state: "needs_you", thread_id: "b-need", started_at: ago(60 * MIN) },
    ];
    const items = nightTaskItems(rows, NOW);
    expect(items.map((i) => i.key)).toEqual(["n:n-bare", "n:n-run"]);
    expect(items[1]).toMatchObject({ kind: "night", who: "Night task", when: "25 min", detail: "Add the invoice export", href: buildSessionHref("b-night") });
    expect(items[1].line).toBe("Night task · 25 min");
    // No words: the folder's name; no session yet: the Code view.
    expect(items[0]).toMatchObject({ detail: "shop", href: CODE_VIEW_LINK });
  });

  it("the whole list: what waits first, then what runs (runs, chats, coding, workflows, media, night), then what is queued", () => {
    const items = runningItems(
      [{ id: "s-run", status: "active", task: "a run", created_at: ago(5 * MIN + 5_000) }, { id: "s-q", status: "queued", task: "a queued run" }],
      [{ id: "t-run", title: "Chat", running: true }],
      NOW,
      null,
      {
        workflows: [
          { id: "r-run", workflow_name: "nightly brief", status: "running", started_at: ago(3 * MIN) },
          { id: "r-wait", workflow_name: "intake", status: "waiting", started_at: ago(12 * MIN) },
        ],
        media: [
          { id: "m-run", kind: "image", status: "running", prompt: "A lighthouse", thread_id: "t-art", created_at: ago(2 * MIN) },
          { id: "m-queue", kind: "video", status: "queued", prompt: "A clip", thread_id: "", created_at: ago(MIN) },
        ],
        night: [{ id: "n-run", folder: "C:\\work\\shop", words: "Invoices", state: "running", thread_id: "b-night", started_at: ago(25 * MIN) }],
      },
    );
    expect(items.map((i) => i.key)).toEqual(["w:r-wait", "s:s-run", "c:t-run", "w:r-run", "m:m-run", "n:n-run", "s:s-q", "m:m-queue"]);
    // Nothing read of the three: exactly the old list.
    const old = runningItems([{ id: "s-run", status: "active", task: "a run", created_at: ago(5 * MIN) }], null, NOW, null);
    expect(old.map((i) => i.key)).toEqual(["s:s-run"]);
    expect(runningItems(null, null, NOW, null, { workflows: null, media: null, night: null })).toEqual([]);
  });
});

describe("the orb's list", () => {
  it("reads the three routes once opened and lists a workflow, a media job and a night task beside the rest", async () => {
    seed();
    wrap(<AppSidebar />);
    expect(count(RUNNING_WORKFLOWS_PATH)).toBe(0);
    fireEvent.click(orb());
    const pop = await screen.findByRole("dialog", { name: "Running now" }, W);
    await within(pop).findByText("Workflow · 3 min", {}, W);
    const rows = within(pop).getAllByTestId("running-row");
    const kinds = rows.map((r) => r.getAttribute("data-kind"));
    expect(kinds).toEqual(["workflow", "workflow", "workflow", "media", "night", "media"]);
    const lines = rows.map((r) => within(r).getByTestId("running-row-line").textContent);
    expect(lines[0]).toBe(`Workflow · 12 min · ${statusWords("waiting").word}`);
    expect(rows[0].getAttribute("data-waiting")).toBe("true");
    expect(rows[0].getAttribute("href")).toBe("/workflows");
    expect(lines).toContain(`Workflow · 1 min · ${statusWords("cancelling").word}`);
    expect(lines).toContain("Making an image · 2 min");
    expect(rows.find((r) => r.textContent?.includes("Making an image"))?.getAttribute("href")).toBe("/chat?thread=t-art");
    expect(lines).toContain("Night task · 25 min");
    expect(rows.find((r) => r.textContent?.includes("Night task"))?.getAttribute("href")).toBe(buildSessionHref("b-night"));
    expect(lines.at(-1)).toBe(`Making a video · ${statusWords("queued").word}`);
    // Finished rows of every kind are not listed.
    expect(pop.textContent).not.toContain("old one");
    expect(pop.textContent).not.toContain("Done at dawn");
    expect(pop.textContent).not.toContain("Finished");
    expect(count(RUNNING_WORKFLOWS_PATH)).toBe(1);
    expect(count(RUNNING_MEDIA_PATH)).toBe(1);
    expect(count(RUNNING_NIGHT_PATH)).toBe(1);
  });

  it("an older daemon without the routes (404) lists none of those kinds and never hides the rest", async () => {
    H.replies[RUNNING_SESSIONS_PATH] = {
      sessions: [{ id: "s-sched", status: "active", origin: "schedule:nightly-brief", task: "Brief", agent_type: "builder", created_at: ago(3 * MIN + 5_000) }],
    };
    H.replies["/chat/threads"] = { threads: [] };
    H.replies["/projects"] = { projects: [] };
    wrap(<AppSidebar />);
    fireEvent.click(orb());
    const pop = await screen.findByRole("dialog", { name: "Running now" }, W);
    await within(pop).findByText("Schedule · nightly-brief · 3 min", {}, W);
    await waitFor(() => expect(count(RUNNING_MEDIA_PATH)).toBe(1), W);
    expect(within(pop).getAllByTestId("running-row")).toHaveLength(1);
    expect(within(pop).queryByTestId("running-now-failed")).toBeNull();
  });

  it("a workflow that ENDS while the list is open drops at once (workflow.completed re-reads the list)", async () => {
    seed();
    wrap(<AppSidebar />);
    act(() => socket().open());
    fireEvent.click(orb());
    const pop = await screen.findByRole("dialog", { name: "Running now" }, W);
    await within(pop).findByText("Workflow · 3 min", {}, W);
    expect(RUN_EDGE_TYPES).toContain("workflow.completed");
    expect(RUN_EDGE_TYPES).toContain("media.completed");
    expect(RUN_EDGE_TYPES).toContain("media.failed");
    H.replies[RUNNING_WORKFLOWS_PATH] = { runs: [{ id: "r-run", workflow_name: "nightly brief", status: "completed", started_at: ago(4 * MIN) }] };
    H.replies[RUNNING_MEDIA_PATH] = { jobs: [] };
    act(() => socket().frame("e-end", "workflow.completed", { run_id: "r-run" }));
    await waitFor(() => expect(within(pop).queryByText("Workflow · 3 min")).toBeNull(), W);
    expect(count(RUNNING_WORKFLOWS_PATH)).toBe(2);
    expect(count(RUNNING_MEDIA_PATH)).toBe(2);
  });
});
