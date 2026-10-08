/**
 * Calm UI redesign S0: one POST /ui/visit per page change, tagged "nav" when
 * a menu/palette/tile press (recordOpen) caused it, "link" otherwise. Best-
 * effort: a failing daemon is ignored.
 */
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ path: "/", posts: [] as { path: string; body: unknown }[], fail: false }));

vi.mock("next/navigation", () => ({ usePathname: () => H.path }));
vi.mock("@/lib/api", () => ({
  post: (path: string, body: unknown) => {
    H.posts.push({ path, body });
    return H.fail ? Promise.reject(new Error("down")) : Promise.resolve({ ok: true });
  },
}));

import { RouteVisitBeacon, VISIT_SETTLE_MS } from "@/components/RouteVisitBeacon";
import { recordOpen } from "@/lib/appTiles";

beforeEach(() => {
  vi.useFakeTimers();
  H.posts.length = 0;
  H.path = "/";
  H.fail = false;
});
afterEach(() => vi.useRealTimers());

describe("RouteVisitBeacon", () => {
  it("posts once per page change, after it settles", async () => {
    const { rerender } = render(<RouteVisitBeacon />);
    await act(async () => {
      vi.advanceTimersByTime(VISIT_SETTLE_MS);
    });
    expect(H.posts).toEqual([{ path: "/ui/visit", body: { route: "/", via: "link" } }]);
    H.path = "/settings";
    rerender(<RouteVisitBeacon />);
    // A redirect inside the settle window counts once, for where it landed.
    H.path = "/settings/";
    rerender(<RouteVisitBeacon />);
    await act(async () => {
      vi.advanceTimersByTime(VISIT_SETTLE_MS);
    });
    expect(H.posts.map((p) => (p.body as { route: string }).route)).toEqual(["/", "/settings/"]);
  });

  it("a nav press tags the visit 'nav' once", async () => {
    recordOpen("/workflows");
    H.path = "/workflows";
    render(<RouteVisitBeacon />);
    await act(async () => {
      vi.advanceTimersByTime(VISIT_SETTLE_MS);
    });
    expect(H.posts.at(-1)!.body).toEqual({ route: "/workflows", via: "nav" });
  });

  it("a failing daemon is ignored (nothing thrown, nothing retried)", async () => {
    H.fail = true;
    render(<RouteVisitBeacon />);
    await act(async () => {
      vi.advanceTimersByTime(VISIT_SETTLE_MS * 5);
    });
    expect(H.posts).toHaveLength(1);
  });
});
