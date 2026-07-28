"use client";

import { useEffect, useMemo } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, Search } from "lucide-react";
import { NAV_ENTRIES } from "@/lib/nav";

/** The desktop bridge surface this component uses (preload.js exposes it). */
interface DesktopBridge {
  setTitleBarOverlay?: (color: string, symbolColor: string) => Promise<boolean>;
}

/**
 * `"7 8 9"` → `"#070809"`. The theme palette stores colors as bare RGB
 * triplets (see globals.css `--ink-950: 7 8 9;`) so Tailwind can apply alpha;
 * Electron's setTitleBarOverlay wants hex. Exported for tests.
 */
export function tripletToHex(triplet: string): string | null {
  const parts = triplet.trim().split(/\s+/).map((n) => Number(n));
  if (parts.length !== 3 || parts.some((n) => !Number.isInteger(n) || n < 0 || n > 255))
    return null;
  return `#${parts.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * The app's own top strip (v1.111.0 frontier chrome).
 *
 * In the desktop app the Electron window is created with
 * `titleBarStyle: "hidden"` + `titleBarOverlay: { height: 40 }` (see
 * desktop/main.js), so the OS draws ONLY close/max/min — everything else in the
 * title bar is ours. In a plain browser at :8788 there is no overlay and this
 * same component degrades to an ordinary sticky-less header. One component, two
 * runtimes: every Electron-specific bit below is written so it collapses to a
 * no-op outside Electron (see the drag region and the clearance calc).
 *
 * The bar is 40px tall (`h-10`) ON PURPOSE: it must equal the
 * `titleBarOverlay.height` in desktop/main.js. If the two ever disagree the
 * native buttons sit above or below our row — a visibly broken window frame
 * that no amount of CSS on our side can fix. Change one, change the other.
 */
// NOTE: the return type is `React.JSX.Element`, not the bare `JSX.Element` —
// @types/react 19 (this repo's version) dropped the GLOBAL JSX namespace, so
// the unqualified name no longer resolves. Same type, current spelling.
export function TitleBar({ right }: { right?: React.ReactNode }): React.JSX.Element {
  const pathname = usePathname();

  // NATIVE-OVERLAY THEMING (v1.112.0). Windows paints the min/max/close strip
  // from titleBarOverlay colors frozen at window creation — it cannot see CSS,
  // so switching to the light Mark left black buttons on a white bar. Resolve
  // the bar's OWN palette (--ink-950 is exactly what this header renders on,
  // --zinc-300 is its readable foreground in every Mark) and push it through
  // the desktop bridge on mount and on every data-theme flip. In a browser the
  // bridge is absent and this effect is a no-op.
  useEffect(() => {
    const bridge = (window as unknown as { ironjarvis?: DesktopBridge }).ironjarvis;
    if (!bridge?.setTitleBarOverlay) return;
    const push = () => {
      const cs = getComputedStyle(document.documentElement);
      const bg = tripletToHex(cs.getPropertyValue("--ink-950"));
      const fg = tripletToHex(cs.getPropertyValue("--zinc-300"));
      if (bg && fg) void bridge.setTitleBarOverlay!(bg, fg);
    };
    push();
    // The ThemeSwitcher flips data-theme on <html>; observing the attribute
    // (rather than subscribing to the switcher) keeps the coupling one-way and
    // also catches the boot-time restore script in layout.tsx.
    const mo = new MutationObserver(push);
    mo.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => mo.disconnect();
  }, []);

  // WHERE AM I? With the whole nav hidden behind the hamburger, the rail is no
  // longer on screen to highlight the active page, so this label is the user's
  // only orientation cue. Longest-prefix match so nested routes (e.g.
  // /sessions/abc123) still resolve to their parent entry; "/" and unknown
  // paths deliberately render nothing rather than guessing.
  const pageLabel = useMemo(() => {
    if (!pathname || pathname === "/") return null;
    let best: (typeof NAV_ENTRIES)[number] | null = null;
    for (const entry of NAV_ENTRIES) {
      if (entry.href === "/") continue; // matches everything; never a useful label
      const hit = pathname === entry.href || pathname.startsWith(`${entry.href}/`);
      if (!hit) continue;
      if (!best || entry.href.length > best.href.length) best = entry;
    }
    return best?.label ?? null;
  }, [pathname]);

  // The nav drawer owns its own open/closed state and listens for this event.
  // The bar deliberately does NOT hold nav state: it renders above the drawer,
  // is mounted once, and would otherwise become a second source of truth that
  // drifts from the drawer's own Escape/route-change closes.
  const openNav = () => window.dispatchEvent(new CustomEvent("ij:toggle-nav"));

  // Same shape for search: the command palette owns the query, the results, and
  // the open state. We only knock on its door.
  const openPalette = () => window.dispatchEvent(new CustomEvent("ij:open-palette"));

  return (
    <header
      // DRAG REGION. `-webkit-app-region` is the ONLY way to tell a frameless
      // window which pixels move it; Tailwind cannot express it and TS does not
      // know the non-standard property, hence the inline style + cast. Outside
      // Electron the declaration is simply ignored.
      //
      // WINDOW-CONTROLS CLEARANCE. Under `titleBarOverlay` the renderer gets
      // env(titlebar-area-x / -width): the rectangle NOT covered by the native
      // buttons. `-x` is that rectangle's LEFT edge, `-width` its width, so the
      // OS owns `x` pixels on the left and `100% - x - width` on the right. We
      // reserve exactly those two gutters — hardcoding a pixel width would break
      // on different DPI/scale factors and on non-Windows overlays.
      //
      // On WINDOWS (the only platform we package) the buttons sit on the right,
      // so x = 0: the left padding is 0 and the right one reduces to
      // `100% - width` = precisely the close/max/min strip. Nothing is reserved
      // twice — the two terms are disjoint by construction. The left term is
      // kept because on macOS the traffic lights are on the LEFT (x ≈ 78px) and
      // a right-only formula would put our hamburger underneath them.
      //
      // In a normal browser neither env() exists, so the fallbacks kick in:
      // left = 0px, right = `calc(100% - 0px - 100%)` = 0. No reserved space, no
      // wasted margin, one component for both runtimes.
      //
      // The `100%` resolves against this header's containing block, so mount the
      // bar full-width (it is the window's top strip); nesting it inside a
      // narrower column would under-reserve the gutter.
      style={
        {
          WebkitAppRegion: "drag",
          // Wrapped in calc() on purpose: a BARE env() in a length slot is
          // dropped by stricter/older CSS parsers (jsdom's included), and the
          // wrapper costs nothing.
          paddingLeft: "calc(env(titlebar-area-x, 0px))",
          paddingRight:
            "calc(100% - env(titlebar-area-x, 0px) - env(titlebar-area-width, 100%))",
        } as React.CSSProperties
      }
      // `relative z-40` is LOAD-BEARING, not styling. backdrop-blur makes this
      // header a stacking context, and it is the FIRST child of the app column
      // — so at z:auto everything rendered after it (the whole page) paints on
      // top, and any dropdown inside the bar (model switcher, bell, theme
      // menu) is trapped underneath the content no matter what z-index the
      // dropdown itself claims. The old in-page header carried z-30 for the
      // same reason; 40 keeps the bar's flyouts above all page content while
      // staying under the drawer/palette overlays at z-50.
      className="relative z-40 flex h-10 shrink-0 select-none items-center border-b border-white/[0.06] bg-ink-950/70 backdrop-blur-xl"
    >
      <div className="flex h-full w-full items-center gap-2 px-2">
        {/* Left cluster: nav trigger · brand · you-are-here. */}
        <button
          type="button"
          onClick={openNav}
          aria-label="Open navigation"
          title="Open navigation"
          // NO-DRAG on every interactive child. This is the classic frameless
          // bug in both directions: forget `drag` on the root and the window
          // becomes immovable; forget `no-drag` here and the button is DEAD —
          // clicks are swallowed by the drag region before React sees them.
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
        >
          <Menu size={16} strokeWidth={2} />
        </button>

        {/* Brand. Intentionally self-contained (a dot, not Sidebar's private
            ArcMark): the rail's mark is a 36px reactor tuned for a 64px header
            and would dominate a 40px strip — and importing it would couple this
            component to the rail's internals. */}
        <Link
          href="/"
          title="Iron Jarvis — Overview"
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
          className="flex shrink-0 items-center gap-2 rounded-lg px-1 py-1 transition-colors hover:bg-white/[0.04]"
        >
          <span className="relative grid h-3.5 w-3.5 place-items-center">
            <span className="absolute inset-0 rounded-full bg-accent/25 blur-[3px]" />
            <span className="relative h-2 w-2 rounded-full bg-accent shadow-[0_0_6px_rgb(var(--accent-rgb)/0.7)]" />
          </span>
          <span className="text-[13px] font-medium tracking-tight text-zinc-100">
            Iron Jarvis
          </span>
        </Link>

        {pageLabel && (
          <>
            <span aria-hidden className="text-zinc-700">
              /
            </span>
            <span className="truncate text-[13px] text-zinc-500">{pageLabel}</span>
          </>
        )}

        {/* SEARCH — the front door. */}
        <div className="flex min-w-0 flex-1 justify-center px-2">
          <button
            type="button"
            onClick={openPalette}
            aria-label="Search Iron Jarvis (Ctrl K)"
            title="Search — Ctrl K"
            // It LOOKS like a field but is a BUTTON on purpose: the command
            // palette already owns the query, the debounce, and the results.
            // A live input here would fork that state into two places (type in
            // the bar, palette opens empty — or worse, both filter separately).
            // One results surface, one input.
            //
            // And it is VISIBLE on purpose: this month proved that a shortcut
            // with no on-screen affordance reads as "the feature does not
            // exist". The box is the discoverable front door; the chip TEACHES
            // the shortcut so the box eventually stops being needed.
            style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
            className="flex h-7 w-full min-w-[200px] max-w-sm items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 text-left text-zinc-500 transition-colors hover:border-white/20 hover:bg-white/[0.06] hover:text-zinc-300"
          >
            <Search size={13} strokeWidth={2} className="shrink-0" />
            <span className="truncate text-[12px]">Search</span>
            <kbd className="ml-auto shrink-0 rounded border border-white/10 bg-white/[0.03] px-1.5 py-0.5 font-sans text-[10px] text-zinc-500">
              Ctrl K
            </kbd>
          </button>
        </div>

        {/* Right slot: status/notification chrome supplied by the layout. Also
            no-drag — whatever the caller puts here is interactive by nature. */}
        {right && (
          <div
            style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
            className="flex shrink-0 items-center gap-2"
          >
            {right}
          </div>
        )}
      </div>
    </header>
  );
}
