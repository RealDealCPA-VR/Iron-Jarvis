/**
 * Calm UI redesign S7 — the persistent sidebar (T1, unit half; e2e/t1 runs it
 * in a browser).
 *
 * Four items (Build, Projects, Everything, Settings) and nothing else on a
 * fresh profile; pins (up to three) under them; "New chat" starts one in place
 * on the chat surface and navigates elsewhere; on the chat surface the CHATS
 * space is the chat page's own thread rail (a portal slot, wide screens
 * only); the phone drawer is the same body; Help lives in the footer menu.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const nav = vi.hoisted(() => ({ pathname: "/usage", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push, replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  API_BASE: "",
  get: async (path: string) => {
    if (path === "/chat/threads")
      return {
        threads: [
          { id: "t1", title: "Quarterly numbers", updated_at: new Date().toISOString() },
          { id: "t2", title: "Old one", updated_at: "2020-01-01T00:00:00" },
        ],
      };
    if (path === "/projects") return { projects: [{ id: "p1", name: "Q3 Bookkeeping" }, { id: "p2", name: "Gone", status: "archived" }] };
    return {};
  },
}));

import { AppSidebar } from "@/components/AppSidebar";
import { NavDrawer } from "@/components/Sidebar";
import { TitleBar } from "@/components/TitleBar";
import { NEW_CHAT_EVENT, setChatSlot, useChatSlot } from "@/lib/sidebarSlot";
import { PIN_KEY } from "@/lib/surfaces";

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

beforeEach(() => {
  window.localStorage.clear();
  nav.pathname = "/usage";
  nav.push.mockReset();
  setWide(true);
  setChatSlot(null);
});
afterEach(() => cleanup());

describe("the four items", () => {
  it("a fresh profile shows exactly Build, Projects, Everything and Settings", async () => {
    render(<AppSidebar />);
    const navEl = screen.getByTestId("sidebar-nav");
    const labels = within(navEl)
      .getAllByRole("link")
      .map((a) => a.textContent);
    expect(labels).toEqual(["Build", "Projects", "Everything", "Settings"]);
    expect(within(navEl).queryByTestId("sidebar-pins")).toBeNull();
  });

  it("pins sit under the four, never among them, and at most three", async () => {
    window.localStorage.setItem(PIN_KEY, JSON.stringify(["/usage", "/fleet", "/connections", "/skills"]));
    render(<AppSidebar />);
    const pins = await screen.findByTestId("sidebar-pins");
    expect(within(pins).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/usage", "/fleet", "/connections"]);
    expect(screen.getByTestId("sidebar-nav-usage").getAttribute("aria-current")).toBe("page");
  });

  it("collapses to icons and remembers it", () => {
    render(<AppSidebar />);
    fireEvent.click(screen.getByLabelText("Collapse the sidebar"));
    expect(screen.getByTestId("sidebar-nav-terminals").textContent).toBe("");
    expect(screen.getByTestId("sidebar-nav-terminals").getAttribute("title")).toBe("Build");
    expect(window.localStorage.getItem("ij_sidebar_collapsed")).toBe("1");
  });
});

describe("New chat", () => {
  it("away from the chat surface it opens a fresh chat", () => {
    render(<AppSidebar />);
    fireEvent.click(screen.getByTestId("sidebar-new-chat"));
    expect(nav.push).toHaveBeenCalledWith("/chat?new=1");
  });

  it("on the chat surface it starts one in place (the page owns that)", () => {
    nav.pathname = "/chat";
    render(<AppSidebar />);
    const seen = vi.fn();
    window.addEventListener(NEW_CHAT_EVENT, seen);
    fireEvent.click(screen.getByTestId("sidebar-new-chat"));
    window.removeEventListener(NEW_CHAT_EVENT, seen);
    expect(seen).toHaveBeenCalledTimes(1);
    expect(nav.push).not.toHaveBeenCalled();
  });
});

describe("the conversation list", () => {
  // v1.329.0 (calm chat W4 F2): the flat "Today / Previous 7 days" list is
  // gone. Away from chat the sidebar draws the chat page's own grouped list
  // (ThreadGroups: project headings, dots, ages) and a row opens its chat.
  // Pinned in depth by sidebar-chats-v1329.test.tsx.
  it("away from chat: the grouped chat list, each row opening its thread", async () => {
    render(<AppSidebar />);
    const list = await screen.findByTestId("sidebar-recent-chats");
    const row = await within(list).findByTitle("Quarterly numbers");
    expect(list.textContent).not.toContain("Previous 7 days");
    expect(within(list).getByTestId("thread-groups")).toBeTruthy();
    fireEvent.click(row);
    expect(nav.push).toHaveBeenCalledWith("/chat?thread=t1");
  });

  it("on chat, on a wide screen: the slot for Chat's own list is published", async () => {
    nav.pathname = "/chat";
    render(
      <>
        <AppSidebar />
        <SlotProbe />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("slot-probe").textContent).toBe("ij-sidebar-chat-slot"));
    expect(screen.queryByTestId("sidebar-recent-chats")).toBeNull();
  });

  // v1.329.0: the persistent rail (hidden on a phone) still publishes no
  // slot there; the phone's slot is the nav drawer's (sidebar-chats-v1329).
  it("on chat, on a phone: the hidden rail publishes no slot", async () => {
    nav.pathname = "/chat";
    setWide(false);
    render(
      <>
        <AppSidebar />
        <SlotProbe />
      </>,
    );
    await act(async () => {});
    expect(screen.getByTestId("slot-probe").textContent).toBe("none");
  });

  it("projects are listed (archived ones are not)", async () => {
    render(<AppSidebar />);
    const projects = await screen.findByTestId("sidebar-projects");
    expect(within(projects).getByRole("link", { name: "Q3 Bookkeeping" }).getAttribute("href")).toBe("/projects/p1");
    expect(projects.textContent).not.toContain("Gone");
  });
});

describe("the footer", () => {
  it("the status opens Everything › Status, and Help is a menu, not a fifth item", () => {
    render(<AppSidebar />);
    expect(screen.getByTestId("sidebar-status").getAttribute("href")).toBe("/everything#status");
    fireEvent.click(screen.getByRole("button", { name: "Help" }));
    const menu = screen.getByRole("menu", { name: "Help" });
    const hrefs = within(menu)
      .getAllByRole("menuitem")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/help");
    expect(hrefs).toContain("/updates");
    expect(within(screen.getByTestId("sidebar-nav")).queryByText(/Help/)).toBeNull();
  });
});

describe("the phone drawer and the title bar", () => {
  it("the drawer opens on ij:toggle-nav with the same four items and the theme row", async () => {
    render(<NavDrawer />);
    await act(async () => {
      window.dispatchEvent(new CustomEvent("ij:toggle-nav"));
    });
    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    const labels = within(within(drawer).getByTestId("sidebar-nav"))
      .getAllByRole("link")
      .map((a) => a.textContent);
    expect(labels).toEqual(["Build", "Projects", "Everything", "Settings"]);
    expect(within(drawer).getByRole("group", { name: /theme/i })).toBeTruthy();
  });

  it("the ☰ is a phone control (the sidebar is on screen from md up)", () => {
    render(<TitleBar />);
    const cls = screen.getByLabelText("Open navigation").getAttribute("class") ?? "";
    expect(cls.split(/\s+/)).toContain("md:hidden");
  });
});
