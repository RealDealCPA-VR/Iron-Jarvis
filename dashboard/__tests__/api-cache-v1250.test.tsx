/**
 * Stale-while-revalidate for GET hooks (v1.250.0, S-04).
 *
 * Returning to a page the user had already opened showed a grey placeholder and
 * then the same content again: every `useApi` mount started from `null` and
 * waited for the network, even though the window had just displayed the answer.
 * The payload cache lets the hook start from what the path last returned and
 * revalidate behind it.
 *
 * What these pin:
 *   - the second mount has data on its FIRST render — the property the user
 *     actually feels, and the one a placeholder-free return visit depends on;
 *   - it still revalidates: a changed payload replaces the seeded one;
 *   - an error on the revalidation does NOT blank the seeded content;
 *   - the cache is bounded (LRU) and resettable, so a long session cannot grow
 *     it without limit and tests cannot leak state into each other.
 *
 * The cache lives OUTSIDE lib/api.ts on purpose: ~71 test files mock that
 * module wholesale, so helpers imported from it would look like failed fetches
 * (the v1.230.0 lesson, re-applied).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, get: (...a: unknown[]) => getMock(...a) };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ epoch: 0, health: null, refresh: () => {} }),
}));

import { useApi } from "@/lib/useApi";
import {
  __resetApiCache,
  cacheDrop,
  cacheSet,
  cacheSize,
  cachedGet,
} from "@/lib/apiCache";

/** Renders the hook's data and records what it held on the FIRST render. */
const firstRenders: Record<string, string> = {};
function Reader({ id, path = "/things" }: { id: string; path?: string }) {
  const { data, error } = useApi<{ label: string }>(path);
  if (!(id in firstRenders)) firstRenders[id] = data?.label ?? "(null)";
  return (
    <div>
      <span data-testid={`${id}-label`}>{data?.label ?? "(null)"}</span>
      <span data-testid={`${id}-error`}>{error ? error.message : "-"}</span>
    </div>
  );
}

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue({ label: "first" });
  for (const k of Object.keys(firstRenders)) delete firstRenders[k];
  __resetApiCache();
});

describe("the payload cache", () => {
  it("holds the last payload per path, and reports its size", () => {
    expect(cachedGet("/a")).toBeUndefined();
    cacheSet("/a", { label: "A" });
    expect(cachedGet<{ label: string }>("/a")).toEqual({ label: "A" });
    expect(cacheSize()).toBe(1);
    cacheDrop("/a");
    expect(cachedGet("/a")).toBeUndefined();
    expect(cacheSize()).toBe(0);
  });

  it("is BOUNDED — a long session cannot grow it without limit", () => {
    for (let i = 0; i < 300; i += 1) cacheSet(`/p${i}`, { label: String(i) });
    expect(cacheSize()).toBeLessThanOrEqual(120);
    // The most recent write is always still there.
    expect(cachedGet<{ label: string }>("/p299")).toEqual({ label: "299" });
  });

  it("__resetApiCache clears it (the hook between tests)", () => {
    cacheSet("/a", { label: "A" });
    __resetApiCache();
    expect(cacheSize()).toBe(0);
  });
});

describe("useApi + the cache", () => {
  it("a FIRST visit starts empty — placeholders are for content never seen", async () => {
    render(<Reader id="cold" />);
    expect(firstRenders.cold).toBe("(null)");
    await waitFor(() =>
      expect(screen.getByTestId("cold-label")).toHaveTextContent("first"),
    );
  });

  it("a RETURN visit has the content on its first render — no placeholder", async () => {
    const first = render(<Reader id="warm-a" />);
    await waitFor(() =>
      expect(screen.getByTestId("warm-a-label")).toHaveTextContent("first"),
    );
    first.unmount();

    render(<Reader id="warm-b" />);
    // THE claim: not "eventually" — immediately, on the very first render.
    expect(firstRenders["warm-b"]).toBe("first");
  });

  it("still revalidates: a changed payload replaces the seeded one", async () => {
    const first = render(<Reader id="rev-a" />);
    await waitFor(() =>
      expect(screen.getByTestId("rev-a-label")).toHaveTextContent("first"),
    );
    first.unmount();

    getMock.mockResolvedValue({ label: "second" });
    render(<Reader id="rev-b" />);
    expect(firstRenders["rev-b"]).toBe("first"); // seeded…
    await waitFor(() =>
      expect(screen.getByTestId("rev-b-label")).toHaveTextContent("second"),
    ); // …then fresh
  });

  it("a failed revalidation keeps the seeded content on screen", async () => {
    const first = render(<Reader id="err-a" />);
    await waitFor(() =>
      expect(screen.getByTestId("err-a-label")).toHaveTextContent("first"),
    );
    first.unmount();

    getMock.mockRejectedValue(new Error("daemon went away"));
    render(<Reader id="err-b" />);
    await waitFor(() =>
      expect(screen.getByTestId("err-b-error")).not.toHaveTextContent("-"),
    );
    // The error is surfaced, and what the user was reading is still there.
    expect(screen.getByTestId("err-b-label")).toHaveTextContent("first");
  });

  it("caches per PATH — one path's payload never answers another's", async () => {
    const first = render(<Reader id="p1" path="/things" />);
    await waitFor(() =>
      expect(screen.getByTestId("p1-label")).toHaveTextContent("first"),
    );
    first.unmount();

    getMock.mockResolvedValue({ label: "other" });
    render(<Reader id="p2" path="/others" />);
    expect(firstRenders.p2).toBe("(null)"); // never seen: no seed
  });
});
