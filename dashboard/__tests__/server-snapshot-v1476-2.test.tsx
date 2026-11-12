/**
 * v1.476.2: React warns "The result of getServerSnapshot should be cached to
 * avoid an infinite loop" when a store's server snapshot is a new object on
 * every call. Emergency stop's strip (in the layout, so on every page) did
 * that; it is one constant now. React checks it while it takes over the
 * server's HTML in the browser (hydration).
 */
import React from "react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useEmergencyStop } from "@/components/EmergencyStopStrip";

vi.mock("@/lib/api", () => ({
  get: () => new Promise(() => {}),
  post: () => Promise.resolve({}),
  ApiError: class extends Error {},
}));

function Probe() {
  const s = useEmergencyStop();
  return <span>{String(s.on)}</span>;
}

afterEach(() => vi.restoreAllMocks());

describe("Emergency stop's store on a server render", () => {
  it("hands React the same server snapshot every time (no warning)", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const box = document.createElement("div");
    box.innerHTML = renderToString(<Probe />);
    document.body.appendChild(box);
    act(() => {
      hydrateRoot(box, <Probe />);
    });
    expect(box.textContent).toBe("false");
    const said = err.mock.calls.map((c) => String(c[0]));
    expect(said.filter((m) => m.includes("getServerSnapshot"))).toEqual([]);
  });
});
