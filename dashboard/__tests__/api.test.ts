import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  ApiError,
  onRequestErrorChange,
  onUnauthorizedChange,
} from "@/lib/api";

/**
 * api.ts drives two app-wide signals off fetch results:
 *  - a 401/403 fires the "unauthorized" signal (stale/missing bearer token),
 *  - a >=500 fires the "request error" signal (avoids a false "empty install"),
 *  - a successful DATA request clears both,
 *  - a network throw becomes `ApiError(status: 0)` ("daemon offline").
 * All offline: global fetch is stubbed, never touching the network.
 */

function mockResponse(status: number, body: unknown = {}, statusText = ""): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.ts error + auth signals", () => {
  it("returns parsed JSON and clears both signals on success", async () => {
    let unauthorized = true;
    let failing = true;
    const offAuth = onUnauthorizedChange((v) => (unauthorized = v));
    const offErr = onRequestErrorChange((v) => (failing = v));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mockResponse(200, { hello: "world" })),
    );

    const data = await api<{ hello: string }>("/anything");

    expect(data).toEqual({ hello: "world" });
    expect(unauthorized).toBe(false);
    expect(failing).toBe(false);
    offAuth();
    offErr();
  });

  it("signals unauthorized and throws ApiError on 401", async () => {
    let unauthorized = false;
    const offAuth = onUnauthorizedChange((v) => (unauthorized = v));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mockResponse(401, { detail: "no token" }, "Unauthorized")),
    );

    await expect(api("/secret")).rejects.toMatchObject({ status: 401, message: "no token" });
    expect(unauthorized).toBe(true);
    offAuth();
  });

  it("signals a server error on 500", async () => {
    let failing = false;
    const offErr = onRequestErrorChange((v) => (failing = v));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mockResponse(500, { detail: "boom" }, "Server Error")),
    );

    await expect(api("/broken")).rejects.toBeInstanceOf(ApiError);
    expect(failing).toBe(true);
    offErr();
  });

  it("maps a network failure to ApiError(status 0) 'daemon offline'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      }),
    );

    await expect(api("/health-ish")).rejects.toMatchObject({
      status: 0,
      message: "daemon offline",
    });
  });
});
