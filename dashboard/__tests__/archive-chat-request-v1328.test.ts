/**
 * lib/archiveChat (v1.328.0, calm chat W3-3) — the one request the shared
 * `post` cannot make, because a 409's `running` list must be read.
 *
 * Pinned:
 *  - POST /chat/threads/{id}/archive with {} (or {stop: true}), the bearer
 *    token when one is set, the id URL-encoded;
 *  - 409 → {kind: "busy", running} (blank entries dropped), never a throw;
 *  - 200 → {kind: "archived", stillRunning, note};
 *  - any other status throws an ApiError with the daemon's detail; a dead
 *    network throws status 0 ("daemon offline");
 *  - stillRunningNote: the daemon's note when it sent one, a plain fallback
 *    when it did not, nothing when everything stopped.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, API_BASE } from "@/lib/api";
import { archiveChat, stillRunningNote, unarchiveChat } from "@/lib/archiveChat";

type Call = { url: string; init: RequestInit };
let calls: Call[] = [];

function answer(status: number, body: unknown) {
  return vi.fn((url: string, init: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve({
      status,
      ok: status >= 200 && status < 300,
      statusText: "",
      json: async () => body,
      headers: { get: () => null },
    });
  });
}

beforeEach(() => {
  calls = [];
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("archiveChat", () => {
  it("posts {} for a plain archive and {stop: true} for a stop", async () => {
    vi.stubGlobal("fetch", answer(200, { id: "a b", archived: true, still_running: [] }));
    await archiveChat("a b");
    await archiveChat("a b", true);
    expect(calls.map((c) => c.url)).toEqual([
      `${API_BASE}/chat/threads/a%20b/archive`,
      `${API_BASE}/chat/threads/a%20b/archive`,
    ]);
    expect(calls.map((c) => c.init.method)).toEqual(["POST", "POST"]);
    expect(calls.map((c) => JSON.parse(String(c.init.body)))).toEqual([{}, { stop: true }]);
  });

  it("sends the bearer token when one is set", async () => {
    window.localStorage.setItem("ij_token", "tok-123");
    vi.stubGlobal("fetch", answer(200, { archived: true }));
    await archiveChat("t1");
    const headers = calls[0].init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-123");
  });

  it("a 409 is 'busy' with the daemon's list, not a throw", async () => {
    vi.stubGlobal(
      "fetch",
      answer(409, { detail: "This chat is still working.", running: ["a reply in progress", " ", 3] }),
    );
    const out = await archiveChat("t1");
    expect(out).toEqual({ kind: "busy", running: ["a reply in progress"], detail: "This chat is still working." });
  });

  it("a 200 reports what kept going, with the daemon's note", async () => {
    vi.stubGlobal(
      "fetch",
      answer(200, { archived: true, still_running: ["a reply in progress"], note: "Archived. It will finish." }),
    );
    expect(await archiveChat("t1", true)).toEqual({
      kind: "archived",
      stillRunning: ["a reply in progress"],
      note: "Archived. It will finish.",
    });
  });

  it("another status throws the daemon's detail; a dead network is status 0", async () => {
    vi.stubGlobal("fetch", answer(404, { detail: "no such thread" }));
    const err = await archiveChat("gone").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("no such thread");

    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    const off = await archiveChat("t1").catch((e: unknown) => e);
    expect(off).toBeInstanceOf(ApiError);
    expect((off as ApiError).status).toBe(0);
  });
});

describe("unarchiveChat", () => {
  it("posts to the unarchive route", async () => {
    vi.stubGlobal("fetch", answer(200, { id: "t1", archived: false }));
    await unarchiveChat("t1");
    expect(calls[0].url).toBe(`${API_BASE}/chat/threads/t1/unarchive`);
    expect(calls[0].init.method).toBe("POST");
  });
});

describe("stillRunningNote", () => {
  it("is the daemon's note, a plain fallback, or nothing", () => {
    expect(stillRunningNote([], null)).toBeNull();
    expect(stillRunningNote([], "ignored")).toBeNull();
    expect(stillRunningNote(["a reply in progress"], "Archived. It will finish.")).toBe(
      "Archived. It will finish.",
    );
    expect(stillRunningNote(["a reply in progress"], null)).toBe(
      "Archived. This will finish on its own: a reply in progress.",
    );
  });
});
