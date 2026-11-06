"use client";

/**
 * The desktop app's GLOBAL hotkeys on the Help page (v1.439.0, chat-first
 * Wave 6). Before this the keys were shown nowhere in the app.
 *
 * The keys are read from the desktop app itself (`shell.getState()`, the same
 * `hotkeyState` the tray reads): the window key is a LADDER (Ctrl+Shift+J,
 * then Ctrl+Alt+J) and another app can hold every rung, so a hard-coded key
 * would be a claim the OS may already have refused (the v1.229.0 lesson).
 * In a browser tab there is no desktop app to ask, and the page says so.
 */

import { useEffect, useState } from "react";
import { hotkeysOf, isDesktopShell, shellBridge, type ShellHotkeys } from "@/lib/desktopShell";

/** What one line says: the key, or why there is none. */
export interface HotkeyLine {
  id: "window" | "spotlight";
  what: string;
  key: string | null;
  note: string;
}

/** Words for the two keys (pure; the tests read it). */
export function hotkeyLines(h: ShellHotkeys): HotkeyLine[] {
  const line = (
    id: "window" | "spotlight",
    what: string,
    trayItem: string,
  ): HotkeyLine => {
    const key = h[id];
    const preferred = h.preferred[id];
    if (key) {
      return {
        id,
        what,
        key,
        note:
          preferred && preferred !== key
            ? `${preferred} is taken by another app, so Iron Jarvis uses this one.`
            : "",
      };
    }
    return {
      id,
      what,
      key: null,
      note: `No key right now: another app holds ${preferred ?? "it"}${
        id === "window" ? " and the fallback" : ""
      }. Iron Jarvis tries again later; the tray's “${trayItem}” always works.`,
    };
  };
  return [
    line("window", "Bring up Iron Jarvis from anywhere", "Open Iron Jarvis"),
    line("spotlight", "Quick task, from any app", "Quick task…"),
  ];
}

export const BROWSER_HOTKEYS_WORDS =
  "These keys belong to the Iron Jarvis desktop app. In a browser tab there are none; open the desktop app to use them.";
export const UNREAD_HOTKEYS_WORDS =
  "The desktop app did not say which keys it holds. Its tray menu shows them next to Open Iron Jarvis and Quick task.";

type Read = { kind: "browser" } | { kind: "reading" } | { kind: "unread" } | { kind: "ok"; keys: ShellHotkeys };

export function GlobalHotkeys() {
  const [read, setRead] = useState<Read>({ kind: "reading" });

  useEffect(() => {
    let live = true;
    const fn = shellBridge()?.getState;
    if (typeof fn !== "function") {
      // An older desktop app with no shell bridge is still the desktop app.
      setRead(isDesktopShell() ? { kind: "unread" } : { kind: "browser" });
      return;
    }
    fn()
      .then((state) => {
        if (!live) return;
        const keys = hotkeysOf(state);
        setRead(keys ? { kind: "ok", keys } : { kind: "unread" });
      })
      .catch(() => {
        if (live) setRead({ kind: "unread" });
      });
    return () => {
      live = false;
    };
  }, []);

  if (read.kind === "reading") return null;
  if (read.kind === "browser" || read.kind === "unread") {
    return (
      <p data-testid="help-hotkeys-none" className="text-[13px] leading-relaxed text-zinc-500">
        {read.kind === "browser" ? BROWSER_HOTKEYS_WORDS : UNREAD_HOTKEYS_WORDS}
      </p>
    );
  }
  return (
    <ul data-testid="help-hotkeys" className="space-y-2">
      {hotkeyLines(read.keys).map((l) => (
        <li key={l.id} data-testid={`help-hotkey-${l.id}`} className="text-[13px] leading-relaxed">
          <span className="text-zinc-200">{l.what}</span>
          {l.key ? (
            <>
              {": "}
              <kbd className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
                {l.key}
              </kbd>
            </>
          ) : null}
          {l.note ? <span className="block text-zinc-500">{l.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}
