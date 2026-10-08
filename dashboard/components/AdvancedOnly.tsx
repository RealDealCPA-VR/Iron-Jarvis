"use client";

/**
 * Renders its children only in Advanced mode (v1.319.0). Used for title-bar
 * chrome a first-time user does not need on every screen — the theme dots,
 * which Simple mode keeps in the menu drawer, Settings → Appearance and
 * Ctrl K ("Theme: …"). Seeded Simple, so the server render and the first
 * client render agree; it appears after mount in Advanced.
 */

import type { ReactNode } from "react";
import { useAdvancedMode } from "@/lib/uiMode";

export function AdvancedOnly({ children }: { children: ReactNode }) {
  const [advanced] = useAdvancedMode();
  return advanced ? <>{children}</> : null;
}
