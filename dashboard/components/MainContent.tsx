"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

// Routes that want the FULL width of the content area (bordering the sidebar)
// with minimal padding — the Build workspace needs every pixel for its canvas.
const WIDE_ROUTES = ["/terminals"];

/**
 * The page-content wrapper. Most pages are centered at a comfortable reading
 * width (max-w-7xl); a few (the Build workspace) go edge-to-edge so their canvas
 * borders the left sidebar. Kept as a small client component so `layout.tsx`
 * (a server component) can stay static.
 */
export function MainContent({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "";
  const wide = WIDE_ROUTES.some(
    (r) => pathname === r || pathname.startsWith(`${r}/`),
  );
  return (
    <div
      id="main-content"
      tabIndex={-1}
      className={
        wide
          ? "w-full max-w-none px-3 py-4 outline-none lg:px-4"
          : "mx-auto w-full max-w-7xl px-6 py-8 outline-none lg:px-10"
      }
    >
      {children}
    </div>
  );
}
