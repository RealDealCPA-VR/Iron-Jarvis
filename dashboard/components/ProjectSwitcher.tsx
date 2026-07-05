"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, FolderKanban, Plus } from "lucide-react";
import { get, post } from "@/lib/api";

/**
 * The CONTEXT SPINE, surfaced. The daemon already threads the active project
 * into every session (orchestrator defaults project_id to it), but until now
 * the only place to see or change it was the buried Projects page. This
 * switcher lives in the sidebar so "what am I working on" is always one glance
 * — and one click — away, on every page.
 */

interface ProjectRow {
  id: string;
  name: string;
  active?: boolean;
}

export function ProjectSwitcher() {
  const [projects, setProjects] = useState<ProjectRow[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    get<{ projects: ProjectRow[] }>("/projects")
      .then((d) => setProjects(d.projects))
      .catch(() => setProjects((prev) => prev ?? []));
  }, []);

  useEffect(() => {
    load();
    // Refresh when any page mutates projects (the Projects page dispatches this).
    const onChanged = () => load();
    window.addEventListener("ij:projects-changed", onChanged);
    return () => window.removeEventListener("ij:projects-changed", onChanged);
  }, [load]);

  // Outside click / Escape closes the popover.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!popRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function activate(id: string | null) {
    if (busy) return;
    setBusy(true);
    try {
      if (id === null) await post("/projects/deactivate");
      else await post(`/projects/${id}/activate`);
      load();
      window.dispatchEvent(new Event("ij:projects-changed"));
    } catch {
      /* daemon offline / project gone — the reload keeps the truth */
      load();
    } finally {
      setBusy(false);
      setOpen(false);
    }
  }

  const active = projects?.find((p) => p.active) ?? null;
  if (projects === null) return null; // first paint — no flicker

  return (
    <div ref={popRef} className="relative px-3 pb-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={
          active
            ? `Active project: ${active.name} — chat, sessions and workflows run in its context`
            : "No active project — pick one so every agent knows what you're working on"
        }
        className={`flex w-full items-center gap-2 rounded-xl border px-3 py-2 text-left text-[12px] transition-colors ${
          active
            ? "border-accent/25 bg-accent/[0.07] text-accent-soft"
            : "border-white/[0.08] bg-white/[0.02] text-zinc-500 hover:border-white/20 hover:text-zinc-300"
        }`}
      >
        <FolderKanban size={13} className="shrink-0" />
        <span className="min-w-0 flex-1 truncate font-medium">
          {active ? active.name : "No active project"}
        </span>
        <ChevronDown
          size={12}
          className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Switch project"
          className="absolute left-3 right-3 z-40 mt-1 overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-lg shadow-black/40"
        >
          <div className="max-h-56 overflow-y-auto p-1">
            {projects.length === 0 ? (
              <p className="px-2.5 py-2 text-[11px] text-zinc-500">
                No projects yet.
              </p>
            ) : (
              projects.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  role="option"
                  aria-selected={!!p.active}
                  disabled={busy}
                  onClick={() => void activate(p.active ? null : p.id)}
                  title={p.active ? "Click to deactivate" : `Work in ${p.name}`}
                  className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] transition-colors ${
                    p.active
                      ? "bg-accent/[0.12] text-accent-soft"
                      : "text-zinc-300 hover:bg-white/[0.05]"
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      p.active ? "bg-accent" : "bg-zinc-700"
                    }`}
                  />
                  <span className="truncate">{p.name}</span>
                </button>
              ))
            )}
          </div>
          <Link
            href="/projects"
            onClick={() => setOpen(false)}
            className="flex items-center gap-1.5 border-t hairline px-3 py-2 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
          >
            <Plus size={11} /> Manage projects
          </Link>
        </div>
      )}
    </div>
  );
}
