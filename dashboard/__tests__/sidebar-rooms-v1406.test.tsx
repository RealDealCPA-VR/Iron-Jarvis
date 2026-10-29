/**
 * v1.406.0 part 5 — fewer rooms in the menu.
 *
 * The sidebar (and the phone drawer, the same body) keeps Chat (with its
 * Chat | Code switch), Build (the terminals), Projects, Everything and
 * Settings. The automation and admin rooms — agents and missions, goals
 * (Autonomy), workflows, schedules, reflexes, sentinels, webhooks, the
 * fleet, the session board, sessions, activity, usage, self-development —
 * are NOT sidebar items; each stays on Everything and in Ctrl K, and still
 * opens by its address (every route is in the surface manifest — pinned in
 * everything-v1321).
 *
 * Also pinned: the sidebar's list heading follows the chat page's view
 * ("Chats" in Chat, "Coding sessions" in Code), and Ctrl K has a row for the
 * Code view itself.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";

const nav = vi.hoisted(() => ({ pathname: "/chat" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    if (path.startsWith("/sessions")) return { sessions: [] };
    if (path.startsWith("/chat/threads")) return { threads: [] };
    if (path.startsWith("/projects")) return { projects: [] };
    return {};
  },
  post: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { version: "1.406.0", providers: [] }, refresh: () => {} }),
}));

import { AppSidebar } from "@/components/AppSidebar";
import EverythingPage from "@/app/everything/page";
import { SIDEBAR_EXCLUDED, SIDEBAR_HREFS, SURFACES, surfaceFor } from "@/lib/surfaces";
import { keepView, resetViewMemory } from "@/lib/chatSurface";
import { CODE_VIEW_HREF } from "@/lib/chatSurface";

beforeEach(() => {
  nav.pathname = "/chat";
  resetViewMemory();
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(() => cleanup());

describe("the sidebar's items", () => {
  it("are Chat, Build, Projects, Everything, Settings — and no automation or admin room", () => {
    render(<AppSidebar />);
    const navEl = screen.getByTestId("sidebar-nav");
    const hrefs = within(navEl)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/chat", "/terminals", "/projects", "/everything", "/settings"]);
    for (const room of SIDEBAR_EXCLUDED) {
      expect(hrefs, room).not.toContain(room);
      expect((SIDEBAR_HREFS as readonly string[]).includes(room), room).toBe(false);
    }
  });

  it("the excluded rooms are real pages, each on Everything (so two clicks from home)", () => {
    render(<EverythingPage />);
    const grid = screen.getByTestId("everything-grid");
    const listed = within(grid)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    for (const room of SIDEBAR_EXCLUDED) {
      expect(surfaceFor(room), room).toBeTruthy();
      expect(listed, room).toContain(room);
    }
  });

  it("the excluded rooms and the Code view are Ctrl K rows", async () => {
    const { CommandPalette } = await import("@/components/CommandPalette");
    render(<CommandPalette />);
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
    });
    // The palette lists every surface (the manifest); the Code view is one.
    const all = SURFACES.map((s) => s.href);
    for (const room of SIDEBAR_EXCLUDED) expect(all, room).toContain(room);
    expect(all).toContain(CODE_VIEW_HREF);
    expect(surfaceFor(CODE_VIEW_HREF)!.label).toBe("Code");
    expect(surfaceFor(CODE_VIEW_HREF)!.group).toBe("primary"); // not an Everything entry
  });

  it("the Chat row is lit on the chat page, home included", () => {
    nav.pathname = "/";
    render(<AppSidebar />);
    expect(screen.getByTestId("sidebar-nav-chat").getAttribute("aria-current")).toBe("page");
    expect(screen.getByTestId("sidebar-nav-terminals").getAttribute("aria-current")).toBeNull();
  });
});

describe("the sidebar's list heading follows the chat page's view", () => {
  it("Chats in Chat; Coding sessions in Code; Chats again elsewhere", () => {
    render(<AppSidebar />);
    expect(screen.getByTestId("sidebar-list-heading").textContent?.trim()).toBe("Chats");
    act(() => keepView("code"));
    expect(screen.getByTestId("sidebar-list-heading").textContent?.trim()).toBe("Coding sessions");
    cleanup();
    nav.pathname = "/projects";
    render(<AppSidebar />);
    expect(screen.getByTestId("sidebar-list-heading").textContent?.trim()).toBe("Chats");
  });
});
