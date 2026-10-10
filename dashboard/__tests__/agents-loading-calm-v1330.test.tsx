/**
 * v1.330.0 (calm chat wave 10, L3): the Agents page waits without a box.
 *
 * ScreenLoading (app/agents/page.tsx) is what /agents shows before the URL is
 * read and while a deferred screen (Your team, an old room) loads. It was a
 * pulsing card-surface block; the screens it stands in for sit on the page
 * with no card since v1.329.0. It is now one quiet "Loading…" line a screen
 * reader hears (role=status) over three faint lines under a hairline, and it
 * keeps the screen's full height so nothing jumps when the real screen lands.
 *
 * The Team screen's module is HELD here (its import waits on a gate the test
 * opens), so the loading shape is observed for real, then replaced.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

const G = vi.hoisted(() => {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => {
    release = r;
  });
  return { gate, release: () => release() };
});

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 500;
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    get: () => new Promise(() => {}),
    post: () => new Promise(() => {}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
// The Team screen arrives only when the test opens the gate.
vi.mock("@/components/agents/mission/TeamScreen", async () => {
  await G.gate;
  return { TeamScreen: () => <div data-testid="team-screen">Your team</div> };
});
vi.mock("@/components/agents/mission/RoomTranscript", async () => {
  await G.gate;
  return { RoomTranscript: () => <div data-testid="room-transcript" /> };
});

import AgentsPage from "@/app/agents/page";

const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const LITERAL_HUE = new RegExp(`(?:text|bg|border|ring|from|via|to|fill|stroke)-(?:${HUES})-\\d{2,3}`);

/** Every class token on the element and everything inside it. */
function allClasses(el: Element): string[] {
  return [el, ...Array.from(el.querySelectorAll("*"))].flatMap((n) =>
    (n.getAttribute("class") ?? "").split(/\s+/).filter(Boolean),
  );
}

function expectCalmLoading(el: HTMLElement) {
  // A screen reader hears it: a polite status that is busy, with words.
  expect(el.getAttribute("role")).toBe("status");
  expect(el.getAttribute("aria-busy")).toBe("true");
  expect(el.getAttribute("aria-live")).toBe("polite");
  expect(el.textContent?.trim()).toBe("Loading…");
  const own = (el.getAttribute("class") ?? "").split(/\s+/);
  // No card: no card-surface, no border box, no fill, no pulsing block.
  expect(own).not.toContain("card-surface");
  expect(own.filter((c) => /^(border|rounded|shadow|bg-)/.test(c))).toEqual([]);
  expect(own).not.toContain("animate-pulse");
  // Keeps the screen's full height (minus the demo strip): no jump on arrival.
  expect(own).toContain("h-[calc(100vh-7rem-var(--ij-strip-h,0px))]");
  expect(own).toContain("min-h-[32rem]");
  const every = allClasses(el);
  expect(every).not.toContain("card-surface");
  // Theme tokens only, whole pixels only.
  expect(every.filter((c) => LITERAL_HUE.test(c))).toEqual([]);
  expect(every.filter((c) => /text-\[\d+\.\d+px\]/.test(c))).toEqual([]);
  // A pulse only where motion is welcome.
  expect(every.filter((c) => c === "animate-pulse")).toEqual([]);
  expect(every).toContain("motion-safe:animate-pulse");
  // The faint lines are decoration: hidden from a screen reader, and the
  // one hairline is a theme hairline.
  const lines = el.querySelector('[aria-hidden="true"].border-t');
  expect(lines).not.toBeNull();
  expect(lines!.className).toContain("border-white/[0.06]");
  expect(lines!.children.length).toBe(3);
}

beforeEach(() => {
  window.history.pushState(null, "", "/agents?view=team");
});
afterEach(() => {
  cleanup();
  window.history.pushState(null, "", "/");
});

describe("the Agents page loads without a boxed placeholder", () => {
  it("before the URL is read (the static HTML), the wait is the calm status line", async () => {
    const { renderToString } = await import("react-dom/server");
    const host = document.createElement("div");
    host.innerHTML = renderToString(<AgentsPage />);
    const el = host.querySelector<HTMLElement>('[data-testid="agents-screen-loading"]');
    expect(el).not.toBeNull();
    expectCalmLoading(el!);
  });

  it("while Your team's module loads, the calm line stands in, then the screen replaces it", async () => {
    render(<AgentsPage />);
    const loading = await screen.findByTestId("agents-screen-loading");
    expectCalmLoading(loading);
    // It is announced by its role and words.
    expect(screen.getByRole("status").textContent?.trim()).toBe("Loading…");
    expect(screen.queryByTestId("team-screen")).toBeNull();
    await act(async () => {
      G.release();
      await G.gate;
    });
    await waitFor(() => expect(screen.getByTestId("team-screen")).toBeTruthy());
    await waitFor(() => expect(screen.queryByTestId("agents-screen-loading")).toBeNull());
  });
});
