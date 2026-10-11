/**
 * v1.333.0 (lane F, F7) — the Creative studio's one-second clock runs only
 * while the studio is SHOWN.
 *
 * A live studio session ran a 1 s `setNowTick` interval (a re-render of the
 * whole studio every second) while the user sat on the Creations tab (the
 * studio stays mounted, hidden, so a live session survives a peek) and while
 * the window was minimised. The clock only feeds what is drawn (the boot
 * counter, the "working" fallback), so it now runs only with the Create tab
 * active AND the window visible, and is set at once on the way back.
 *
 * Counted: the live 1000 ms intervals (never a clock reading). Harness:
 * creative-consent-relaunch-v1192's (the real page, the api mocked).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    getResponses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.getResponses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path === null ? null : (api.getResponses[path] ?? null),
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));

import CreativePage from "@/app/creative/page";

const ITEMS_PATH = "/creative/items?limit=500";
const STUDIO_KEY = "ironjarvis.creative.studio";
const VIEW_KEY = "ironjarvis.creative.view";
const DEST = "D:\\Renders";

/* ---- live 1 s intervals, counted ------------------------------------------ */

const live = new Map<unknown, number>(); // id -> ms
let realSet: typeof window.setInterval;
let realClear: typeof window.clearInterval;
const oneSecond = () => [...live.values()].filter((ms) => ms === 1000).length;

let hidden = false;
function setHidden(h: boolean) {
  hidden = h;
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

function seedLiveSession() {
  window.localStorage.setItem(VIEW_KEY, "create");
  window.localStorage.setItem(
    STUDIO_KEY,
    JSON.stringify({
      cli: "codex",
      dir: DEST,
      autopilot: true,
      session: {
        terminal_id: "t-1",
        dest: DEST,
        cli_label: "Codex",
        cli_id: "codex",
        skill: "Auto",
        skill_value: "",
        autopilot: true,
        command: "codex",
        sent_first: true,
        baseline: [],
        messages: ["make a teaser"],
        started_at: Date.now() - 60_000,
      },
    }),
  );
  api.getResponses[ITEMS_PATH] = { items: [], count: 0 };
  api.getResponses["/terminals/ai-clis"] = {
    clis: [{ id: "codex", label: "Codex", installed: true, command: "codex" }],
  };
  api.getResponses["/skills"] = { skills: [] };
  api.getResponses[`/fs/list?path=${encodeURIComponent(DEST)}`] = { path: DEST, entries: [] };
  api.getResponses[`/creative/studio-media?path=${encodeURIComponent(DEST)}`] = { files: [], truncated: false };
  const alive = { alive: true, exit_code: null, tail: "working…", mode: null, automode: false, ready: true };
  api.getResponses["/creative/studio/t-1/tail?chars=1"] = alive;
  api.getResponses["/creative/studio/t-1/tail?chars=4000"] = alive;
}

async function reachLiveStudio() {
  render(<CreativePage />);
  fireEvent.click(await screen.findByRole("button", { name: /Resume session/i }));
  // The live studio: its clock is running.
  await waitFor(() => expect(oneSecond()).toBe(1));
}

beforeEach(() => {
  window.localStorage.clear();
  api.gets = [];
  api.getResponses = {};
  window.HTMLElement.prototype.scrollTo = vi.fn();
  live.clear();
  realSet = window.setInterval;
  realClear = window.clearInterval;
  window.setInterval = ((fn: TimerHandler, ms?: number, ...rest: unknown[]) => {
    const id = realSet(fn, ms, ...rest);
    live.set(id, ms ?? 0);
    return id;
  }) as typeof window.setInterval;
  window.clearInterval = ((id?: number) => {
    live.delete(id);
    realClear(id);
  }) as typeof window.clearInterval;
  hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => (hidden ? "hidden" : "visible"),
  });
});

afterEach(() => {
  cleanup();
  window.setInterval = realSet;
  window.clearInterval = realClear;
  vi.restoreAllMocks();
  delete (document as unknown as Record<string, unknown>).hidden;
  delete (document as unknown as Record<string, unknown>).visibilityState;
});

describe("the studio clock ticks only while the studio is shown", () => {
  it("a minimised window runs no 1 s clock; it is back the moment the window is visible", async () => {
    seedLiveSession();
    await reachLiveStudio();
    setHidden(true);
    expect(oneSecond()).toBe(0);
    setHidden(false);
    expect(oneSecond()).toBe(1);
  });

  it("the Creations tab (studio mounted underneath) runs no 1 s clock; Create brings it back", async () => {
    seedLiveSession();
    await reachLiveStudio();
    fireEvent.click(screen.getByRole("tab", { name: /Creations/ }));
    expect(oneSecond()).toBe(0);
    fireEvent.click(screen.getByRole("tab", { name: /Create$/ }));
    expect(oneSecond()).toBe(1);
  });
});
