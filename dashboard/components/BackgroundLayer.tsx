"use client";

/**
 * THE ANIMATED BACKGROUND BEHIND THE APP (v1.410.0).
 *
 * When the theme in use is one of the user's palettes with a background
 * (lib/themePalette `background`), this draws that background — the file the
 * daemon downloaded and verified — fixed behind everything, in every window
 * (pop-outs render the same layout).
 *
 * SEALED OFF. The iframe is `sandbox="allow-scripts"` and NEVER
 * `allow-same-origin`: the page runs in an opaque origin, so it cannot read
 * this window's storage (where the daemon's token lives), its cookies or its
 * DOM. The daemon serves it under a CSP with `default-src 'none'`, so it
 * cannot reach the network or the daemon either. It takes no pointer events,
 * no focus and is hidden from screen readers.
 *
 * READABLE. The iframe is drawn at BACKGROUND_PEAK opacity and a scrim of the
 * page colour (SCRIM_ALPHA) lies over it — the exact figures the theme
 * maker's readability check measures text against.
 *
 * LIGHT ON THE PC (the v1.333.0 rule). The animation is told to stop while the
 * window is hidden or minimised, and to draw one still frame when the user
 * chose Still, when Windows asks for less motion, and while on battery power.
 *
 * MISSING IS FINE. A file that was deleted, never downloaded, or no longer
 * matches the catalog shows nothing: the iframe stays invisible until the
 * page says it drew (`ij-bg-ready`), so the plain theme simply stays. A file
 * that is out of date after an update (`outdated`) is downloaded again once.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useDaemon } from "@/lib/daemon";
import {
  BACKGROUNDS_EVENT,
  backgroundHash,
  backgroundViewUrl,
  downloadBackground,
  listBackgrounds,
  type BackgroundState,
} from "@/lib/backgrounds";
import { isCustomTheme } from "@/lib/theme";
import {
  BACKGROUND_PEAK,
  PALETTES_EVENT,
  PALETTES_KEY,
  SCRIM_ALPHA,
  loadPalettes,
  type CustomPalette,
} from "@/lib/themePalette";

/** What the page is told (it is in an opaque origin, so the messages carry
 *  nothing but these two booleans). */
export function backgroundMessages(still: boolean, run: boolean): { type: "ij-bg"; still?: boolean; run?: boolean }[] {
  return [
    { type: "ij-bg", still },
    { type: "ij-bg", run },
  ];
}

/** The iframe's attributes — pinned by test (no allow-same-origin, ever). */
export const BACKGROUND_SANDBOX = "allow-scripts";

function useThemeAttrs(): { theme: string; scheme: "dark" | "light" } {
  const [attrs, setAttrs] = useState<{ theme: string; scheme: "dark" | "light" }>({
    theme: "",
    scheme: "dark",
  });
  useEffect(() => {
    const html = document.documentElement;
    const sync = () =>
      setAttrs((prev) => {
        const theme = html.dataset.theme ?? "";
        const scheme = html.dataset.scheme === "light" ? "light" : "dark";
        return prev.theme === theme && prev.scheme === scheme ? prev : { theme, scheme };
      });
    sync();
    const mo = new MutationObserver(sync);
    mo.observe(html, { attributes: true, attributeFilter: ["data-theme", "data-scheme"] });
    return () => mo.disconnect();
  }, []);
  return attrs;
}

function usePalette(id: string): CustomPalette | null {
  const [palette, setPalette] = useState<CustomPalette | null>(null);
  useEffect(() => {
    if (!isCustomTheme(id)) {
      setPalette(null);
      return;
    }
    const sync = () => setPalette(loadPalettes().find((p) => p.id === id) ?? null);
    sync();
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === PALETTES_KEY) sync();
    };
    window.addEventListener(PALETTES_EVENT, sync);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(PALETTES_EVENT, sync);
      window.removeEventListener("storage", onStorage);
    };
  }, [id]);
  return palette;
}

function useMedia(query: string): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia(query);
    } catch {
      return;
    }
    const sync = () => setOn(!!mq.matches);
    sync();
    mq.addEventListener?.("change", sync);
    return () => mq.removeEventListener?.("change", sync);
  }, [query]);
  return on;
}

interface BatteryLike {
  charging: boolean;
  addEventListener?: (t: string, f: () => void) => void;
  removeEventListener?: (t: string, f: () => void) => void;
}

