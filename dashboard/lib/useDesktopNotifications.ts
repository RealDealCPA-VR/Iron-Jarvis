"use client";

import { useCallback, useEffect, useState } from "react";

/* -------------------------------------------------------------------------- */
/*  Desktop (Web) notifications                                                */
/* -------------------------------------------------------------------------- */

export type NotifyPermission = "default" | "granted" | "denied" | "unsupported";

/** True only in a browser that actually exposes the Notification API. */
function supported(): boolean {
  return typeof window !== "undefined" && typeof Notification !== "undefined";
}

export interface UseDesktopNotifications {
  /** Whether the browser exposes the Notification API at all. */
  supported: boolean;
  /** Current permission: "default" until asked, then granted/denied. */
  permission: NotifyPermission;
  /**
   * Lazily ask the user for permission (once). Resolves to the resulting
   * state. No-ops (resolving to the current value) when unsupported or already
   * decided, so it is safe to call on first use behind existing UI.
   */
  requestPermission: () => Promise<NotifyPermission>;
  /**
   * Fire a desktop notification. No-ops silently when unsupported or not
   * granted. Clicking focuses this window and runs `onClick`.
   */
  notify: (title: string, body?: string, onClick?: () => void) => void;
}

/**
 * Small, dependency-free, SSR-safe wrapper around the Web Notification API.
 *
 * Everything degrades to a no-op when the API is missing or permission is
 * denied, so callers never have to branch on capability — they just call
 * `notify()` and optionally surface `permission`/`supported` for affordances.
 */
export function useDesktopNotifications(): UseDesktopNotifications {
  const [permission, setPermission] = useState<NotifyPermission>("default");

  // Sync the real permission state on mount (guarded for SSR / unsupported).
  useEffect(() => {
    if (!supported()) {
      setPermission("unsupported");
      return;
    }
    setPermission(Notification.permission as NotifyPermission);
  }, []);

  const requestPermission = useCallback(async (): Promise<NotifyPermission> => {
    if (!supported()) return "unsupported";
    if (Notification.permission !== "default") {
      const cur = Notification.permission as NotifyPermission;
      setPermission(cur);
      return cur;
    }
    try {
      const result = (await Notification.requestPermission()) as NotifyPermission;
      setPermission(result);
      return result;
    } catch {
      // Some browsers reject if not called from a user gesture; treat as default.
      return "default";
    }
  }, []);

  const notify = useCallback(
    (title: string, body?: string, onClick?: () => void) => {
      if (!supported() || Notification.permission !== "granted") return;
      try {
        const n = new Notification(title, {
          body,
          icon: "/favicon.ico",
          tag: "iron-jarvis",
        });
        n.onclick = () => {
          try {
            window.focus();
          } catch {
            /* ignore */
          }
          onClick?.();
          n.close();
        };
      } catch {
        /* notification construction can throw on some platforms — ignore */
      }
    },
    [],
  );

  return { supported: permission !== "unsupported", permission, requestPermission, notify };
}
