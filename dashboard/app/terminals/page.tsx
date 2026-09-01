"use client";

// Multi-terminal workspace: a FREE-FORM canvas of live xterm.js terminals on the
// left/center (each pane is dragged by its header and resized from its edges,
// like windows on a desktop), and a directory tree on the right for picking a
// project folder to open a terminal in. xterm is dynamically imported (no SSR).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Rnd } from "react-rnd";
import {
  FileText,
  FolderTree,
  LayoutGrid,
  Loader2,
  MessageSquare,
  PanelLeft,
  PanelLeftOpen,
  PanelRightClose,
  Plus,
  SquareTerminal,
  X,
} from "lucide-react";
import { ApiError, del, get, post } from "@/lib/api";
import type { AiCli, ModelOption, Shell, Skill, TerminalInfo } from "@/lib/types";
import { Card, OfflineHint, ErrorNote, Spinner, ConfirmButton } from "@/components/ui";
import { usePolledApi } from "@/lib/useApi";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";
import { FilesPanel } from "@/components/terminal/FilesPanel";
import {
  TERM_LINE_CHARS,
  TERM_PEEK_QUIET_MS,
  lastLine,
  type PaneChatStatus,
} from "@/components/terminal/paneStatusCore";
import { PaneRail, type RailPane } from "@/components/terminal/PaneRail";
import {
  PaneStateSummary,
  displayState,
  resolveState,
  type PaneActivity,
  type PaneState,
} from "@/components/terminal/PaneState";

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

