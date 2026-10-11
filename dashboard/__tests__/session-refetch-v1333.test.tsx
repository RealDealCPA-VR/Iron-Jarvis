/**
 * v1.333.0 (lane F, F8) — live events cost one render per subscriber that
 * READS them, and the session page refetches once per frame, never while
 * hidden.
 *
 *  - Nine `useEvents()` subscribers read one or two event types but took
 *    EVERY frame, so each frame re-rendered them for nothing. Each now passes
 *    `{ types }`. The source pin below keeps it that way: a new unfiltered
 *    subscriber must be added to the allowlist with its reason.
 *  - The session page re-read the transcript, the evaluation and the files
 *    panel on EVERY tool.executed, including in a minimised window. A burst
 *    inside one frame now costs one refetch, and frames that land while
 *    hidden cost one refetch when the window is visible again.
 *
 * Counts only (reloads, source matches).
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";

const pageApi = vi.hoisted(() => ({
  detail: null as unknown,
  events: [] as unknown[],
  reloads: [] as string[],
}));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {
    status = 0;
  },
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: () => Promise.reject(new Error("unmocked")),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path && /^\/sessions\/[^/]+$/.test(path) ? pageApi.detail : null,
    error: null,
    loading: false,
    reload: () => {
      if (path) pageApi.reloads.push(path);
    },
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: pageApi.events }) }));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({ enabled: false, supported: false, toggle: () => {}, speak: () => {}, stop: () => {} }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }) }));
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({ TimeTravelFeed: () => null }));
vi.mock("@/components/chat/DocPreview", () => ({ DocPreview: () => null, appLabelFor: () => "app" }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import SessionDetailPage from "@/app/sessions/[id]/page";

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

const DETAIL = "/sessions/s-1";
const detailReloads = () => pageApi.reloads.filter((p) => p === DETAIL).length;
let seq = 0;
/** A NEW tool.executed frame for s-1 on top of the window (newest first). */
function pushToolFrame() {
  seq += 1;
  pageApi.events = [
    { id: `e-${seq}`, type: "tool.executed", session_id: "s-1", ts: "2026-10-11T10:00:00Z", payload: { tool: "write_file" } },
    ...pageApi.events,
  ];
}
const frame = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 40)); // past the next animation frame
  });

let hidden = false;
function setHidden(h: boolean) {
  hidden = h;
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  pageApi.reloads = [];
  pageApi.events = [];
  pageApi.detail = {
    session: {
      id: "s-1",
      task: "task s-1",
      agent_type: "coder",
      provider: "openai",
      model: "gpt-x",
      status: "completed",
      workspace_path: "C:\\work",
      summary: "",
      origin: null,
      created_at: "2026-10-11T10:00:00Z",
      finished_at: "2026-10-11T10:05:00Z",
    },
    transcript: { runs: [], tools: [] },
  };
  hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => (hidden ? "hidden" : "visible"),
  });
});
afterEach(() => {
  cleanup();
  delete (document as unknown as Record<string, unknown>).hidden;
  delete (document as unknown as Record<string, unknown>).visibilityState;
});

describe("the session page refetches once per frame, and not while hidden", () => {
  it("a burst of 5 tool.executed frames inside one frame costs ONE refetch", async () => {
    const { rerender } = render(<SessionDetailPage params={fakeParams("s-1")} />);
    await act(async () => {});
    expect(detailReloads()).toBe(0);
    for (let i = 0; i < 5; i += 1) {
      pushToolFrame();
      rerender(<SessionDetailPage params={fakeParams("s-1")} />);
    }
    await frame();
    expect(detailReloads()).toBe(1);
    // Every refetch still covers all three: detail, evaluation, files.
    expect(pageApi.reloads).toContain("/sessions/s-1/evaluation");
  });

  it("frames in separate frames each refetch, as before", async () => {
    const { rerender } = render(<SessionDetailPage params={fakeParams("s-1")} />);
    await act(async () => {});
    for (let i = 0; i < 3; i += 1) {
      pushToolFrame();
      rerender(<SessionDetailPage params={fakeParams("s-1")} />);
      await frame();
    }
    expect(detailReloads()).toBe(3);
  });

  it("frames that land while hidden refetch NOTHING, then ONCE when the window is visible", async () => {
    const { rerender } = render(<SessionDetailPage params={fakeParams("s-1")} />);
    await act(async () => {});
    setHidden(true);
    for (let i = 0; i < 4; i += 1) {
      pushToolFrame();
      rerender(<SessionDetailPage params={fakeParams("s-1")} />);
      await frame();
    }
    expect(detailReloads()).toBe(0);
    setHidden(false);
    await frame();
    expect(detailReloads()).toBe(1);
  });
});

/* ---- every live-event subscriber says which types it reads ---------------- */

/** The subscribers that take EVERY frame on purpose, with the reason. */
const UNFILTERED_OK: Record<string, string> = {
  "components/EventStream.tsx": "the live feed shows every event",
  "components/TimeTravelFeed.tsx": "the time-travel feed shows every event",
  "app/sessions/[id]/page.tsx": "the session's own live feed shows every event of that session",
};

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (/\.(ts|tsx)$/.test(name)) out.push(p);
  }
  return out;
}

it("every useEvents() subscriber passes { types } unless it shows every event", () => {
  const root = join(__dirname, "..");
  const unfiltered: string[] = [];
  for (const dir of ["app", "components", "lib"]) {
    for (const file of sources(join(root, dir))) {
      const rel = relative(root, file).replace(/\\/g, "/");
      if (rel === "lib/useEvents.ts") continue; // the definition
      const src = readFileSync(file, "utf-8");
      // A call with ONE argument (or none): `useEvents()` / `useEvents(60)`.
      for (const m of src.matchAll(/\buseEvents\(\s*(\d*)\s*\)/g)) {
        unfiltered.push(`${rel}: ${m[0]}`);
      }
    }
  }
  const unexpected = unfiltered.filter((u) => !(u.split(":")[0] in UNFILTERED_OK));
  expect(unexpected).toEqual([]);
});
