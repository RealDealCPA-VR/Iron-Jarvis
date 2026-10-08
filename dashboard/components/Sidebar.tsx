"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, m } from "framer-motion"; // v1.250.0 (S-08)
import { X } from "lucide-react";
import { ArcMark, SidebarBody } from "@/components/AppSidebar";

// The persistent sidebar (and the body it shares with this drawer) lives in
// components/AppSidebar.tsx since the calm UI redesign S7. This file is the
// phone drawer, loaded on demand by components/Overlays.tsx so framer stays out
// of the first-paint chunk (v1.250.0, S-08).

/** The phone drawer: the same body, opened by the title bar's ☰
 *  (`ij:toggle-nav`), closed by Escape, the backdrop or any navigation. */
export function NavDrawer() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    const onToggle = () => setOpen((o) => !o);
    window.addEventListener("ij:toggle-nav", onToggle);
    return () => window.removeEventListener("ij:toggle-nav", onToggle);
  }, []);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <m.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
          />
          <m.aside
            key="drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            initial={{ x: "-100%" }}
            animate={{ x: 0 }}
            exit={{ x: "-100%" }}
            transition={{ type: "spring", stiffness: 360, damping: 38 }}
            className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r border-white/[0.06] bg-ink-900/95 backdrop-blur-xl"
          >
            <div className="flex shrink-0 items-center justify-between px-3 py-3">
              <Link href="/" className="flex items-center gap-2 text-zinc-100" onClick={() => setOpen(false)}>
                <ArcMark />
                <span className="text-[15px] font-semibold tracking-tight">Iron Jarvis</span>
              </Link>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close navigation"
                className="grid h-8 w-8 place-items-center rounded-lg border border-white/10 text-zinc-400 transition-colors hover:text-zinc-100"
              >
                <X size={16} />
              </button>
            </div>
            <SidebarBody variant="drawer" onNavigate={() => setOpen(false)} />
          </m.aside>
        </>
      )}
    </AnimatePresence>
  );
}