/** Rail or canvas. Its own key — "ij_term_layout" is the RECT map. */
const SHAPE_KEY = "ij.build.shape";

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

  // ---- what each pane's agent is doing (v1.217.0) --------------------------
  // A SMALL, SEPARATE poll. The terminal list is loaded once and then mutated
  // locally on add/close, so re-polling it would fight those edits; this asks
  // only for the volatile part, exactly as `chatStatus` already does for the
  // hidden chat layer. 2.5s is a human-noticing cadence for "that pane stopped
  // and needs me", not a progress bar.
  const { data: activityData } = usePolledApi<{ panes: PaneActivity[] }>(
    "/terminals/activity",
    2500,
  );
  // HOW BUILD IS LAID OUT (v1.218.0). "rail" is a list of every pane with its
  // live state and ONE pane in focus; "canvas" is the free-form drag-and-drop
  // workspace Build has always been. The rail is the default because it is the
  // shape the state work was for — but the canvas is not deleted: seeing three
  // panes at once is a real way to work, and taking it away to add a list would
  // trade one workflow for another rather than adding one.
  const [shape, setShape] = useState<"rail" | "canvas">("rail");
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(SHAPE_KEY);
      if (saved === "canvas" || saved === "rail") setShape(saved);
    } catch {
      /* private mode / blocked storage — the default stands */
    }
  }, []);
  const chooseShape = useCallback((next: "rail" | "canvas") => {
    setShape(next);
    try {
      window.localStorage.setItem(SHAPE_KEY, next);
    } catch {
      /* the choice still applies to this session */
    }
  }, []);

  // A RENAME MUST LAND ON THE FIRST FRAME. The activity poll is the source of
  // truth for a pane's name, but it runs every 2.5s — long enough that typing
  // a name and watching the header snap back to the shell reads as a failed
  // save. These are the local echo, and the poll overwrites them as soon as
  // the daemon agrees.
  const [paneOverrides, setPaneOverrides] = useState<
    Record<string, { name?: string; cli?: string }>
  >({});
  const notePaneOverride = useCallback(
    (id: string, patchIn: { name?: string; cli?: string }) =>
      setPaneOverrides((prev) => ({ ...prev, [id]: { ...prev[id], ...patchIn } })),
    [],
  );

  const paneActivity = useMemo(() => {
    const m = new Map<string, PaneActivity>();
    for (const p of activityData?.panes ?? []) m.set(p.id, p);
    return m;
  }, [activityData]);

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

  // ---- pane progress visibility (v1.212.0, peek strip v1.213.0) -----------
  // The view toggle must not be blind: from terminal view the user needs to
  // SEE that the hidden chat is streaming — or worse, PAUSED on an approval
  // the daemon holds for up to 180s — and from chat view that the hidden
  // terminal printed new output. PaneChat reports {streaming, approval,
  // tool, textTail} via onStatus; TerminalPane fires onOutput (throttled) on
  // real PTY frames, carrying the frame's decoded text. Both feed the small
  // badges on the toggle buttons AND the one-line peek strip at the pane's
  // bottom edge (v1.213.0) — badges say THAT something is happening, the
  // strip shows WHAT, and clicking it flips to the hidden view.
  const [chatStatus, setChatStatus] = useState<Record<string, PaneChatStatus>>({});
  // Panes whose terminal printed output the user has NOT seen (it arrived
  // while the pane showed its chat layer). Cleared on the flip back.
  const [unseenTermOutput, setUnseenTermOutput] = useState<Record<string, boolean>>({});
  // The last output line the hidden terminal printed (ANSI-stripped, capped)
  // + when it landed. SERVER truth only: every entry came from a real PTY
  // frame. `at` powers the quiet-window hide below — timestamp state, no
  // polling loop.
  const [termPeek, setTermPeek] = useState<
    Record<string, { lastLine: string; at: number }>
  >({});
  // Re-render trigger for the quiet window: ONE timeout scheduled from the
  // LAST output frame (per pane) bumps this so the render re-evaluates the
  // recency check. New frames reschedule it — no interval ever runs.
  const [, bumpPeekEval] = useState(0);
  const termPeekTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  useEffect(() => {
    const timers = termPeekTimers.current;
    return () => {
      Object.values(timers).forEach((t) => clearTimeout(t));
    };
  }, []);
  // The views map, readable from onOutput callbacks: TerminalPane wires
  // ws.onmessage once per session id, so a closure over `views` would pin
  // the map from that render forever — the ref always holds the latest.
  /** The RAIL's rows (v1.218.0) — every live pane, in a stable order.
   *
   *  Built from the same `resolveState` the panes themselves use, so a row can
   *  never disagree with the chip inside the pane it selects, and NOT filtered:
   *  unlike the summary strip, a list that drops the quiet panes is not a list
   *  of panes. `displayState` is what turns the classifier's silence into a
   *  row that says something honest — "shell" for a pane with no agent in it,
   *  "can't tell" for one we cannot read.
   */
  const railPanes: RailPane[] = useMemo(
    () =>
      terminals
        .filter((t) => t.alive)
        .map((t) => {
          const a = paneActivity.get(t.id);
          const cli = paneOverrides[t.id]?.cli ?? a?.agent_cli ?? null;
          return {
            id: t.id,
            label:
              paneOverrides[t.id]?.name ?? a?.name ?? t.shell ?? t.id,
            state: displayState(
              resolveState(a?.state, Boolean(unseenTermOutput[t.id])),
              cli,
            ),
            cli,
            cwd: t.cwd,
            unseen: Boolean(unseenTermOutput[t.id]),
            chatApproval: Boolean(chatStatus[t.id]?.approval),
          };
        }),
    [terminals, paneActivity, unseenTermOutput, paneOverrides, chatStatus],
  );

  /** The summary's rows. Built from the SAME resolve the panes use, so the
   *  strip can never disagree with the chip on the pane it points at. */
  const paneSummary = useMemo(
    () =>
      terminals
        .filter((t) => t.alive)
        .map((t) => {
          const a = paneActivity.get(t.id);
          return {
            id: t.id,
            name: paneOverrides[t.id]?.name ?? a?.name ?? null,
            state: resolveState(a?.state, Boolean(unseenTermOutput[t.id])),
          };
        })
        .filter((p) => p.state !== "unknown" && p.state !== "idle"),
    [terminals, paneActivity, unseenTermOutput, paneOverrides],
  );

  const viewsRef = useRef<Record<string, PaneView>>({});
  viewsRef.current = views;

  /** PaneChat's onStatus sink — stored per pane, no-op when nothing changed
   *  (the reporter already reports only on change; this keeps a duplicate
   *  report from re-rendering the whole canvas anyway). */
  const reportChatStatus = useCallback((id: string, s: PaneChatStatus) => {
    setChatStatus((prev) => {
      const cur = prev[id];
      if (
        cur &&
        cur.streaming === s.streaming &&
        cur.approval === s.approval &&
        cur.tool === s.tool &&
        cur.textTail === s.textTail
      ) {
        return prev;
      }
      return { ...prev, [id]: s };
    });
  }, []);

  /** TerminalPane's onOutput sink. Output counts as UNSEEN only while the
   *  pane is showing its chat layer — output the user is already watching
   *  needs no badge or peek — and the check reads viewsRef so the
   *  once-per-session ws closure never judges against a stale views map.
   *  v1.213.0: also keeps the peek line. A chunk whose stripped last line is
   *  empty (pure escape traffic) refreshes `at` on an EXISTING entry (frames
   *  are landing — keep the line up) but never creates one: an empty husk is
   *  not a peek. */
  const noteTermOutput = useCallback((id: string, chunk: string) => {
    if (viewsRef.current[id] !== "chat") return;
    setUnseenTermOutput((prev) => (prev[id] ? prev : { ...prev, [id]: true }));
    const line = lastLine(chunk, TERM_LINE_CHARS);
    setTermPeek((prev) => {
      const cur = prev[id];
      if (!line && !cur) return prev; // nothing to show — no empty husk
      return {
        ...prev,
        [id]: { lastLine: line || (cur?.lastLine ?? ""), at: Date.now() },
      };
    });
    const timers = termPeekTimers.current;
    if (timers[id]) clearTimeout(timers[id]);
    timers[id] = setTimeout(() => {
      delete timers[id];
      bumpPeekEval((n) => n + 1);
    }, TERM_PEEK_QUIET_MS);
  }, []);

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
    } else {
      // Back on the terminal: whatever it printed is on screen now, so the
      // "new terminal output" badge has nothing left to announce (v1.212.0).
      setUnseenTermOutput((prev) => {
        if (!prev[id]) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
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
  /** The pane the rail is showing (v1.218.0).
   *
   *  On the canvas, "nothing focused" is a fine state — every pane is on screen
   *  regardless. In the rail it is not: focus decides what is VISIBLE, so a
   *  null would render an empty workspace. That is not hypothetical — it is
   *  every page load (focus starts null) and every close of the focused pane
   *  (it resets to null). Falling back to the first live pane means the rail
   *  always has something in it, and the fallback is derived rather than
   *  written into state so it cannot go stale against the list.
   */
  const activeId = useMemo(() => {
    const live = terminals.filter((t) => t.alive);
    if (focusedId && live.some((t) => t.id === focusedId)) return focusedId;
    return live[0]?.id ?? null;
  }, [terminals, focusedId]);

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
              {/* Back to the rail. Only on the canvas — in the rail its own
                  footer holds the other direction, so the switch always lives
                  in the shape you are leaving. */}
              {shape === "canvas" && (
                <button
                  type="button"
                  data-testid="shape-rail"
                  onClick={() => chooseShape("rail")}
                  title="Rail — every pane in a list with its state, one in focus"
                  className="btn-ghost flex items-center gap-1.5 py-1.5 text-[13px]"
                >
                  <PanelLeft size={14} />
                  Rail
                </button>
              )}
              {/* Tidy — re-tile every pane into a neat grid when it gets messy. */}
              {shape === "canvas" && (
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
              )}
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

      {/* NEVER HUNT FOR THE STUCK ONE — the CANVAS's version (v1.217.0, scoped
          to the canvas in v1.218.0). One line under the header naming the panes
          that stopped and need a human, each a button that brings one to the
          front. In the rail this is redundant: the list already shows every
          pane's state and carries its own jump, and saying it twice is how a
          surface teaches people to stop reading it. */}
      {shape === "canvas" && paneSummary.length > 0 && (
        <Reveal>
          <PaneStateSummary panes={paneSummary} onFocus={bringToFront} />
        </Reveal>
      )}

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
          {/* THE RAIL (v1.218.0) — Build's spine. Every pane in one column with
              its live state; clicking one brings it into focus in the workspace
              beside it. Hidden on the canvas, which has its own way of showing
              you everything at once. */}
          {shape === "rail" && !loading && (
            <div
              className="w-full shrink-0 lg:w-56 xl:w-64"
              style={{ height: "calc(100vh - 12rem)", minHeight: 480 }}
            >
              <PaneRail
                panes={railPanes}
                focusedId={activeId}
                onFocus={bringToFront}
                onClose={(id) => setPendingClose(id)}
                onNew={() => addTerminal(selectedPath)}
                busy={busy}
                footer={
                  <button
                    type="button"
                    data-testid="shape-canvas"
                    onClick={() => chooseShape("canvas")}
                    title="Free-form canvas — drag and resize panes, several visible at once"
                    className="flex w-full items-center gap-2 rounded-xl border border-white/[0.06] px-2 py-1.5 text-[11.5px] text-zinc-500 transition-colors hover:border-white/[0.14] hover:text-zinc-300"
                  >
                    <LayoutGrid size={13} className="shrink-0" />
                    Canvas
                  </button>
                }
              />
            </div>
          )}

          {/* The workspace. On the canvas it is a free-form surface of movable
              panes; in the rail it is one box that every pane fills, with the
              focused one visible. */}
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
                    // Toggle badges (v1.212.0): the HIDDEN layer's progress,
                    // painted on the button that would reveal it. Chat side
                    // only while the chat is hidden — an approval waiting
                    // OUTRANKS mere streaming (it blocks the turn for up to
                    // 180s and needs the user). Terminal side only while the
                    // chat is showing and unseen output arrived.
                    // WHAT THE AGENT IN THIS PANE IS DOING (v1.217.0). The
                    // daemon reports the settled state as `idle`; whether the
                    // user has LOOKED is a fact about this browser, and the
                    // page already tracks it for the peek strip — so the
                    // idle→done downgrade reuses that rather than inventing a
                    // second notion of seen-ness on the server.
                    const act = paneActivity.get(t.id);
                    const paneState: PaneState = resolveState(
                      act?.state,
                      Boolean(unseenTermOutput[t.id]),
                    );
                    const status = chatStatus[t.id];
                    const chatBadge =
                      view !== "chat" && status
                        ? status.approval
                          ? ("approval" as const)
                          : status.streaming
                            ? ("streaming" as const)
                            : null
                        : null;
                    const chatBadgeText =
                      chatBadge === "approval"
                        ? "Approval waiting in chat"
                        : chatBadge === "streaming"
                          ? "Chat is working"
                          : null;
                    const termBadge = view === "chat" && !!unseenTermOutput[t.id];
                    // Peek strip (v1.213.0): ONE slim line at the pane's
                    // bottom edge showing the HIDDEN view's live activity,
                    // clickable to flip. Renders only when it has something
                    // TRUE to say (server truth: a status the chat actually
                    // reported, bytes the terminal actually printed) — never
                    // an empty husk, never a synthesized "probably working".
                    // Suppressed under the pendingClose confirm (z-30 — the
                    // strip is z-20 anyway, but a clickable flip under a
                    // modal asking "close this pane?" is noise).
                    const chatPeek =
                      view !== "chat" &&
                      pendingClose !== t.id &&
                      status &&
                      (status.approval || status.streaming)
                        ? status.approval
                          ? {
                              amber: true,
                              text: "Approval needed — click to answer",
                            }
                          : {
                              amber: false,
                              // Precedence mirrors what the chat itself shows:
                              // a running tool row beats the token text.
                              text: status.tool
                                ? `Chat: ${status.tool}…`
                                : status.textTail
                                  ? `Chat: ${status.textTail}`
                                  : "Chat: working…",
                            }
                        : null;
                    const peek = termPeek[t.id];
                    const termPeekLine =
                      view === "chat" &&
                      pendingClose !== t.id &&
                      peek &&
                      peek.lastLine &&
                      (unseenTermOutput[t.id] ||
                        Date.now() - peek.at < TERM_PEEK_QUIET_MS)
                        ? peek.lastLine
                        : null;
                    // ONE pane body, TWO frames (v1.218.0). Everything below
                    // — both layers, the toggle, the peek strip, the close
                    // confirm — is identical in the rail and on the canvas;
                    // only what holds it differs. Building it once is not
                    // tidiness: the two frames would drift, and the drift
                    // would land in the pane the user works in.
                    const inner = (
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
                              paneName={paneOverrides[t.id]?.name ?? act?.name}
                              draggable={shape === "canvas"}
                              paneState={paneState}
                              agentCli={paneOverrides[t.id]?.cli ?? act?.agent_cli}
                              paneStateLine={act?.state_line}
                              onRenamed={(name) =>
                                notePaneOverride(t.id, { name: name || undefined })
                              }
                              onLaunched={(cli) => notePaneOverride(t.id, { cli })}
                              onFocus={() => bringToFront(t.id)}
                              onClose={() => setPendingClose(t.id)}
                              onWriterReady={(w) => registerWriter(t.id, w)}
                              onOutput={(chunk) => noteTermOutput(t.id, chunk)}
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
                              <header
                                className={`flex shrink-0 items-center gap-2 border-b border-white/[0.06] bg-ink-900/60 px-3 py-2 ${
                                  shape === "canvas" ? "ij-term-drag cursor-move" : ""
                                }`}
                              >
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
                                  onStatus={(s) => reportChatStatus(t.id, s)}
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
                              title={
                                termBadge
                                  ? "Terminal view — New terminal output"
                                  : "Terminal view"
                              }
                              aria-label={
                                termBadge
                                  ? `Terminal view for pane ${t.id} — New terminal output`
                                  : `Terminal view for pane ${t.id}`
                              }
                              aria-pressed={view === "terminal"}
                              className={`relative grid h-5 w-5 place-items-center rounded-md transition-colors ${
                                view === "terminal"
                                  ? "bg-accent/15 text-accent"
                                  : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
                              }`}
                            >
                              <SquareTerminal size={12} />
                              {/* INSIDE the button on purpose: react-rnd's
                                  cancel="button…" exempts the whole subtree
                                  from dragging, so the badge inherits it. */}
                              {termBadge ? (
                                <span
                                  data-testid={`pane-term-badge-${t.id}`}
                                  aria-hidden
                                  className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-accent shadow-glow-sm"
                                />
                              ) : null}
                            </button>
                            <button
                              type="button"
                              disabled={!t.cwd}
                              onClick={(e) => {
                                e.stopPropagation();
                                if (t.cwd) setPaneView(t.id, "chat");
                              }}
                              title={
                                !t.cwd
                                  ? "Chat needs a working folder — this terminal has no cwd"
                                  : chatBadgeText
                                    ? `Chat view — ${chatBadgeText}`
                                    : "Chat view — talk to an agent grounded in this pane's folder"
                              }
                              aria-label={
                                chatBadgeText
                                  ? `Chat view for pane ${t.id} — ${chatBadgeText}`
                                  : `Chat view for pane ${t.id}`
                              }
                              aria-pressed={view === "chat"}
                              className={`relative grid h-5 w-5 place-items-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                                view === "chat"
                                  ? "bg-accent/15 text-accent"
                                  : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
                              }`}
                            >
                              <MessageSquare size={12} />
                              {/* INSIDE the button (drag-exempt via the Rnd
                                  cancel). Amber pulse = an approval is
                                  WAITING; accent pulse = the chat is
                                  streaming a turn. */}
                              {chatBadge ? (
                                <span
                                  data-testid={`pane-chat-badge-${t.id}`}
                                  aria-hidden
                                  className={`absolute -right-0.5 -top-0.5 h-1.5 w-1.5 animate-pulse rounded-full ${
                                    chatBadge === "approval"
                                      ? "bg-amber-400"
                                      : "bg-accent"
                                  }`}
                                />
                              ) : null}
                            </button>
                          </div>

                          {/* LIVE PEEK STRIP (v1.213.0) — the hidden view's
                              activity as one slim line at the bottom edge,
                              above both layers (z-20; the visibility-hidden
                              layer is out of hit-testing anyway) and BELOW
                              the pendingClose confirm (z-30 — also gated out
                              above). A BUTTON so react-rnd's cancel keeps it
                              drag-exempt; pointer events live only on the
                              strip itself. Renders ONLY with something true
                              to say; clicking flips to the view doing the
                              talking. */}
                          {chatPeek ? (
                            <button
                              type="button"
                              data-testid={`pane-peek-${t.id}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setPaneView(t.id, "chat");
                              }}
                              title="Open the chat"
                              className={`absolute inset-x-0 bottom-0 z-20 truncate rounded-b-2xl border-t px-3 py-1 text-left text-[11px] backdrop-blur transition-colors ${
                                chatPeek.amber
                                  ? "border-amber-400/30 bg-amber-500/[0.18] text-amber-200 hover:bg-amber-500/[0.28]"
                                  : "border-white/10 bg-ink-900/85 text-zinc-300 hover:bg-accent/15 hover:text-accent-soft"
                              }`}
                            >
                              {chatPeek.text}
                            </button>
                          ) : termPeekLine ? (
                            <button
                              type="button"
                              data-testid={`pane-peek-${t.id}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setPaneView(t.id, "terminal");
                              }}
                              title="Open the terminal"
                              className="absolute inset-x-0 bottom-0 z-20 truncate rounded-b-2xl border-t border-white/10 bg-ink-900/85 px-3 py-1 text-left font-mono text-[11px] text-zinc-300 backdrop-blur transition-colors hover:bg-accent/15 hover:text-accent-soft"
                            >
                              {termPeekLine}
                            </button>
                          ) : null}

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
                    );
                    if (shape === "canvas") {
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
                          // Free movement: a pane goes exactly where you drop
                          // it (windows may overlap — the focused one comes to
                          // the front). No snap-back; Tidy re-packs the grid.
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
                          {inner}
                        </Rnd>
                      );
                    }
                    // RAIL: every pane fills the SAME box and only the focused
                    // one is visible. Not `display:none` and not an unmount —
                    // both are forbidden here for the reason v1.190.0 records:
                    // a terminal whose holder has no size wraps its replay into
                    // a default-sized buffer that no later fit can re-wrap, so
                    // a hidden pane must keep a real box. `visibility` also
                    // takes the hidden panes out of hit-testing and focus, so
                    // they cannot sit invisibly over the live one.
                    return (
                      <div
                        key={t.id}
                        data-testid={`rail-pane-${t.id}`}
                        className="absolute inset-0"
                        style={{
                          visibility: activeId === t.id ? "visible" : "hidden",
                          zIndex: activeId === t.id ? 2 : 1,
                        }}
                      >
                        {inner}
                      </div>
                    );
                  })
                )}

                {/* Compact floating add button — a small, always-there way to
                    open a terminal without hunting for the header button. The
                    rail has "New pane" in its own footer, so on the rail this
                    would be the third button doing one job. */}
                {shape === "canvas" && (
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
                )}
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
