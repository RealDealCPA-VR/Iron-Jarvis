/**
 * v1.311.0 (Wave 3 SPEED, Track B) — the Build Files panel's 4 s poll is
 * CONDITIONAL, and a short scan says so.
 *
 *  - The second poll sends the ETag the first response carried
 *    (`If-None-Match`, through `lib/api.ts`'s existing `ifNoneMatch` path and
 *    `lib/etag.ts::etagOf`); a 304 keeps the rows and does NOT re-render the
 *    panel (counted with a React Profiler — commits, not a clock).
 *  - ANTI-VACUITY: a changed answer (200, new tag) DOES re-render and shows
 *    the new file.
 *  - A response with `scan_truncated: true` + `scanned: N` says the list is the
 *    newest of the first N entries scanned — a short scan must never read as
 *    complete.
 *
 * Real `@/lib/api` (+ etag), real FilesPanel; only `fetch` is stubbed.
 */
import { Profiler } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FilesPanel } from "@/components/terminal/FilesPanel";

type FetchCall = { url: string; init: RequestInit };
type Reply = { status?: number; body?: unknown; etag?: string };

const calls: FetchCall[] = [];
const filesCalls = () => calls.filter((c) => c.url.includes("/fs/files"));
const headerOf = (call: FetchCall, name: string) =>
  ((call.init.headers || {}) as Record<string, string>)[name];

function stubFetch(route: (path: string, init: RequestInit, n: number) => Reply) {
  let n = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      const path = url.replace(/^https?:\/\/[^/]+/, "");
      const r = path.startsWith("/fs/files") ? route(path, init, n++) : { body: {} };
      const status = r.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 304 ? "Not Modified" : "OK",
        headers: { get: (k: string) => (k.toLowerCase() === "etag" ? (r.etag ?? null) : null) },
        json: async () => r.body ?? {},
      } as unknown as Response;
    }),
  );
}

const NOW_S = 1_800_000_000;
const row = (rel: string, mtime: number) => ({
  name: rel.split("/").pop(),
  path: `C:/work/${rel}`,
  rel,
  size: 10,
  mtime,
});
const BODY_A = {
  root: "C:/work",
  files: [row("one.txt", NOW_S - 100)],
  count: 1,
  truncated: false,
  scan_truncated: false,
  scanned: 3,
};
const BODY_B = {
  ...BODY_A,
  files: [row("two.txt", NOW_S - 1), row("one.txt", NOW_S - 100)],
  count: 2,
};

const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => {
  calls.length = 0;
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  vi.useFakeTimers();
  vi.setSystemTime(NOW_S * 1000);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
});

describe("FilesPanel polls conditionally", () => {
  it("sends If-None-Match on the next tick, keeps its rows on a 304 without re-rendering, and re-renders on a real change", async () => {
    stubFetch((_path, init, n) => {
      const sent = ((init.headers || {}) as Record<string, string>)["If-None-Match"];
      if (n >= 2) return { body: BODY_B, etag: 'W/"b"' }; // the folder changed
      if (sent === 'W/"a"') return { status: 304, etag: 'W/"a"' };
      return { body: BODY_A, etag: 'W/"a"' }; // the old client re-downloads every tick
    });
    let commits = 0;
    render(
      <Profiler id="files" onRender={() => (commits += 1)}>
        <FilesPanel folder="C:/work" />
      </Profiler>,
    );
    await advance(0);
    expect(screen.getByText("one.txt")).toBeTruthy();
    expect(filesCalls()).toHaveLength(1);
    const settled = commits;

    await advance(4000); // the 4 s tick
    expect(filesCalls()).toHaveLength(2);
    expect(headerOf(filesCalls()[1], "If-None-Match")).toBe('W/"a"');
    expect(screen.getByText("one.txt")).toBeTruthy();
    // Nothing changed, so nothing is drawn again.
    expect(commits).toBe(settled);

    await advance(4000); // ANTI-VACUITY: the folder changed -> 200 + new tag
    expect(filesCalls()).toHaveLength(3);
    expect(screen.getByText("two.txt")).toBeTruthy();
    expect(commits).toBeGreaterThan(settled);
  });

  it("says the list is the newest of the first N scanned when the scan was cut short", async () => {
    stubFetch(() => ({
      body: { ...BODY_A, scan_truncated: true, scanned: 20000 },
      etag: 'W/"s"',
    }));
    render(<FilesPanel folder="C:/work" />);
    await advance(0);
    expect(screen.getByText("one.txt")).toBeTruthy();
    expect(screen.getByText(/first\s+20,?000[^.]*scanned/i)).toBeTruthy();
  });

  it("control: a complete scan says nothing about scanning", async () => {
    stubFetch(() => ({ body: BODY_A, etag: 'W/"c"' }));
    render(<FilesPanel folder="C:/work" />);
    await advance(0);
    expect(screen.getByText("one.txt")).toBeTruthy();
    expect(screen.queryByText(/scanned/i)).toBeNull();
  });
});
