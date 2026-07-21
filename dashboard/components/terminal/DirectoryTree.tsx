"use client";

// Right-hand directory tree: browse the computer's folders one level at a time
// and pick a project directory to use as a terminal's cwd. Each folder is listed
// lazily (`GET /fs/list?dirs_only=true`) the first time it is expanded.

import { useState } from "react";
import {
  ChevronRight,
  Folder,
  FolderOpen,
  GitBranch,
  HardDrive,
  Loader2,
  PanelRightClose,
  PanelRightOpen,
  SquareTerminal,
  TriangleAlert,
} from "lucide-react";
import { ApiError, get } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { Drive, FsListing } from "@/lib/types";

/** Colour + short label for a project marker, or null for plain folders. */
function projectChip(kind: string) {
  const map: Record<string, { label: string; cls: string; git?: boolean }> = {
    git: { label: "git", cls: "border-accent/30 bg-accent/10 text-accent-soft", git: true },
    python: { label: "py", cls: "border-amber-500/30 bg-amber-500/10 text-amber-300" },
    node: { label: "node", cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" },
    rust: { label: "rust", cls: "border-orange-500/30 bg-orange-500/10 text-orange-300" },
    go: { label: "go", cls: "border-sky-500/30 bg-sky-500/10 text-sky-300" },
  };
  const m = map[kind] ?? {
    label: kind,
    cls: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  };
  return (
    <span
      className={`ml-auto inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0 text-[9px] font-medium ${m.cls}`}
      title={`${kind} project`}
    >
      {m.git && <GitBranch size={9} />}
      {m.label}
    </span>
  );
}

interface DirNodeData {
  name: string;
  path: string;
  is_project: string | null;
}

function DirNode({
  node,
  depth,
  selectedPath,
  onSelect,
  defaultOpen = false,
}: {
  node: DirNodeData;
  depth: number;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [children, setChildren] = useState<DirNodeData[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = selectedPath === node.path;

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await get<FsListing>(
        `/fs/list?path=${encodeURIComponent(node.path)}&dirs_only=true`,
      );
      setChildren(
        data.entries.map((e) => ({
          name: e.name,
          path: e.path,
          is_project: e.is_project,
        })),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setChildren([]);
    } finally {
      setLoading(false);
    }
  }

  // Auto-load the root node the first time it mounts open.
  if (defaultOpen && open && children === null && !loading && !error) {
    void load();
  }

  function onRowClick() {
    onSelect(node.path);
    const next = !open;
    setOpen(next);
    if (next && children === null && !loading) void load();
  }

  return (
    <div>
      <button
        onClick={onRowClick}
        title={node.path}
        style={{ paddingLeft: 6 + depth * 14 }}
        className={`flex w-full items-center gap-1.5 rounded-lg py-1 pr-2 text-left text-[12.5px] transition-colors ${
          active
            ? "bg-accent/[0.12] text-accent-soft ring-1 ring-inset ring-accent/30"
            : "text-zinc-300 hover:bg-white/[0.05]"
        }`}
      >
        <ChevronRight
          size={13}
          className={`shrink-0 text-zinc-500 transition-transform ${open ? "rotate-90" : ""}`}
        />
        {open ? (
          <FolderOpen size={14} className="shrink-0 text-accent-soft/80" />
        ) : (
          <Folder size={14} className="shrink-0 text-zinc-500" />
        )}
        <span className="truncate">{node.name}</span>
        {node.is_project && projectChip(node.is_project)}
      </button>

      {open && (
        <div>
          {loading && (
            <div
              className="flex items-center gap-1.5 py-1 text-[11px] text-zinc-500"
              style={{ paddingLeft: 6 + (depth + 1) * 14 }}
            >
              <Loader2 size={11} className="animate-spin" /> loading…
            </div>
          )}
          {error && (
            <div
              className="flex items-center gap-1.5 py-1 text-[11px] text-rose-300/80"
              style={{ paddingLeft: 6 + (depth + 1) * 14 }}
            >
              <TriangleAlert size={11} /> {error}
            </div>
          )}
          {!loading && !error && children && children.length === 0 && (
            <div
              className="py-1 text-[11px] text-zinc-600"
              style={{ paddingLeft: 6 + (depth + 1) * 14 }}
            >
              empty
            </div>
          )}
          {children?.map((c) => (
            <DirNode
              key={c.path}
              node={c}
              depth={depth + 1}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function DirectoryTree({
  selectedPath,
  onSelect,
  onOpenTerminal,
  hideAction = false,
  onCollapse,
}: {
  selectedPath: string | null;
  onSelect: (path: string) => void;
  /** Create a new terminal whose cwd is the selected directory. Optional so the
   *  tree can be reused as a plain folder PICKER (e.g. the LTM page), where the
   *  parent provides its own confirm button. */
  onOpenTerminal?: (path: string) => void;
  /** Hide the built-in "Open terminal here" action (picker-only reuse). */
  hideAction?: boolean;
  /** When provided, the collapse button hands control to the PARENT (which can
   *  shrink the whole column) instead of just hiding this panel's body. */
  onCollapse?: () => void;
}) {
  const { data, error, loading } = useApi<{ drives: Drive[] }>("/fs/drives");
  const drives = data?.drives ?? [];
  const [root, setRoot] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  // Typed root: a pasted Z:\Clients or \\server\share becomes the tree root —
  // the network-share path works even when the mapped letter is invisible to
  // this app's Windows session.
  const [typedRoot, setTypedRoot] = useState("");
  function goTypedRoot() {
    const p = typedRoot.trim().replace(/[“”"]/g, "");
    if (p) setRoot(p);
  }

  // Default the root to the first drive once they load.
  const activeRoot = root ?? drives[0]?.path ?? null;
  const rootLabel = drives.find((d) => d.path === activeRoot)?.label ?? activeRoot ?? "";

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-white/[0.06] bg-ink-850/80 shadow-card backdrop-blur-sm">
      <header className="flex shrink-0 items-center gap-2 border-b border-white/[0.06] px-4 py-3">
        <HardDrive size={15} className="text-accent-soft/80" />
        <h2 className="text-[13px] font-semibold tracking-wide text-zinc-200">
          Directory
        </h2>
        <button
          onClick={() => (onCollapse ? onCollapse() : setCollapsed((c) => !c))}
          title={onCollapse || !collapsed ? "Collapse panel" : "Expand panel"}
          className="ml-auto grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
        >
          {!onCollapse && collapsed ? <PanelRightOpen size={14} /> : <PanelRightClose size={14} />}
        </button>
      </header>

      {!collapsed && (
        <>
          {/* Drive / root selector */}
          <div className="shrink-0 border-b border-white/[0.06] px-4 py-3">
            <label className="mb-1.5 block text-[10px] uppercase tracking-[0.12em] text-zinc-400">
              Root
            </label>
            <select
              aria-label="Root directory"
              value={activeRoot ?? ""}
              onChange={(e) => setRoot(e.target.value)}
              className="field py-1.5 text-[13px]"
              disabled={loading || drives.length === 0}
            >
              {drives.length === 0 && <option value="">{loading ? "loading…" : "—"}</option>}
              {/* A typed root that isn't an enumerated drive still shows. */}
              {activeRoot && !drives.some((d) => d.path === activeRoot) && (
                <option value={activeRoot}>{activeRoot}</option>
              )}
              {drives.map((d) => (
                <option key={d.path} value={d.path}>
                  {d.label} — {d.path}
                </option>
              ))}
            </select>
            <div className="mt-1.5 flex items-center gap-1.5">
              <input
                value={typedRoot}
                onChange={(e) => setTypedRoot(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    goTypedRoot();
                  }
                }}
                placeholder={"or paste a path… \\\\server\\share\\Clients"}
                aria-label="Go to root path"
                spellCheck={false}
                className="field min-w-0 flex-1 py-1 font-mono text-[11px]"
              />
              <button
                type="button"
                onClick={goTypedRoot}
                disabled={!typedRoot.trim()}
                className="btn-ghost shrink-0 px-2 py-1 text-[11px]"
              >
                Go
              </button>
            </div>
          </div>

          {/* Selected directory + open-terminal-here action */}
          <div className="shrink-0 border-b border-white/[0.06] px-4 py-3">
            <div className="text-[10px] uppercase tracking-[0.12em] text-zinc-400">
              Selected
            </div>
            <div
              className="mt-1 truncate font-mono text-[12px] text-accent-soft"
              title={selectedPath ?? undefined}
            >
              {selectedPath ?? "— pick a folder below —"}
            </div>
            {!hideAction && onOpenTerminal && (
              <button
                onClick={() => selectedPath && onOpenTerminal(selectedPath)}
                disabled={!selectedPath}
                className="btn-accent mt-2.5 w-full py-1.5 text-[12px]"
              >
                <SquareTerminal size={13} /> Open terminal here →
              </button>
            )}
          </div>

          {/* The tree itself */}
          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
            {error ? (
              <div className="flex items-center gap-1.5 px-2 py-3 text-[12px] text-rose-300/80">
                <TriangleAlert size={13} /> {error.message}
              </div>
            ) : activeRoot ? (
              <DirNode
                key={activeRoot}
                node={{ name: rootLabel, path: activeRoot, is_project: null }}
                depth={0}
                selectedPath={selectedPath}
                onSelect={onSelect}
                defaultOpen
              />
            ) : (
              <div className="px-2 py-3 text-[12px] text-zinc-600">
                {loading ? "loading drives…" : "no drives"}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
