/**
 * v1.226.0 (F-F-4, contract C4) — a pydantic 422 renders as "field: msg", not
 * "[object Object]". An older daemon returns a LIST detail
 * (`[{loc:["body","steps"], msg:"..."}]`); api.ts flattens it to
 * "steps: Input should be a valid list" (loc minus the leading "body").
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, flattenDetail, onNetworkError } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

describe("ApiError message for a pydantic 422 (v1.226.0)", () => {
  it("flattens a list-shaped detail to 'field: msg; field: msg'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        statusText: "Unprocessable Entity",
        json: async () => ({
          detail: [
            { type: "list_type", loc: ["body", "steps"], msg: "Input should be a valid list", input: "x" },
            { type: "missing", loc: ["body", "trigger", "kind"], msg: "Field required" },
          ],
        }),
      })),
    );
    await expect(api("/workflows", { method: "POST" })).rejects.toMatchObject({
      status: 422,
      message: "steps: Input should be a valid list; trigger.kind: Field required",
    });
  });

  it("flattenDetail: strings pass through, non-body loc is kept, odd items do not crash", () => {
    expect(flattenDetail("plain")).toBe("plain");
    expect(flattenDetail([{ loc: ["query", "limit"], msg: "too big" }])).toBe("query.limit: too big");
    expect(flattenDetail([{ msg: "no loc" }])).toBe("no loc");
    expect(flattenDetail(["a", 2])).toBe("a; 2");
    expect(flattenDetail([{ weird: true }])).toBe('{"weird":true}');
  });

  it("a network failure fires onNetworkError before the status-0 ApiError (v1.226.0)", async () => {
    let fired = 0;
    const off = onNetworkError(() => {
      fired += 1;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(api("/settings")).rejects.toMatchObject({ status: 0, message: "daemon offline" });
    expect(fired).toBe(1);
    off();
    // A 500 is NOT a network error — the listener stays quiet.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, statusText: "boom", json: async () => ({}) })),
    );
    await expect(api("/settings")).rejects.toMatchObject({ status: 500 });
    expect(fired).toBe(1);
  });

  it("a string detail (the daemon's own envelope) is unchanged", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        statusText: "Unprocessable Entity",
        json: async () => ({ detail: "steps: Input should be a valid list" }),
      })),
    );
    await expect(api("/workflows", { method: "POST" })).rejects.toMatchObject({
      status: 422,
      message: "steps: Input should be a valid list",
    });
  });
});
