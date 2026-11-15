/**
 * v1.500.0 — ONE sidebar (the owner: the module's own second sidebar beside
 * the app sidebar is redundant).
 *
 *  - The app sidebar is the only left column. Hidden, it leaves the screen
 *    entirely (no icon strip); the title bar's "Show the sidebar" brings it
 *    back. The choice is kept per window (sessionStorage first, then the last
 *    choice made in any window).
 *  - A hidden sidebar stays mounted and KEEPS its list slot, so a module's
 *    list never drops back into the page as a second column.
 *  - Below "Settings" the list space follows the module: Chat's list on the
 *    chat surface, Build's own list on Build (never the recent chats), the
 *    recent chats elsewhere — now with "Archived chats".
 *
 * Build's half (the terminals and Build › Code's sessions in the slot) is
 * one-sidebar-build-v1500.test.tsx.
 */

import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const nav = vi.hoisted(() => ({ pathname: "/everything", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push, replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  API_BASE: "",
  get: async (path: string) => {
    if (path === "/chat/threads")
      return { threads: [{ id: "t1", title: "Quarterly numbers", updated_at: new Date().toISOString() }] };
    if (path === "/projects") return { projects: [] };
    return {};
  },
}));

import { AppSidebar } from "@/components/AppSidebar";
import { TitleBar } from "@/components/TitleBar";
import { setChatSlot, useChatSlot } from "@/lib/sidebarSlot";
import { SIDEBAR_HIDDEN_KEY, resetSidebarHidden } from "@/lib/sidebarState";
import { ChatCodeSurface } from "@/components/chat/ChatCodeSurface";
import { keepView } from "@/lib/chatSurface";

