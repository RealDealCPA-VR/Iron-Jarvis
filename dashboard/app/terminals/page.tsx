"use client";

// Multi-terminal workspace: a FREE-FORM canvas of live xterm.js terminals on the
// left/center (each pane is dragged by its header and resized from its edges,
// like windows on a desktop), and a directory tree on the right for picking a
// project folder to open a terminal in. xterm is dynamically imported (no SSR).

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Rnd } from "react-rnd";
import {
  LayoutGrid,
  Loader2,
  PanelLeftOpen,
  Plus,
  SquareTerminal,
} from "lucide-react";
import { ApiError, del, get, post } from "@/lib/api";
import type { AiCli, ModelOption, Shell, Skill, TerminalInfo } from "@/lib/types";
import { Card, OfflineHint, ErrorNote, Spinner, ConfirmButton } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";

// xterm only runs in the browser — never during SSR / `next build`.
const TerminalPane = dynamic(
  () => import("@/components/terminal/TerminalPane").then((m) => m.TerminalPane),
  {
    ssr: false,
    loading: () => (
      <div className="grid h-full place-items-center text-zinc-600">
        <Loader2 size={18} className="animate-spin" />
      </div>
    ),
  },
);

// A pane's position + size on the free-form canvas.
type Rect = { x: number; y: number; width: number; height: number };

// Cascading default (fallback only) so freshly opened panes stagger.
function cascadeRect(i: number): Rect {
  return { x: 24 + (i % 5) * 34, y: 24 + (i % 5) * 34, width: 620, height: 380 };
}

// Axis-aligned rectangle overlap test (a small gutter keeps panes from touching).
function rectsOverlap(a: Rect, b: Rect, gutter = 6): boolean {
  return (
    a.x < b.x + b.width + gutter &&
    a.x + a.width + gutter > b.x &&
    a.y < b.y + b.height + gutter &&
    a.y + a.height + gutter > b.y
  );
}

