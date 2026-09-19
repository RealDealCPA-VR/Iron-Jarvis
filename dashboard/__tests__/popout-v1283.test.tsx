/**
 * v1.283.0 — pop a module out into its own window.
 *
 * The desktop shell exposes `window.ironjarvis.popout`; the dashboard offers
 * the door in two places — the title bar (this page) and every nav row (that
 * module) — only inside the desktop shell and never inside a pop-out; a
 * popped-out window wears a badge and is titled by its module so two Iron
 * Jarvis windows never read as one app opened twice. Outside the shell (a
 * browser tab) nothing changes at all.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const routerState = vi.hoisted(() => ({ pathname: "/chat" }));
vi.mock("next/navigation", () => ({
  usePathname: () => routerState.pathname,
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    health: null,
    checking: false,
    refresh: () => {},
    provided: true,
  }),
}));
vi.mock("framer-motion", () => {
  const plain = (tag: string) =>
    function Plain({ children, ...rest }: { children?: React.ReactNode } & Record<string, unknown>) {
      const { layoutId: _l, initial: _i, animate: _a, exit: _e, transition: _t, ...dom } = rest as Record<string, unknown>;
      void _l; void _i; void _a; void _e; void _t;
      const Tag = tag as keyof React.JSX.IntrinsicElements;
      return <Tag {...(dom as object)}>{children}</Tag>;
    };
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    m: { div: plain("div"), aside: plain("aside"), span: plain("span") },
    motion: { div: plain("div") },
  };
});

import { TitleBar } from "@/components/TitleBar";
import { NavDrawer } from "@/components/Sidebar";
import { isPopoutWindow, popoutBridge } from "@/lib/desktopShell";
import { labelForPath } from "@/lib/nav";

type Bridge = { isPopout: boolean; path: string; open: ReturnType<typeof vi.fn> };

function installBridge(overrides: Partial<Bridge> = {}): Bridge {
  const bridge: Bridge = { isPopout: false, path: "", open: vi.fn(async () => ({ ok: true, path: "/chat" })), ...overrides };
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, popout: bridge };
  return bridge;
}

beforeEach(() => {
  routerState.pathname = "/chat";
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  delete document.documentElement.dataset.ijTitle;
  document.title = "Iron Jarvis";
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the bridge accessors", () => {
  it("answer null outside the desktop shell and the bridge inside it", () => {
    expect(popoutBridge()).toBeNull();
    expect(isPopoutWindow()).toBe(false);
    const b = installBridge();
    expect(popoutBridge()).toBe(b);
    expect(isPopoutWindow()).toBe(false);
    installBridge({ isPopout: true, path: "/terminals" });
    expect(isPopoutWindow()).toBe(true);
  });

  it("an older shell without the pop-out surface reads as no bridge", () => {
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true };
    expect(popoutBridge()).toBeNull();
  });
});

describe("the title bar door and badge", () => {
  it("offers 'open in a new window' only inside the shell, and opens THIS page", async () => {
    const { unmount } = render(<TitleBar />);
    await act(async () => {});
    expect(screen.queryByTestId("popout-open")).toBeNull();
    unmount();

    const b = installBridge();
    render(<TitleBar />);
    const door = await screen.findByTestId("popout-open");
    expect(door.getAttribute("aria-label")).toBe("Open Chat in a new window");
    fireEvent.click(door);
    expect(b.open).toHaveBeenCalledWith("/chat");
    expect(screen.queryByTestId("popout-badge")).toBeNull();
  });

  it("never offers the door for the Overview — that is the main window's job", async () => {
    installBridge();
    routerState.pathname = "/";
    render(<TitleBar />);
    await act(async () => {});
    expect(screen.queryByTestId("popout-open")).toBeNull();
  });

  it("inside a pop-out: no door, a badge, and the window is titled by its module", async () => {
    installBridge({ isPopout: true, path: "/chat" });
    render(<TitleBar />);
    const badge = await screen.findByTestId("popout-badge");
    expect(badge.textContent).toBe("own window");
    expect(screen.queryByTestId("popout-open")).toBeNull();
    expect(document.title).toBe("Chat — Iron Jarvis");
    expect(document.documentElement.dataset.ijTitle).toBe("Chat — Iron Jarvis");
  });

  it("a nested route titles the window by its parent module", async () => {
    installBridge({ isPopout: true, path: "/sessions/abc" });
    routerState.pathname = "/sessions/abc";
    render(<TitleBar />);
    await screen.findByTestId("popout-badge");
    expect(document.title).toBe("Sessions — Iron Jarvis");
  });
});

describe("the nav rows' doors", () => {
  async function openDrawer() {
    render(<NavDrawer />);
    await act(async () => {
      window.dispatchEvent(new CustomEvent("ij:toggle-nav"));
    });
    return screen.findByRole("dialog", { name: "Navigation" });
  }

  it("each module row carries a door that opens that module and closes the drawer", async () => {
    const b = installBridge();
    await openDrawer();
    const door = await screen.findByTestId("popout-row-terminals");
    expect(door.getAttribute("aria-label")).toBe("Open Build in a new window");
    // The Overview never pops out.
    expect(screen.queryByTestId("popout-row-")).toBeNull();
    fireEvent.click(door);
    expect(b.open).toHaveBeenCalledWith("/terminals");
    // The click did not navigate the drawer's link (the window opens elsewhere).
    expect(routerState.pathname).toBe("/chat");
  });

  it("no doors outside the shell, and none inside a pop-out", async () => {
    await openDrawer();
    expect(screen.queryByTestId("popout-row-terminals")).toBeNull();
    cleanup();
    installBridge({ isPopout: true, path: "/chat" });
    await openDrawer();
    await act(async () => {});
    expect(screen.queryByTestId("popout-row-terminals")).toBeNull();
  });
});

describe("labelForPath (the one rule the strip and the window title share)", () => {
  it("resolves nested routes to their module and answers null for the root and the unknown", () => {
    expect(labelForPath("/chat")).toBe("Chat");
    expect(labelForPath("/terminals")).toBe("Build");
    expect(labelForPath("/sessions/abc123")).toBe("Sessions");
    expect(labelForPath("/")).toBeNull();
    expect(labelForPath("/no-such-page")).toBeNull();
    expect(labelForPath(null)).toBeNull();
    // "/fleet" must not collide with "/filesearch" (prefix at a segment boundary).
    expect(labelForPath("/filesearch")).not.toBe(labelForPath("/fleet"));
  });
});
