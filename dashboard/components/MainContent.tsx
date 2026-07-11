import type { ReactNode } from "react";

/**
 * The page-content wrapper. Every module shares the Build workspace's outer
 * frame — edge-to-edge against the left sidebar with tight padding — so the
 * whole dashboard has one spacing rhythm. The inner rhythm (section gaps, the
 * header block) is owned by PageShell (`space-y-6`) + PageHeader, which every
 * page already uses; this only governs the surrounding padding and width.
 */
export function MainContent({ children }: { children: ReactNode }) {
  return (
    <div
      id="main-content"
      tabIndex={-1}
      className="w-full max-w-none px-3 py-4 outline-none lg:px-4"
    >
      {children}
    </div>
  );
}