function setWide(wide: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q.includes("min-width") ? wide : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function SlotProbe() {
  const slot = useChatSlot();
  return <span data-testid="slot-probe">{slot ? slot.id : "none"}</span>;
}

function Shell() {
  return (
    <>
      <TitleBar />
      <AppSidebar />
      <SlotProbe />
    </>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  resetSidebarHidden();
  nav.pathname = "/everything";
  nav.push.mockReset();
  setWide(true);
  setChatSlot(null);
});
afterEach(() => cleanup());

describe("hiding the sidebar leaves open space, not an icon strip", () => {
  it("is shown by default, with no 'Show the sidebar' in the title bar", () => {
    render(<Shell />);
    const aside = screen.getByTestId("app-sidebar");
    expect(aside.getAttribute("data-hidden")).toBeNull();
    expect(aside.className).toContain("md:flex");
    expect(screen.getByTestId("sidebar-nav-terminals").textContent).toBe("Build");
    expect(screen.queryByTestId("titlebar-show-sidebar")).toBeNull();
  });

  it("Hide takes the whole column off the screen; the title bar brings it back", () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("button", { name: "Hide the sidebar" }));
    const aside = screen.getByTestId("app-sidebar");
    expect(aside.getAttribute("data-hidden")).toBe("true");
    // display:none at every width: nothing of it is drawn, no icon strip.
    expect(aside.className.split(/\s+/)).toContain("hidden");
    expect(aside.className).not.toContain("md:flex");
    expect(aside.className).not.toContain("w-14");
    expect(window.sessionStorage.getItem(SIDEBAR_HIDDEN_KEY)).toBe("1");
    expect(window.localStorage.getItem(SIDEBAR_HIDDEN_KEY)).toBe("1");

    const show = screen.getByRole("button", { name: "Show the sidebar" });
    fireEvent.click(show);
    expect(screen.getByTestId("app-sidebar").getAttribute("data-hidden")).toBeNull();
    expect(screen.getByTestId("app-sidebar").className).toContain("md:flex");
    expect(screen.queryByTestId("titlebar-show-sidebar")).toBeNull();
    expect(window.sessionStorage.getItem(SIDEBAR_HIDDEN_KEY)).toBe("0");
  });

  it("a hidden sidebar keeps the list slot, so Chat never draws a second list", () => {
    nav.pathname = "/chat";
    render(<Shell />);
    expect(screen.getByTestId("slot-probe").textContent).toBe("ij-sidebar-chat-slot");
    fireEvent.click(screen.getByRole("button", { name: "Hide the sidebar" }));
    expect(screen.getByTestId("slot-probe").textContent).toBe("ij-sidebar-chat-slot");
  });

  it("the choice is this window's: its own wins, a new window takes the last one", () => {
    // Another window hid it last; THIS window had it shown.
    window.localStorage.setItem(SIDEBAR_HIDDEN_KEY, "1");
    window.sessionStorage.setItem(SIDEBAR_HIDDEN_KEY, "0");
    render(<Shell />);
    expect(screen.getByTestId("app-sidebar").getAttribute("data-hidden")).toBeNull();
    cleanup();
    // A new window (no choice of its own yet) opens as it was last left.
    window.sessionStorage.clear();
    resetSidebarHidden();
    render(<Shell />);
    expect(screen.getByTestId("app-sidebar").getAttribute("data-hidden")).toBe("true");
    expect(screen.getByTestId("titlebar-show-sidebar")).toBeTruthy();
  });

  it("the keyboard lands on the button that undoes the press", async () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("button", { name: "Hide the sidebar" }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(document.activeElement?.getAttribute("data-testid")).toBe("titlebar-show-sidebar");
    fireEvent.click(screen.getByRole("button", { name: "Show the sidebar" }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(document.activeElement?.getAttribute("data-testid")).toBe("sidebar-hide");
  });
});

describe("below Settings: the current module's list", () => {
  it("on Build: Build's own list space, never the recent chats", async () => {
    nav.pathname = "/terminals";
    render(<Shell />);
    const lists = screen.getByTestId("sidebar-lists");
    expect(lists.getAttribute("data-list")).toBe("build");
    expect(screen.getByTestId("slot-probe").textContent).toBe("ij-sidebar-chat-slot");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByTestId("sidebar-recent-chats")).toBeNull();
    // No "Chats" heading over Build's list (the list names itself).
    expect(screen.queryByTestId("sidebar-list-heading")).toBeNull();
  });

  it("on the chat surface: the chat page's own list, under 'Chats'", () => {
    nav.pathname = "/chat";
    render(<Shell />);
    expect(screen.getByTestId("sidebar-lists").getAttribute("data-list")).toBe("chat");
    expect(screen.getByTestId("sidebar-list-heading").textContent).toContain("Chats");
  });

  for (const page of ["/everything", "/settings", "/projects"]) {
    it(`on ${page}: the recent chats, with the way to the archived ones`, async () => {
      nav.pathname = page;
      render(<Shell />);
      const list = await screen.findByTestId("sidebar-recent-chats");
      expect(await within(list).findByTitle("Quarterly numbers")).toBeTruthy();
      fireEvent.click(within(list).getByRole("button", { name: "Archived chats" }));
      expect(nav.push).toHaveBeenCalledWith("/chat?archived=1");
    });
  }
});

/* ------------------------------------------- a chat row opens CHAT, always */

describe("a chat opened from the sidebar shows Chat, even after Code", () => {
  // The bug the merged sidebar surfaced: on Everything the sidebar lists the
  // chats; pressing one in a window that last showed Chat › Code landed on
  // Code, the chat hidden behind it. The chat strips its link parameters in
  // its own mount effect, which ran BEFORE the surface read the address. The
  // surface now reads it first (a layout effect).
  function StripsLikeTheChat() {
    useEffect(() => {
      const url = new URL(window.location.href);
      url.searchParams.delete("thread");
      url.searchParams.delete("archived");
      window.history.replaceState(null, "", url.toString());
    }, []);
    return <p>the chat</p>;
  }

  for (const link of ["/chat?thread=t1", "/chat?archived=1"]) {
    it(`${link} opens the Chat view in a window that last showed Code`, async () => {
      keepView("code");
      window.history.replaceState(null, "", link);
      render(<ChatCodeSurface renderChat={() => <StripsLikeTheChat />} />);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 10));
      });
      expect(screen.getByTestId("chat-view-panel-chat").hasAttribute("hidden")).toBe(false);
      expect(screen.queryByTestId("chat-view-panel-code")).toBeNull();
      keepView("chat");
      window.history.replaceState(null, "", "/");
    });
  }
});
