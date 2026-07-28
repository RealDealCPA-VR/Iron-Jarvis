import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * TitleBar — the custom strip that replaces the OS title bar in the frameless
 * Electron window.
 *
 * What is pinned here is exactly what BREAKS SILENTLY elsewhere:
 *  - the two event names, because the nav drawer and the command palette are
 *    the only listeners and a typo'd name fails as "the button does nothing";
 *  - the no-drag wrapper around the `right` slot, because a missing one makes
 *    every injected control DEAD in the desktop app while looking perfect in a
 *    browser (and in this jsdom suite, hence the explicit style assertions);
 *  - the page-label prefix match, which must resolve nested routes to their
 *    parent WITHOUT letting "/fleet" collide with "/filesearch".
 */

// usePathname is a client-router hook with no provider in jsdom. A hoisted box
// lets each test choose the route before rendering.
const routerState = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => routerState.pathname }));

// next/link reaches for the App Router context, which does not exist in a bare
// render. Swap in a plain anchor that forwards props, so the brand's own
// styling (incl. no-drag) is still what lands on the DOM node.
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { TitleBar } from "@/components/TitleBar";

/** The `-webkit-app-region` value React actually put on the node. */
function appRegion(el: HTMLElement | null | undefined): string | undefined {
  if (!el) return undefined;
  return (el.style as unknown as Record<string, string | undefined>).WebkitAppRegion;
}

function renderAt(pathname: string, right?: React.ReactNode) {
  routerState.pathname = pathname;
  return render(<TitleBar right={right} />);
}

afterEach(() => {
  cleanup();
  routerState.pathname = "/";
});

describe("TitleBar — chrome", () => {
  it("renders the brand", () => {
    renderAt("/");
    expect(screen.getByText("Iron Jarvis")).toBeInTheDocument();
  });

  it("is exactly 40px tall (h-10) to match titleBarOverlay.height in desktop/main.js", () => {
    const { container } = renderAt("/");
    const header = container.querySelector("header") as HTMLElement;
    expect(header.className).toContain("h-10");
  });

  it("makes the whole bar draggable and reserves the native-controls gutter", () => {
    const { container } = renderAt("/");
    const header = container.querySelector("header") as HTMLElement;
    expect(appRegion(header)).toBe("drag");
    // Substring, not equality: jsdom's cssstyle re-serializes calc() and
    // scrambles env() fallback args (`env(0px * , * titlebar-area-x)`). The
    // real browsers/Electron keep the authored string; all we can pin here is
    // that BOTH env vars are consulted, right gutter and left.
    expect(header.style.paddingRight).toContain("titlebar-area-x");
    expect(header.style.paddingRight).toContain("titlebar-area-width");
    expect(header.style.paddingLeft).toContain("titlebar-area-x");
  });
});

describe("TitleBar — the two events it owns", () => {
  it("hamburger is labelled and dispatches ij:toggle-nav on window", () => {
    renderAt("/");
    const button = screen.getByLabelText("Open navigation");
    expect(appRegion(button)).toBe("no-drag");

    const seen = vi.fn();
    window.addEventListener("ij:toggle-nav", seen);
    fireEvent.click(button);
    window.removeEventListener("ij:toggle-nav", seen);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("search dispatches ij:open-palette on window and teaches the shortcut", () => {
    renderAt("/");
    const button = screen.getByRole("button", { name: /search/i });
    expect(appRegion(button)).toBe("no-drag");
    expect(button.textContent).toMatch(/Ctrl/);
    expect(button.textContent).toMatch(/K/);

    const seen = vi.fn();
    window.addEventListener("ij:open-palette", seen);
    fireEvent.click(button);
    window.removeEventListener("ij:open-palette", seen);
    expect(seen).toHaveBeenCalledTimes(1);
  });
});

describe("TitleBar — the right slot", () => {
  it("wraps injected content in a no-drag container", () => {
    renderAt("/", <button data-testid="injected">Bell</button>);
    const injected = screen.getByTestId("injected");
    // The WRAPPER owns no-drag, not the child: anything the layout injects is
    // clickable in the desktop app without knowing this rule exists.
    expect(appRegion(injected.parentElement)).toBe("no-drag");
  });

  it("renders nothing extra when no right slot is supplied", () => {
    const { container } = renderAt("/");
    const wrappers = Array.from(container.querySelectorAll("div")).filter(
      (d) => appRegion(d as HTMLElement) === "no-drag"
    );
    expect(wrappers).toHaveLength(0);
  });
});

describe("TitleBar — page label (longest-prefix over NAV_ENTRIES)", () => {
  it("shows the label for an exact route", () => {
    renderAt("/connections");
    expect(screen.getByText("Connections")).toBeInTheDocument();
  });

  it("resolves a nested route to its parent entry", () => {
    renderAt("/sessions/abc123");
    expect(screen.getByText("Sessions")).toBeInTheDocument();
  });

  it("does not match a sibling that merely shares a prefix string", () => {
    // "/fleet" must not be answered by "/filesearch" (or vice versa).
    renderAt("/fleet");
    expect(screen.getByText("Local fleet")).toBeInTheDocument();
    expect(screen.queryByText("File Search")).not.toBeInTheDocument();
  });

  it("renders no label on / (the catch-all entry is skipped)", () => {
    renderAt("/");
    expect(screen.queryByText("Overview")).not.toBeInTheDocument();
  });

  it("renders no label for a route with no nav entry", () => {
    // /projects has no NAV_ENTRIES row (Projects lives inside Chat), so the bar
    // stays silent rather than guessing — acceptable and deliberate.
    const { container } = renderAt("/projects/abc123");
    expect(screen.queryByText("Chat")).not.toBeInTheDocument();
    // Brand only — no separator, no label.
    expect(container.textContent).not.toContain("/");
  });
});
