"use client";

// Multi-terminal workspace: a FREE-FORM canvas of live xterm.js terminals on the
// left/center (each pane is dragged by its header and resized from its edges,
// like windows on a desktop), and a directory tree on the right for picking a
// project folder to open a terminal in. xterm is dynamically imported (no SSR).

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Rnd } from "react-rnd";
import {
  FileText,
  FolderTree,
  LayoutGrid,
  Loader2,
  MessageSquare,
  PanelLeftOpen,
  PanelRightClose,
  Plus,
  SquareTerminal,
  X,
} from "lucide-react";
import { ApiError, del, get, post } from "@/lib/api";
import type { AiCli, ModelOption, Shell, Skill, TerminalInfo } from "@/lib/types";
import { Card, OfflineHint, ErrorNote, Spinner, ConfirmButton } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";
import { FilesPanel } from "@/components/terminal/FilesPanel";

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

// Chat view for a pane (v1.206.0): the same box, flipped from a live shell to
// a chat thread grounded in that pane's folder. Self-contained by contract —
// own thread, composer, drag-drop, stream. Loaded like TerminalPane: browser
// only, never during SSR / `next build`.
const PaneChat = dynamic(
  () => import("@/components/terminal/PaneChat").then((m) => m.PaneChat),
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

// What a pane is SHOWING: the live terminal (default) or the pane chat.
// Persisted per pane id under `ij.pane.view.<paneId>` — only ever written on
// an explicit toggle, so existing users' storage is byte-for-byte untouched.
type PaneView = "terminal" | "chat";

const paneViewKey = (id: string) => `ij.pane.view.${id}`;

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
  // Right-column tab: "folders" (the picker) or "files" (live folder contents).
  const [treeTab, setTreeTab] = useState<"folders" | "files">("folders");
  // Once the user manually picks a tab, stop auto-defaulting it (session-sticky).
  const tabTouched = useRef(false);

  // Per-pane Terminal ⇄ Chat view, keyed by pane id. Seeded from localStorage
  // as panes appear (see the fill effect below); missing = "terminal".
  const [views, setViews] = useState<Record<string, PaneView>>({});
  // Panes whose chat has EVER been opened this session. Once open, PaneChat
  // STAYS MOUNTED for the pane's lifetime — the flip only changes which layer
  // is visible. PaneChat owns an in-flight turn (stream + thread saves), and
  // unmounting it mid-turn leaves that turn streaming in a dead closure whose
  // late last-write-wins save can erase a newer turn, or strands the first
  // exchange in an orphaned thread if the remount races the CREATE save.
  // Mounting is still LAZY (first flip) so never-toggled panes pay nothing.
  const [chatOpened, setChatOpened] = useState<Record<string, boolean>>({});

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

  // Seed each pane's persisted view as it appears (mirrors the layout fill
  // above — never during render, localStorage is client-only). Only a stored
  // "chat" flips anything; everything else stays the terminal default, and a
  // pane never toggled writes NOTHING back.
  useEffect(() => {
    setViews((prev) => {
      let changed = false;
      const next = { ...prev };
      terminals.forEach((t) => {
        if (next[t.id] !== undefined) return;
        let v: PaneView = "terminal";
        try {
          if (localStorage.getItem(paneViewKey(t.id)) === "chat") v = "chat";
        } catch {
          /* private mode — default stands */
        }
        next[t.id] = v;
        changed = true;
      });
      return changed ? next : prev;
    });
    // A pane restored INTO chat view counts as opened — its PaneChat mounts
    // now and stays mounted (a cwd-less pane can't ground a chat, so never).
    setChatOpened((prev) => {
      let changed = false;
      const next = { ...prev };
      terminals.forEach((t) => {
        if (next[t.id] || !t.cwd) return;
        try {
          if (localStorage.getItem(paneViewKey(t.id)) === "chat") {
            next[t.id] = true;
            changed = true;
          }
        } catch {
          /* private mode */
        }
      });
      return changed ? next : prev;
    });
  }, [terminals]);

  // Flip one pane's view and persist it under its own key. Opening chat marks
  // the pane so its PaneChat stays mounted across every later flip.
  const setPaneView = useCallback((id: string, v: PaneView) => {
    setViews((prev) => ({ ...prev, [id]: v }));
    if (v === "chat") {
      setChatOpened((prev) => (prev[id] ? prev : { ...prev, [id]: true }));
    }
    try {
      localStorage.setItem(paneViewKey(id), v);
    } catch {
      /* private mode */
    }
  }, []);

  // Per-pane "type into this session" writers (v1.207.0). Each TerminalPane
  // registers the same mechanism the v1.194 snippet path types with — raw
  // text over its attach WebSocket, fed to the PTY as keystrokes — and
  // unregisters with null on dispose. A ref, not state: writers arrive from
  // inside pane effects and must never re-render the whole canvas.
  const paneWriters = useRef<Record<string, (text: string) => boolean>>({});
  const registerWriter = useCallback(
    (id: string, write: ((text: string) => boolean) | null) => {
      if (write) paneWriters.current[id] = write;
      else delete paneWriters.current[id];
    },
    [],
  );

  // "Run in terminal" from a pane's chat. USER-CLICKED ONLY — PaneChat renders
  // a per-code-block button; the model never triggers this (suggest-don't-act,
  // same stance as the AI bar's "Type it in"). WRITE FIRST, FLIP ONLY ON
  // SUCCESS: a pane whose shell isn't connected refuses (false) and stays in
  // chat view, so the button can say so instead of dumping the user onto a
  // dead terminal. On success the pane flips to terminal view — the flip is
  // the point: the user WATCHES their command run in their own visible shell.
  //
  // The hand-over is normalized to PER-LINE writes: split the block on
  // newlines, drop trailing blank lines, terminate EVERY line with "\r" (the
  // byte xterm emits for the Enter key), delivered as ONE write so a dying
  // socket can never hand over half a block. Empirically pinned against live
  // ConPTY by the BC2 review — this is the only shape that keeps the promise:
  //  (a) "\n" is NOT Enter in ConPTY. A fence ending in a blank line used to
  //      skip the "\r" and leave the command typed-but-unexecuted (cmd.exe)
  //      or stuck in a ">>" continuation (PS 5.1) while we still returned
  //      true and flipped "so the user watches it run" — and nothing ran.
  //  (b) In cmd.exe, conhost drops mid-block LFs, so a trailing "\r" executed
  //      one unreviewed WELD ("echo AAA111echo BBB222", live repro). Per-line
  //      "\r" makes each line execute exactly as the user reviewed it.
  //  (c) In PowerShell, per-line + Enter is what a human typing produces —
  //      PSReadLine's continuation buffers an incomplete construct, so a
  //      multi-line block with an open brace still runs as one unit.
  // So "verbatim" means verbatim PER LINE: every line's characters reach the
  // shell untouched; only the line TERMINATORS are normalized to Enter (the
  // bytes-as-one-blob promise was empirically the wrong promise). A block of
  // nothing but blank lines refuses (false) — nothing to run, no flip.
  const runInPane = useCallback(
    (id: string, cmd: string): boolean => {
      const write = paneWriters.current[id];
      if (!write) return false; // pane never attached / already disposed
      const lines = cmd.split(/\r\n|\r|\n/);
      while (lines.length > 0 && lines[lines.length - 1].trim() === "") lines.pop();
      if (lines.length === 0) return false; // all blank — nothing to run, no flip
      const ok = write(lines.map((line) => `${line}\r`).join(""));
      if (!ok) return false;
      setPaneView(id, "terminal");
      return true;
    },
    [setPaneView],
  );

  function changeTreeCollapsed(v: boolean) {
    setTreeCollapsed(v);
    try {
      localStorage.setItem("ij_term_tree_collapsed", v ? "1" : "0");
    } catch {
      /* private mode */
    }
  }

  // The folder the Files tab watches: the focused terminal's cwd, else the
  // folder picked in the tree.
  const focusedFolder =
    terminals.find((t) => t.id === focusedId)?.cwd ?? selectedPath ?? null;

  // Default to Files when working in a folder, Folders otherwise — until the
  // user manually chooses a tab, after which their choice sticks for the session.
  useEffect(() => {
    if (tabTouched.current) return;
    setTreeTab(focusedFolder ? "files" : "folders");
  }, [focusedFolder]);

  function chooseTab(tab: "folders" | "files") {
    tabTouched.current = true;
    setTreeTab(tab);
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
                    // A pane with no cwd can't ground a chat — force terminal
                    // view even if a stale "chat" key survives in storage.
                    const view: PaneView = t.cwd ? (views[t.id] ?? "terminal") : "terminal";
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
                          {/* Terminal layer — ALWAYS mounted. Flipping to chat
                              hides it with visibility (NEVER display:none and
                              never an unmount): the PTY, its WebSocket, and the
                              scrollback all live inside TerminalPane, and
                              visibility keeps the holder's real box so the
                              v1.190.0 fit-before-connect + ResizeObserver
                              machinery measures true dimensions even while
                              hidden. display:none would zero the holder — a
                              (re)connect replay then wraps into a default-sized
                              buffer that no later fit can re-wrap. */}
                          <div
                            data-testid={`term-layer-${t.id}`}
                            className="h-full w-full"
                            style={{ visibility: view === "chat" ? "hidden" : "visible" }}
                          >
                            <TerminalPane
                              info={t}
                              focused={focusedId === t.id}
                              onFocus={() => bringToFront(t.id)}
                              onClose={() => setPendingClose(t.id)}
                              onWriterReady={(w) => registerWriter(t.id, w)}
                              models={models}
                              aiClis={aiClis}
                              skills={skills}
                              otherTerminals={terminals.map((x) => ({
                                id: x.id,
                                shell: x.shell,
                                cwd: x.cwd,
                              }))}
                            />
                          </div>

                          {/* Chat layer — same box, chat grounded in this
                              pane's folder. Its header carries ij-term-drag so
                              the pane still drags by its header in chat view;
                              the pane (not the view) stays the unit of focus,
                              so the Files tab keeps following this cwd.
                              SYMMETRIC with the terminal layer: mounted LAZILY
                              on the first flip to chat, then NEVER unmounted —
                              only hidden via visibility (never display:none,
                              never an unmount). PaneChat owns an in-flight
                              turn's stream and saves; unmounting it mid-turn
                              leaves the turn finishing in a dead closure whose
                              late last-write-wins save can erase a newer turn,
                              or orphans the first exchange if a remount races
                              the CREATE save. visibility:hidden also drops the
                              hidden layer out of hit-testing, focus, and
                              scrolling, so it can't sit invisibly over the
                              terminal and steal input. */}
                          {(view === "chat" || chatOpened[t.id]) && (
                            <div
                              data-testid={`chat-layer-${t.id}`}
                              style={{ visibility: view === "chat" ? "visible" : "hidden" }}
                              className={`absolute inset-0 z-10 flex flex-col overflow-hidden rounded-2xl border bg-[#0a0c11] shadow-card transition-colors ${
                                focusedId === t.id
                                  ? "border-accent/50 shadow-glow-sm ring-1 ring-accent/30"
                                  : "border-white/[0.07] hover:border-white/[0.14]"
                              }`}
                            >
                              <header className="ij-term-drag flex shrink-0 cursor-move items-center gap-2 border-b border-white/[0.06] bg-ink-900/60 px-3 py-2">
                                <MessageSquare
                                  size={13}
                                  className={focusedId === t.id ? "text-accent" : "text-zinc-500"}
                                />
                                <span className="shrink-0 font-mono text-[11px] font-semibold text-zinc-200">
                                  chat
                                </span>
                                <span
                                  className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500"
                                  title={t.cwd}
                                >
                                  {t.cwd}
                                </span>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setPendingClose(t.id);
                                  }}
                                  title="Close terminal"
                                  className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-rose-500/15 hover:text-rose-300"
                                >
                                  <X size={13} />
                                </button>
                              </header>
                              <div className="min-h-0 flex-1">
                                <PaneChat
                                  paneId={t.id}
                                  cwd={t.cwd}
                                  onRunCommand={(cmd) => runInPane(t.id, cmd)}
                                />
                              </div>
                            </div>
                          )}

                          {/* Terminal ⇄ Chat toggle. Floats just BELOW the
                              header in both views — deliberately outside every
                              ij-term-drag region, and buttons besides, which
                              react-rnd's `cancel` already exempts: clicking it
                              can never start a drag. */}
                          <div
                            data-testid={`pane-view-toggle-${t.id}`}
                            className="absolute right-1.5 top-10 z-20 flex items-center gap-0.5 rounded-lg border border-white/10 bg-ink-900/85 p-0.5 shadow-card backdrop-blur"
                          >
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setPaneView(t.id, "terminal");
                              }}
                              title="Terminal view"
                              aria-label={`Terminal view for pane ${t.id}`}
                              aria-pressed={view === "terminal"}
                              className={`grid h-5 w-5 place-items-center rounded-md transition-colors ${
                                view === "terminal"
                                  ? "bg-accent/15 text-accent"
                                  : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
                              }`}
                            >
                              <SquareTerminal size={12} />
                            </button>
                            <button
                              type="button"
                              disabled={!t.cwd}
                              onClick={(e) => {
                                e.stopPropagation();
                                if (t.cwd) setPaneView(t.id, "chat");
                              }}
                              title={
                                t.cwd
                                  ? "Chat view — talk to an agent grounded in this pane's folder"
                                  : "Chat needs a working folder — this terminal has no cwd"
                              }
                              aria-label={`Chat view for pane ${t.id}`}
                              aria-pressed={view === "chat"}
                              className={`grid h-5 w-5 place-items-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                                view === "chat"
                                  ? "bg-accent/15 text-accent"
                                  : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
                              }`}
                            >
                              <MessageSquare size={12} />
                            </button>
                          </div>

                          {pendingClose === t.id && (
                            <div className="absolute inset-0 z-30 grid place-items-center rounded-2xl bg-black/70 backdrop-blur-sm">
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
                  title="Show panel"
                  aria-label="Show panel"
                  className="flex w-full items-center justify-center gap-2 rounded-2xl border border-white/[0.06] bg-ink-850/60 py-2 text-[12px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft lg:h-full lg:flex-col lg:py-4"
                >
                  <PanelLeftOpen size={16} />
                  <span className="lg:hidden">Show panel</span>
                </button>
              ) : (
                <div className="flex h-full flex-col gap-2">
                  {/* Tab bar: Folders (picker) / Files (live folder contents). */}
                  <div className="flex shrink-0 items-center gap-1 rounded-xl border border-white/[0.06] bg-ink-850/60 p-1">
                    <button
                      type="button"
                      onClick={() => chooseTab("folders")}
                      className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] font-medium transition-colors ${
                        treeTab === "folders"
                          ? "bg-accent/[0.12] text-accent-soft ring-1 ring-inset ring-accent/30"
                          : "text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
                      }`}
                    >
                      <FolderTree size={13} /> Folders
                    </button>
                    <button
                      type="button"
                      onClick={() => chooseTab("files")}
                      className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] font-medium transition-colors ${
                        treeTab === "files"
                          ? "bg-accent/[0.12] text-accent-soft ring-1 ring-inset ring-accent/30"
                          : "text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
                      }`}
                    >
                      <FileText size={13} /> Files
                    </button>
                    <button
                      type="button"
                      onClick={() => changeTreeCollapsed(true)}
                      title="Collapse panel"
                      aria-label="Collapse panel"
                      className="ml-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
                    >
                      <PanelRightClose size={14} />
                    </button>
                  </div>

                  <div className="min-h-0 flex-1">
                    {treeTab === "folders" ? (
                      <DirectoryTree
                        selectedPath={selectedPath}
                        onSelect={setSelectedPath}
                        onOpenTerminal={(p) => addTerminal(p)}
                        onCollapse={() => changeTreeCollapsed(true)}
                      />
                    ) : (
                      <FilesPanel
                        folder={focusedFolder}
                        onOpenTerminal={(p) => addTerminal(p)}
                      />
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