/** True while running on battery power (a desktop PC: never). */
function useOnBattery(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const nav = navigator as Navigator & { getBattery?: () => Promise<BatteryLike> };
    if (typeof nav.getBattery !== "function") return;
    let battery: BatteryLike | null = null;
    let alive = true;
    const sync = () => battery && alive && setOn(!battery.charging);
    nav
      .getBattery()
      .then((b) => {
        battery = b;
        sync();
        b.addEventListener?.("chargingchange", sync);
      })
      .catch(() => {});
    return () => {
      alive = false;
      battery?.removeEventListener?.("chargingchange", sync);
    };
  }, []);
  return on;
}

function useHidden(): boolean {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    const sync = () => setHidden(document.visibilityState === "hidden");
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  return hidden;
}

export function BackgroundLayer() {
  const { theme, scheme } = useThemeAttrs();
  const palette = usePalette(theme);
  const choice = palette?.background ?? null;
  const id = choice?.id ?? "";
  const { online } = useDaemon();

  const [state, setState] = useState<BackgroundState | "unknown">("unknown");
  const refreshed = useRef<Set<string>>(new Set());

  // What the daemon has on this PC for this background.
  useEffect(() => {
    if (!id) {
      setState("unknown");
      return;
    }
    let alive = true;
    const read = () =>
      listBackgrounds()
        .then((list) => {
          if (!alive) return;
          const row = list.backgrounds.find((b) => b.id === id);
          const next = row?.state ?? "absent";
          setState(next);
          // Updated after an app update: fetch the new file once, on use.
          if (next === "outdated" && !refreshed.current.has(id)) {
            refreshed.current.add(id);
            downloadBackground(id).catch(() => {});
          }
        })
        .catch(() => alive && setState("unknown"));
    read();
    window.addEventListener(BACKGROUNDS_EVENT, read);
    return () => {
      alive = false;
      window.removeEventListener(BACKGROUNDS_EVENT, read);
    };
  }, [id, online]);

  const hidden = useHidden();
  const reduced = useMedia("(prefers-reduced-motion: reduce)");
  const onBattery = useOnBattery();
  const still = choice?.motion === "still" || reduced || onBattery;
  const run = !hidden;

  const hash = palette ? backgroundHash(palette.vars[scheme], scheme) : "";
  const src = id && state === "ready" ? backgroundViewUrl(id, hash) : null;

  const frame = useRef<HTMLIFrameElement | null>(null);
  const [drawn, setDrawn] = useState(false);
  const hasSrc = src !== null;
  useEffect(() => setDrawn(false), [id, hasSrc]);

  // The page says when it has drawn; until then nothing (no scrim) shows.
  useEffect(() => {
    if (!src) return;
    const onMessage = (e: MessageEvent) => {
      if (!frame.current || e.source !== frame.current.contentWindow) return;
      const m = e.data as { type?: unknown } | null;
      if (m && m.type === "ij-bg-ready") setDrawn(true);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [src]);

  // While a background shows, the app's own drifting glow (globals.css
  // body::before) is covered by it: stop drawing it, so a Still background
  // costs nothing while it sits there.
  useEffect(() => {
    if (!drawn || !src) return;
    const html = document.documentElement;
    html.dataset.background = "on";
    return () => {
      delete html.dataset.background;
    };
  }, [drawn, src]);

  const tell = useMemo(
    () => () => {
      const w = frame.current?.contentWindow;
      if (!w) return;
      // The page is in an opaque origin, so "*" is the only target.
      for (const m of backgroundMessages(still, run)) w.postMessage(m, "*");
    },
    [still, run],
  );
  useEffect(() => {
    tell();
  }, [tell, drawn]);

  if (!src) return null;
  return (
    <div
      aria-hidden="true"
      data-testid="app-background"
      data-background={id}
      data-shown={drawn ? "yes" : "no"}
      data-motion={still ? "still" : "animated"}
      data-running={run ? "yes" : "no"}
      className="pointer-events-none fixed inset-0 -z-[1] overflow-hidden"
    >
      <iframe
        key={id}
        ref={frame}
        data-testid="app-background-frame"
        src={src}
        sandbox={BACKGROUND_SANDBOX}
        referrerPolicy="no-referrer"
        title="Background"
        tabIndex={-1}
        aria-hidden="true"
        onLoad={tell}
        className="absolute inset-0 h-full w-full border-0"
        style={{
          opacity: drawn ? BACKGROUND_PEAK : 0,
          transition: "opacity 600ms ease",
          pointerEvents: "none",
          colorScheme: "normal",
        }}
      />
      {drawn && (
        <div
          data-testid="app-background-scrim"
          className="absolute inset-0"
          style={{ background: `rgb(var(--ink-950) / ${SCRIM_ALPHA[scheme]})` }}
        />
      )}
    </div>
  );
}
