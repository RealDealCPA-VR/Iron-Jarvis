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
    // This used to assert on /projects, with the note "Projects lives inside
    // Chat". That was true when Projects was reachable only through the chat
    // panel — but the page also existed at /projects with no rail row, no
    // search entry and no tile, so v1.151.1 put it in the catalogue (see
    // lib/nav.ts). The BEHAVIOUR under test is unchanged and still worth
    // pinning; it just needs a route that is genuinely unmapped.
    const { container } = renderAt("/not-a-real-page/abc123");
    expect(screen.queryByText("Chat")).not.toBeInTheDocument();
    // Brand only — no separator, no label.
    expect(container.textContent).not.toContain("/");
  });

  it("labels a project's detail route from its catalogue entry", () => {
    // Longest-prefix matching means /projects/<id> now reads "Projects" —
    // which is the point of adding it: a deep route inside a module should say
    // which module you are in.
    renderAt("/projects/abc123");
    expect(screen.getByText("Projects")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// v1.112.0 — the native window-controls strip follows the theme.
//
// REPORTED: "the minimize/maximize/close appear black even with a white theme."
// titleBarOverlay colors are frozen at window creation; Windows paints the
// buttons and never sees CSS. The fix: TitleBar resolves its own palette vars
// and pushes them through the desktop bridge on mount and on every data-theme
// flip. These tests drive that loop with a fake bridge + stubbed vars.
// ---------------------------------------------------------------------------

import { tripletToHex } from "@/components/TitleBar";

describe("tripletToHex — the palette-var converter", () => {
  it("converts the dark page triplet", () => {
    expect(tripletToHex("7 8 9")).toBe("#070809");
  });
  it("converts the Mark 8 light page triplet", () => {
    expect(tripletToHex("235 240 248")).toBe("#ebf0f8");
  });
  it("tolerates the leading space getPropertyValue returns", () => {
    expect(tripletToHex(" 7 8 9 ")).toBe("#070809");
  });
  it.each([["", null], ["not a color", null], ["1 2", null], ["1 2 3 4", null],
           ["256 0 0", null], ["-1 0 0", null], ["1.5 2 3", null]])(
    "rejects %j (never send Windows garbage)", (input, want) => {
      expect(tripletToHex(input as string)).toBe(want);
    },
  );
});

describe("native-overlay theming", () => {
  function stubVars(vars: Record<string, string>) {
    return vi.spyOn(window, "getComputedStyle").mockImplementation(
      () =>
        ({
          getPropertyValue: (name: string) => vars[name] ?? "",
        }) as unknown as CSSStyleDeclaration,
    );
  }
  afterEach(() => {
    vi.restoreAllMocks();
    delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
    document.documentElement.removeAttribute("data-theme");
  });

  it("pushes the resolved colors through the bridge on mount", () => {
    const setTitleBarOverlay = vi.fn().mockResolvedValue(true);
    (window as unknown as { ironjarvis: object }).ironjarvis = { setTitleBarOverlay };
    stubVars({ "--ink-950": "7 8 9", "--zinc-300": "203 208 216" });
    render(<TitleBar />);
    expect(setTitleBarOverlay).toHaveBeenCalledWith("#070809", "#cbd0d8");
  });

  it("pushes again when the theme flips (the reported bug)", async () => {
    const setTitleBarOverlay = vi.fn().mockResolvedValue(true);
    (window as unknown as { ironjarvis: object }).ironjarvis = { setTitleBarOverlay };
    const spy = stubVars({ "--ink-950": "7 8 9", "--zinc-300": "203 208 216" });
    render(<TitleBar />);
    // ThemeSwitcher flips data-theme; the palette now resolves LIGHT.
    spy.mockImplementation(
      () =>
        ({
          getPropertyValue: (name: string) =>
            ({ "--ink-950": "235 240 248", "--zinc-300": "47 57 74" })[name] ?? "",
        }) as unknown as CSSStyleDeclaration,
    );
    document.documentElement.setAttribute("data-theme", "mark8");
    // MutationObserver callbacks are microtask-scheduled.
    await new Promise((r) => setTimeout(r, 0));
    expect(setTitleBarOverlay).toHaveBeenLastCalledWith("#ebf0f8", "#2f394a");
    expect(setTitleBarOverlay).toHaveBeenCalledTimes(2);
  });

  it("is a silent no-op in a plain browser (no bridge)", () => {
    const spy = stubVars({ "--ink-950": "7 8 9", "--zinc-300": "203 208 216" });
    expect(() => render(<TitleBar />)).not.toThrow();
    // Without the bridge the effect must return before ever reading styles.
    expect(spy).not.toHaveBeenCalled();
  });

  it("sends nothing when a var is missing (never half-theme the strip)", () => {
    const setTitleBarOverlay = vi.fn();
    (window as unknown as { ironjarvis: object }).ironjarvis = { setTitleBarOverlay };
    stubVars({ "--ink-950": "7 8 9" }); // --zinc-300 absent
    render(<TitleBar />);
    expect(setTitleBarOverlay).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// v1.112.1 — flyouts inside the bar must paint ABOVE page content.
//
// REPORTED: "the drop down options appear behind whatever is on screen." The
// bar's backdrop-blur makes it a stacking context, and as the app column's
// FIRST child it loses the paint order to everything after it at z:auto — so
// any dropdown inside (model switcher, bell) was trapped under the page no
// matter its own z-index. jsdom cannot see paint order (the real verification
// was elementFromPoint in a browser: 4/4 probe points inside the open panel);
// this pin just stops the load-bearing classes from being "cleaned up".
// ---------------------------------------------------------------------------

describe("stacking context", () => {
  it("the bar carries relative z-40 so its flyouts beat page content", () => {
    render(<TitleBar />);
    const header = document.querySelector("header");
    expect(header?.className).toContain("relative");
    expect(header?.className).toContain("z-40");
  });
});
