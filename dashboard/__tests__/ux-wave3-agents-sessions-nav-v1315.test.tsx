/**
 * v1.315.0 — UX wave 3, track T2: the Session board (/kanban) is FINDABLE.
 *
 * Finding kanban-orphan-route (fix_adjustment wins): the board is reached
 * from the bell, yet search sent "kanban" to Projects and the crumb named it
 * with a developer word. It becomes the "Session board": a NON-RAIL entry
 * (never a new sidebar row — nav.test pins the rail and NAV_ENTRIES stays
 * exactly the rail) that the title-bar crumb and the search box both resolve.
 * The Projects alias "kanban" stays (nav.test pins ["kanban", "/projects"]).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const routerMock = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }));

const apiData = vi.hoisted(() => ({ data: {} as Record<string, unknown> }));
vi.mock("@/lib/api", () => ({
  get: (path: string) => Promise.resolve(apiData.data[path] ?? {}),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout", "layoutId", "whileHover", "whileTap"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

import { CommandPalette } from "@/components/CommandPalette";
import { NAV, NAV_ENTRIES, labelForPath } from "@/lib/nav";

beforeEach(() => {
  apiData.data = { "/skills": { skills: [] }, "/chat/threads": { threads: [] }, "/projects": { projects: [] } };
  routerMock.push.mockReset();
});
afterEach(cleanup);

async function open() {
  await act(async () => {
    window.dispatchEvent(new Event("ij:open-palette"));
  });
}
async function type(value: string) {
  await act(async () => {
    fireEvent.change(screen.getByRole("combobox"), { target: { value } });
  });
}
const optionTexts = () => screen.queryAllByRole("option").map((o) => o.textContent ?? "");

describe("the Session board has a name the shell can say", () => {
  it("the crumb for /kanban is 'Session board'", () => {
    expect(labelForPath("/kanban")).toBe("Session board");
  });

  it("CONTROL: it is NOT a rail row — the sidebar catalogue is untouched", () => {
    expect(NAV_ENTRIES.map((e) => e.href)).not.toContain("/kanban");
    expect(NAV.flatMap((s) => s.items).some((e) => /session board/i.test(e.label))).toBe(false);
    // and the Projects alias stays (nav.test: ["kanban", "/projects"])
    expect(NAV_ENTRIES.find((e) => e.href === "/projects")?.aliases).toContain("kanban");
  });
});

describe("the search box finds the Session board", () => {
  it("'session board' puts the board first, and Enter goes to /kanban", async () => {
    render(<CommandPalette />);
    await open();
    await type("session board");
    const first = optionTexts()[0] ?? "";
    expect(first).toContain("Session board");
    await act(async () => {
      fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    });
    expect(routerMock.push).toHaveBeenCalledWith("/kanban");
  });

  it("'kanban' offers the Session board (and still offers Projects)", async () => {
    render(<CommandPalette />);
    await open();
    await type("kanban");
    const texts = optionTexts();
    expect(texts.some((t) => t.includes("Session board"))).toBe(true);
    expect(texts.some((t) => t.includes("Projects"))).toBe(true);
  });
});