export default function TerminalsPage() {
  const [terminals, setTerminals] = useState<TerminalInfo[]>([]);
  const [shells, setShells] = useState<Shell[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]); // per-pane AI picker
  const [aiClis, setAiClis] = useState<AiCli[]>([]); // per-pane "Launch CLI" menu
  const [skills, setSkills] = useState<Skill[]>([]); // per-pane AI skill picker
  const [shell, setShell] = useState<string>("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  // A terminal whose close was requested (from the pane's X) and is awaiting a
  // confirm — killing a live shell is irreversible, so we gate it.
  const [pendingClose, setPendingClose] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [treeCollapsed, setTreeCollapsed] = useState(false);

  // Per-terminal free-form layout (position + size), persisted to localStorage.
  const [layout, setLayout] = useState<Record<string, Rect>>({});
  // Stacking order — focusing/dragging a pane bumps it to the top. zTop is a
  // monotonic counter handed out as the next-highest z-index.
  const [zOrder, setZOrder] = useState<Record<string, number>>({});
  const zTop = useRef(1);
  const hydrated = useRef(false); // don't clobber stored layout before we read it
  const canvasRef = useRef<HTMLDivElement | null>(null);

  // Seed persisted UI state on mount (client-only — no localStorage during SSR).
  useEffect(() => {
    setTreeCollapsed(localStorage.getItem("ij_term_tree_collapsed") === "1");
    try {
      const raw = localStorage.getItem("ij_term_layout");
      if (raw) {
        const parsed = JSON.parse(raw) as unknown;
        if (parsed && typeof parsed === "object") {
          setLayout(parsed as Record<string, Rect>);
        }
      }
    } catch {
      /* bad JSON / private mode — start clean */
    }
    hydrated.current = true;
  }, []);

  // Persist the whole layout map whenever it changes (after hydration).
  useEffect(() => {
    if (!hydrated.current) return;
    try {
      localStorage.setItem("ij_term_layout", JSON.stringify(layout));
    } catch {
      /* private mode */
    }
  }, [layout]);

  // Find a FREE (non-overlapping, in-bounds) slot for a w×h pane given the rects
  // already placed — scans a coarse grid, falls back to a cascade only if the
  // canvas is full (the user can Tidy or resize to make room). This is what
  // keeps freshly-opened panes from spawning on top of existing ones.
  const findFreeSlot = useCallback((placed: Rect[], w: number, h: number): Rect => {
    const canvas = canvasRef.current;
    const cw = canvas?.clientWidth ?? 1200;
    const ch = canvas?.clientHeight ?? 640;
    const step = 28;
    for (let y = 12; y + h <= ch; y += step) {
      for (let x = 12; x + w <= cw; x += step) {
        const cand: Rect = { x, y, width: w, height: h };
        if (!placed.some((p) => rectsOverlap(cand, p))) return cand;
      }
    }
    return cascadeRect(placed.length);
  }, []);

  // Ensure every live terminal has a rect — fill missing ids with a FREE slot so
  // re-attached panes on load don't overlap (never mutate during render).
  useEffect(() => {
    setLayout((prev) => {
      let changed = false;
      const next = { ...prev };
      const placed: Rect[] = Object.values(next);
      terminals.forEach((t) => {
        if (!next[t.id]) {
          const r = findFreeSlot(placed, 620, 380);
          next[t.id] = r;
          placed.push(r);
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [terminals, findFreeSlot]);

  function changeTreeCollapsed(v: boolean) {
    setTreeCollapsed(v);
    try {
      localStorage.setItem("ij_term_tree_collapsed", v ? "1" : "0");
    } catch {
      /* private mode */
    }
  }

  // Focus + raise a pane to the front of the stack.
  const bringToFront = useCallback((id: string) => {
    setFocusedId(id);
    zTop.current += 1;
    const z = zTop.current;
    setZOrder((prev) => ({ ...prev, [id]: z }));
  }, []);

  // Merge a position/size patch into a pane's rect (drag = x/y, resize = all).
  const setRect = useCallback((id: string, patch: Partial<Rect>) => {
    setLayout((prev) => ({
      ...prev,
      [id]: { ...(prev[id] ?? cascadeRect(0)), ...patch },
    }));
  }, []);

  // The rect to render a pane at — persisted layout, else a cascading default.
  const rectFor = (t: TerminalInfo, i: number): Rect => layout[t.id] ?? cascadeRect(i);

  // Re-tile every pane into a neat 2-column grid that fits the canvas — the
  // escape hatch when the free-form layout gets messy.
  function tidy() {
    if (terminals.length === 0) return;
    const canvas = canvasRef.current;
    const cols = 2;
    const gap = 16;
    const pad = 16;
    const w = canvas?.clientWidth ?? 1200;
    const h = canvas?.clientHeight ?? 640;
    const rows = Math.ceil(terminals.length / cols) || 1;
    const cellW = Math.floor((w - pad * 2 - gap * (cols - 1)) / cols);
    const cellH = Math.floor((h - pad * 2 - gap * (rows - 1)) / rows);
    const next: Record<string, Rect> = {};
    terminals.forEach((t, i) => {
      const c = i % cols;
      const r = Math.floor(i / cols);
      next[t.id] = {
        x: pad + c * (cellW + gap),
        y: pad + r * (cellH + gap),
        width: Math.max(280, cellW),
        height: Math.max(200, cellH),
      };
    });
    setLayout(next);
  }

  // Re-attach to existing sessions + load the shell list on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [terms, sh, mods, clis, sks] = await Promise.all([
          get<{ terminals: TerminalInfo[] }>("/terminals"),
          get<{ shells: Shell[] }>("/terminals/shells").catch(() => ({ shells: [] })),
          get<{ models: ModelOption[] }>("/models").catch(() => ({ models: [] })),
          get<{ clis: AiCli[] }>("/terminals/ai-clis").catch(() => ({ clis: [] })),
          get<{ skills: Skill[] }>("/skills").catch(() => ({ skills: [] })),
        ]);
        if (cancelled) return;
        const alive = terms.terminals.filter((t) => t.alive);
        setTerminals(alive);
        // Deep-link from "Open in Build →" (Creative Studio): ?focus=<id>
        // brings that terminal to the front + centers it so the user lands
        // right on the pane they came to watch. Read window.location to avoid a
        // useSearchParams Suspense boundary under static export.
        let focusId: string | null = null;
        try {
          focusId = new URLSearchParams(window.location.search).get("focus");
        } catch {
          /* ignore */
        }
        const target = focusId ? alive.find((t) => t.id === focusId) : undefined;
        if (target) {
          setFocusedId(target.id);
          zTop.current += 1;
          const z = zTop.current;
          setZOrder((prev) => ({ ...prev, [target.id]: z }));
          const cw = canvasRef.current?.clientWidth ?? 1200;
          setLayout((prev) => {
            const cur = prev[target.id] ?? cascadeRect(0);
            return {
              ...prev,
              [target.id]: { ...cur, x: Math.max(24, Math.round((cw - cur.width) / 2)), y: 24 },
            };
          });
        } else {
          setFocusedId(alive[0]?.id ?? null);
        }
        setShells(sh.shells);
        setShell(sh.shells[0]?.name ?? "");
        // Only offer models the user can ACTUALLY run (provider connected).
        setModels(mods.models.filter((m) => m.available !== false));
        setAiClis(clis.clis);
        setSkills(sks.skills);
        setOffline(false);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 0) setOffline(true);
        else setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const addTerminal = useCallback(
    async (cwd?: string | null) => {
      setBusy(true);
      setError(null);
      try {
        // No explicit folder pick → the daemon falls back to the OS home dir.
        // No client-side path checks — if the daemon can't spawn there, its own
        // error surfaces below.
        const info = await post<TerminalInfo>("/terminals", {
          cwd: cwd ?? undefined,
          shell: shell || undefined,
        });
        setTerminals((prev) => [...prev, info]);
        // Place the new pane in a FREE slot so it never spawns on top of another,
        // and raise it to the front.
        setLayout((prev) => ({
          ...prev,
          [info.id]: findFreeSlot(Object.values(prev), 620, 380),
        }));
        zTop.current += 1;
        const z = zTop.current;
        setZOrder((prev) => ({ ...prev, [info.id]: z }));
        setFocusedId(info.id);
        setOffline(false);
      } catch (e) {
        if (e instanceof ApiError && e.status === 0) setOffline(true);
        else setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [shell],
  );

  const closeTerminal = useCallback((id: string) => {
    // Optimistically remove the pane (its WS unmounts), then kill server-side.
    setTerminals((prev) => prev.filter((t) => t.id !== id));
    setFocusedId((cur) => (cur === id ? null : cur));
    del(`/terminals/${id}`).catch(() => {
      /* already gone / offline — the pane is removed regardless */
    });
  }, []);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Build"
          subtitle="Live terminals on a free-form canvas — drag a pane by its header to move it, drag its edges to resize. Pick a project folder on the right and open a terminal there, or hit + to add one."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {/* Tidy — re-tile every pane into a neat grid when it gets messy. */}
              <button
                type="button"
                onClick={tidy}
                disabled={terminals.length === 0}
                title="Tidy — re-tile all terminals into a neat grid"
                className="btn-ghost flex items-center gap-1.5 py-1.5 text-[13px] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <LayoutGrid size={14} className="text-accent-soft/80" />
                Tidy
              </button>
              <span className="mx-1 h-5 w-px bg-white/10" />
              <label className="flex items-center gap-2 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                <SquareTerminal size={13} className="text-accent-soft/70" />
                Shell
              </label>
              <select
                aria-label="Shell"
                value={shell}
                onChange={(e) => setShell(e.target.value)}
                disabled={shells.length === 0}
                className="field w-auto py-1.5 text-[13px]"
              >
                {shells.length === 0 && <option value="">default</option>}
                {shells.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name}
                  </option>
                ))}
              </select>
              <button
                onClick={() => addTerminal(selectedPath)}
                disabled={busy}
                className="btn-accent py-1.5 text-[13px]"
              >
                {busy ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Plus size={14} />
                )}
                New terminal
              </button>
            </div>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint detail="Terminals and the directory tree both need it running." />
        </Reveal>
      )}
      {error && (
        <Reveal>
          <ErrorNote>{error}</ErrorNote>
        </Reveal>
      )}

      <Reveal>
        <div className="flex flex-col gap-5 lg:flex-row">
          {/* Terminals workspace (left / center) — free-form canvas. */}
          <div className="min-w-0 flex-1">
            {loading ? (
              <Card>
                <Spinner label="Attaching to sessions…" />
              </Card>
            ) : (
              <div
                ref={canvasRef}
                className="relative w-full overflow-hidden rounded-2xl border border-white/[0.05] bg-black/20"
                style={{ height: "calc(100vh - 12rem)", minHeight: 480 }}
              >
                {terminals.length === 0 ? (
                  <div className="grid h-full place-items-center text-sm text-zinc-500">
                    No terminals yet — hit New terminal.
                  </div>
                ) : (
                  terminals.map((t, i) => {
                    const r = rectFor(t, i);
                    return (
                      <Rnd
                        key={t.id}
                        size={{ width: r.width, height: r.height }}
                        position={{ x: r.x, y: r.y }}
                        bounds="parent"
                        minWidth={280}
                        minHeight={200}
                        dragHandleClassName="ij-term-drag"
                        cancel="button, select, input, textarea, .xterm, .xterm-viewport, .xterm-screen"
                        style={{ zIndex: zOrder[t.id] ?? 1 }}
                        onMouseDown={() => bringToFront(t.id)}
                        onDragStart={() => bringToFront(t.id)}
                        // Free movement: a pane goes exactly where you drop it
                        // (windows may overlap — the focused one comes to the
                        // front). No snap-back; use Tidy to re-pack into a grid.
                        onDragStop={(_e, d) => setRect(t.id, { x: d.x, y: d.y })}
                        onResizeStop={(_e, _dir, ref, _delta, pos) =>
                          setRect(t.id, {
                            x: pos.x,
                            y: pos.y,
                            width: ref.offsetWidth,
                            height: ref.offsetHeight,
                          })
                        }
                      >
                        <div className="relative h-full w-full">
                          <TerminalPane
                            info={t}
                            focused={focusedId === t.id}
                            onFocus={() => bringToFront(t.id)}
                            onClose={() => setPendingClose(t.id)}
                            models={models}
                            aiClis={aiClis}
                            skills={skills}
                            otherTerminals={terminals.map((x) => ({
                              id: x.id,
                              shell: x.shell,
                              cwd: x.cwd,
                            }))}
                          />
                          {pendingClose === t.id && (
                            <div className="absolute inset-0 z-20 grid place-items-center rounded-2xl bg-black/70 backdrop-blur-sm">
                              <div className="w-[min(20rem,90%)] rounded-2xl border border-white/10 bg-ink-850/95 p-5 text-center shadow-card">
                                <div className="text-sm font-semibold text-zinc-100">
                                  Close this terminal?
                                </div>
                                <p className="mt-1 break-all text-[12px] text-zinc-500">
                                  Ends the live shell session in {t.cwd}.
                                </p>
                                <div className="mt-4 flex items-center justify-center gap-2">
                                  <ConfirmButton
                                    onConfirm={() => {
                                      closeTerminal(t.id);
                                      setPendingClose(null);
                                    }}
                                    label="Close terminal"
                                    confirmLabel="Confirm close"
                                    title="End this shell session"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => setPendingClose(null)}
                                    className="btn-ghost py-1 text-xs"
                                  >
                                    Cancel
                                  </button>
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      </Rnd>
                    );
                  })
                )}

                {/* Compact floating add button — a small, always-there way to
                    open a terminal without hunting for the header button. */}
                <button
                  onClick={() => addTerminal(selectedPath)}
                  disabled={busy}
                  title="Open a new terminal"
                  className="absolute bottom-3 right-3 z-[9998] flex items-center gap-1.5 rounded-lg border border-accent/30 bg-ink-900/85 px-2.5 py-1.5 text-[12px] font-medium text-accent-soft shadow-card backdrop-blur transition-colors hover:bg-accent/15 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {busy ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Plus size={13} />
                  )}
                  Add
                </button>
              </div>
            )}
          </div>

          {/* Directory tree (right). Collapsing it shrinks the WHOLE column so
              the terminals workspace gets the freed horizontal space. */}
          <div
            className={`w-full shrink-0 transition-[width] duration-200 ${
              treeCollapsed ? "lg:w-11" : "lg:w-80 xl:w-96"
            }`}
          >
            <div className="lg:sticky lg:top-0 lg:h-[calc(100vh-9rem)]">
              {treeCollapsed ? (
                <button
                  onClick={() => changeTreeCollapsed(false)}
                  title="Show directory"
                  aria-label="Show directory"
                  className="flex w-full items-center justify-center gap-2 rounded-2xl border border-white/[0.06] bg-ink-850/60 py-2 text-[12px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft lg:h-full lg:flex-col lg:py-4"
                >
                  <PanelLeftOpen size={16} />
                  <span className="lg:hidden">Show directory</span>
                </button>
              ) : (
                <DirectoryTree
                  selectedPath={selectedPath}
                  onSelect={setSelectedPath}
                  onOpenTerminal={(p) => addTerminal(p)}
                  onCollapse={() => changeTreeCollapsed(true)}
                />
              )}
            </div>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
