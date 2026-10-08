"use client";

/**
 * ONE MODEL SELECTOR (calm UI redesign S9, AUDIT Q3).
 *
 * The title bar's model chip is retired: the model is chosen in the composer.
 * "Switch model" (the palette), "Choose a model" (the demo strip) and the like
 * still dispatch `ij:open-switcher`. On the chat surface the chat page opens
 * its own menu; anywhere else this bridge takes the user to the home chat with
 * the menu open (`/?model=1`).
 */

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { CHAT_PATHS } from "@/components/AppSidebar";

export const OPEN_SWITCHER_EVENT = "ij:open-switcher";

export function ModelMenuBridge() {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  useEffect(() => {
    const onOpen = () => {
      if (!CHAT_PATHS.includes(pathname)) router.push("/?model=1");
    };
    window.addEventListener(OPEN_SWITCHER_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_SWITCHER_EVENT, onOpen);
  }, [pathname, router]);
  return null;
}
