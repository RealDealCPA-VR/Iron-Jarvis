"use client";

// Build (v1.369.0): two views behind one header switch.
//  - TERMINALS: the rail of live xterm.js terminals (every terminal with its
//    state, one in focus) and the folder / files panel on the right.
//  - IJ HARNESS: coding sessions (components/harness/IjHarnessView) — sessions
//    grouped by folder, the conversation in the middle, its plan / terminal /
//    changes / browser / files beside it.
// The free-form canvas is gone (IJ harness plan Phase 6); a window that had it
// saved opens the IJ harness view. xterm is dynamically imported (no SSR).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  Bot,
  Loader2,
  MessageSquare,
  PanelLeftOpen,
  PanelRightClose,
  Plus,
  SquareTerminal,
  X,
} from "lucide-react";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { paneTurn } from "@/components/terminal/paneChatCore";
import type { AiCli, ModelOption, Shell, Skill, TerminalInfo } from "@/lib/types";
import { Card, OfflineHint, Spinner, ConfirmButton, Empty } from "@/components/ui";
import { usePolledApi } from "@/lib/useApi";
import { useModels } from "@/lib/useModels";
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
import { PaneRail, RAIL_FOOTER_BUTTON, type RailPane } from "@/components/terminal/PaneRail";
import {
  livePaneAccounts,
  paneAccountBadge,
  paneLimitOf,
  resumeFailedOf,
  signInNeededOf,
  signedInOf,
} from "@/lib/paneAccounts";
import { PANE_VIEW_PREFIX, prunePaneStorage } from "@/components/terminal/paneKeys";
import { disposePaneHost, retainPaneHosts } from "@/components/terminal/paneHost";
import { PANE_TOGGLE_PLACE, PANE_TOGGLE_SLOT } from "@/components/terminal/paneViewToggle";
import { paneFrameClass } from "@/components/terminal/paneFrame";
import {
  displayState,
  resolveState,
  type PaneActivity,
  type PaneState,
} from "@/components/terminal/PaneState";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { viewFromSaved, type BuildView } from "@/lib/buildSessions";
import { BUILD_NIGHT_EVENT, BUILD_SESSION_EVENT, BUILD_TERMINAL_EVENT, sessionOfEvent } from "@/lib/buildLinks";
import { CODING_VIEW_NAME } from "@/lib/buildWords";
import { cachedShape, keepShape } from "@/lib/turnCache";

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

// The IJ harness view (v1.369.0): browser only, loaded when it is first shown.
const IjHarnessView = dynamic(
  () => import("@/components/harness/IjHarnessView").then((m) => m.IjHarnessView),
  {
    ssr: false,
    loading: () => (
      <div className="grid h-48 place-items-center text-zinc-600">
        <Loader2 size={18} className="animate-spin" />
      </div>
    ),
  },
);

// What a pane is SHOWING: the live terminal (default) or the pane chat.
// Persisted per pane id under `ij.pane.view.<paneId>` — only ever written on
// an explicit toggle, so existing users' storage is byte-for-byte untouched.
type PaneView = "terminal" | "chat";

const paneViewKey = (id: string) => `${PANE_VIEW_PREFIX}${id}`;

/** Which view Build shows: "rail" (Terminals) or "harness" (IJ harness).
 *  v1.369.0: a saved "canvas" (the retired free-form layout) opens the IJ
 *  harness view (lib/buildSessions `viewFromSaved`). */
const SHAPE_KEY = "ij.build.shape";


/** The folder this window last worked in (v1.337.2): the last folder picked
 *  or the pane in front's (a new terminal is put in front). "New terminal" opens
 *  there when nothing is picked, so a restart or an update does not send the
 *  user back through the folder tree to the project they were just in. */
const BUILD_FOLDER_KEY = "ij.build.folder";

/** v1.406.0 (part 6): how often, and how many times, Build re-reads the pane
 *  list while the daemon is still reopening a pane after a restart. */
const RESTORING_REREAD_MS = 3000;
const RESTORING_REREADS = 20;

/** The Build side panel's two tabs (v1.329.0: plain text tabs). */
const BUILD_PANEL_TABS = [
  { value: "folders", label: "Folders" },
  { value: "files", label: "Files" },
] as const;

/** True when this page was opened by a link to one terminal
 *  (`/terminals?focus=<id>`). Read once, at the first render; false on the
 *  server (no window). */
function focusLinkInAddress(): boolean {
  try {
    return typeof window !== "undefined" && !!new URLSearchParams(window.location.search).get("focus");
  } catch {
    return false;
  }
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
  // WHICH VIEW (v1.218.0 the rail; v1.369.0 the IJ harness). "rail" lists
  // every terminal with its live state and ONE in focus; "harness" is the
  // coding sessions. The free-form canvas was retired in v1.369.0 (the IJ
  // harness took its place in the switch), so a saved "canvas" opens the
  // harness. A link to a session (`?session=<id>`) opens the harness on it; a
  // link to a terminal (`?focus=<id>`) opens the rail on it.
  // v1.384.0: the view this WINDOW last showed (lib/turnCache) — a return to
  // Build opens on it at its first render, instead of the rail and then
  // (once the saved choice is read) the harness: "my work jumped" on every
  // return. Module state, so a real reload starts from the default, exactly
  // as the server renders it.
  // v1.388.1: a link to a terminal (`?focus=<id>`) opens the Terminals view
  // from the FIRST frame, whatever this window last showed — "Open the Build
  // terminal" after Sign in landed on the IJ harness and hid the pane.
  const [shape, setShape] = useState<BuildView>(() =>
    focusLinkInAddress() ? "rail" : (cachedShape<BuildView>() ?? "rail"),
  );
  useEffect(() => {
    keepShape(shape);
  }, [shape]);
  const [linkSession, setLinkSession] = useState<string | null>(null);
  // v1.399.0 (night shift N2): a link to Last night (`?night=<date>`, the
  // bell's row and the tray notice) opens Coding sessions on that view. The
  // count makes a second press of the same link open it again.
  const [linkNight, setLinkNight] = useState<{ night: string; n: number } | null>(null);
  useEffect(() => {
    let params: URLSearchParams | null = null;
    try {
      params = new URLSearchParams(window.location.search);
    } catch {
      params = null;
    }
    const session = params?.get("session") ?? null;
    if (session) {
      setLinkSession(session);
      setShape("harness");
      return;
    }
    const night = params?.get("night") ?? null;
    if (night) {
      setLinkNight({ night, n: 1 });
      setShape("harness");
      return;
    }
    if (params?.get("focus")) {
      setShape("rail"); // a terminal link: the rail, never the cached view
      return;
    }
    try {
      setShape(viewFromSaved(window.localStorage.getItem(SHAPE_KEY)));
    } catch {
      /* private mode / blocked storage — the default stands */
    }
  }, []);
  const chooseShape = useCallback((next: BuildView) => {
    setShape(next);
    try {
      window.localStorage.setItem(SHAPE_KEY, next);
    } catch {
      /* the choice still applies to this session */
    }
    if (next === "rail") {
      // The session link belongs to the harness view; the rail's address is
      // plain Build (the harness puts the session back when it opens). The
      // link the page was opened with is spent too: coming back to the
      // harness opens the session this window had open, not that old link
      // (v1.369.0 review).
      setLinkSession(null);
      setLinkNight(null);
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete("session");
        url.searchParams.delete("night");
        window.history.replaceState(window.history.state, "", url.toString());
      } catch {
        /* no history API */
      }
    }
  }, []);

  // A link to a session pressed while Build is already open (the bell): the
  // same-page navigation keeps this page's state, so the session is
  // announced instead (lib/buildLinks BUILD_SESSION_EVENT) — open the IJ
  // harness on it (v1.369.0 review). A harness view already showing opens
  // it itself.
  useEffect(() => {
    const onLink = (e: Event) => {
      const id = sessionOfEvent(e);
      if (!id) return;
      setLinkSession(id);
      setShape("harness");
      try {
        window.localStorage.setItem(SHAPE_KEY, "harness");
      } catch {
        /* the view still opens */
      }
    };
    window.addEventListener(BUILD_SESSION_EVENT, onLink);
    return () => window.removeEventListener(BUILD_SESSION_EVENT, onLink);
  }, []);
  // v1.399.0: the same for a link to Last night pressed while Build is open.
  useEffect(() => {
    const onNight = (e: Event) => {
      const night = sessionOfEvent(e);
      if (!night) return;
      setLinkNight((cur) => ({ night, n: (cur?.n ?? 0) + 1 }));
      setShape("harness");
      try {
        window.localStorage.setItem(SHAPE_KEY, "harness");
      } catch {
        /* the view still opens */
      }
    };
    window.addEventListener(BUILD_NIGHT_EVENT, onNight);
    return () => window.removeEventListener(BUILD_NIGHT_EVENT, onNight);
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
  // Per-pane AI picker. v1.250.0 (S-02): the SHARED catalog, not a fetch of
  // this page's own — the title bar's switcher has usually loaded it already,
  // and `/models` measured 2.1–3.4 s on the live install (see the note below).
  // `usable` is the same filter this page applied by hand: only models a turn
  // could actually reach, with an older daemon's unrated ones still offered.
  const catalog = useModels();
  const models = catalog.usable;
  const [aiClis, setAiClis] = useState<AiCli[]>([]); // per-pane "Launch CLI" menu
  const [skills, setSkills] = useState<Skill[]>([]); // per-pane AI skill picker
  const [shell, setShell] = useState<string>("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  // v1.337.2: the folder this window last worked in (BUILD_FOLDER_KEY).
  const [lastFolder, setLastFolder] = useState<string | null>(null);
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(BUILD_FOLDER_KEY);
      if (saved) setLastFolder(saved);
    } catch {
      /* private mode / blocked storage — no remembered folder */
    }
  }, []);
  const rememberFolder = useCallback((path: string | null | undefined) => {
    if (!path) return;
    setLastFolder(path);
    try {
      window.localStorage.setItem(BUILD_FOLDER_KEY, path);
    } catch {
      /* still remembered for this session */
    }
  }, []);
  const pickFolder = useCallback(
    (path: string) => {
      setSelectedPath(path);
      rememberFolder(path);
    },
    [rememberFolder],
  );
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
  // When each pane's newest output frame landed (v1.333.0) — the quiet
  // window is measured from HERE, so a frame that changes nothing on screen
  // needs no state update.
  const termPeekAt = useRef<Record<string, number>>({});
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
  /** The CLI's own name, by its id. Read off the SAME catalog the Launch menu
   *  renders, so the rail says exactly what the user clicked ("Grok CLI", not
   *  "grok"). Falls back to the id for a CLI sniffed out of scrollback that the
   *  catalog has never heard of — a name we half-know beats none. */
  const cliLabel = useCallback(
    (id: string | null | undefined) => {
      if (!id) return null;
      return aiClis.find((c) => c.id === id)?.label ?? id;
    },
    [aiClis],
  );

  const railPanes: RailPane[] = useMemo(
    () =>
      terminals
        .filter((t) => t.alive)
        .map((t) => {
          const a = paneActivity.get(t.id);
          const cli = paneOverrides[t.id]?.cli ?? a?.agent_cli ?? null;
          return {
            id: t.id,
            // `||`, not `??` (v1.329.0): a pane whose name was cleared reads
            // as "" from the daemon, and a row with no name at all is a dot
            // over nothing; it falls back to the shell's name like the header.
            label:
              paneOverrides[t.id]?.name || a?.name || t.shell || t.id,
            state: displayState(
              resolveState(a?.state, Boolean(unseenTermOutput[t.id])),
              cli,
            ),
            cli: cliLabel(cli),
            cwd: t.cwd,
            unseen: Boolean(unseenTermOutput[t.id]),
            chatApproval: Boolean(chatStatus[t.id]?.approval),
            // v1.238.0: what this pane's harness may reach. Straight off
            // GET /terminals (session.info()), never a client-side guess — the
            // server is the only authority, and the rail's checkbox writes back
            // through PATCH rather than deciding anything itself.
            capabilities: t.capabilities ?? null,
            // v1.302.0: the account this pane started on (the header chip's label).
            // Live: the activity poll's accounts win over the row read at load.
            account: paneAccountBadge(livePaneAccounts(t, a), cli),
            // v1.329.0: the row's quiet age.
            since: t.created_at ?? null,
          };
        }),
    [terminals, paneActivity, unseenTermOutput, paneOverrides, chatStatus, cliLabel],
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
    // v1.333.0: the newest frame's time lives in a ref, so a frame that
    // leaves the peek exactly as it is (same line, still inside the quiet
    // window) changes no state and re-renders nothing. A TUI repainting its
    // status line made a new object ~3 times a second per pane. A frame
    // that brings a line back after the quiet window still renders.
    const now = Date.now();
    const lastAt = termPeekAt.current[id];
    setTermPeek((prev) => {
      const cur = prev[id];
      if (!line && !cur) return prev; // nothing to show — no empty husk
      termPeekAt.current[id] = now;
      const next = line || (cur?.lastLine ?? "");
      if (
        cur &&
        cur.lastLine === next &&
        lastAt !== undefined &&
        now - lastAt < TERM_PEEK_QUIET_MS
      ) {
        return prev;
      }
      return {
        ...prev,
        [id]: { lastLine: next, at: now },
      };
    });
    const timers = termPeekTimers.current;
    if (timers[id]) clearTimeout(timers[id]);
    timers[id] = setTimeout(() => {
      delete timers[id];
      bumpPeekEval((n) => n + 1);
    }, TERM_PEEK_QUIET_MS);
  }, []);

  // Seed persisted UI state on mount (client-only — no localStorage during SSR).
  useEffect(() => {
    setTreeCollapsed(localStorage.getItem("ij_term_tree_collapsed") === "1");
  }, []);

  // Seed each pane's persisted view as it appears (never during render,
  // localStorage is client-only). Only a stored
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

  /** The pane the rail is showing (v1.218.0).
   *
   *  In the rail, focus decides what is VISIBLE, so a null would render an
   *  empty workspace. That is not hypothetical — it is
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

  // v1.337.2: where "New terminal" opens — the folder picked in the tree,
  // else the pane in front's folder, else the folder this window last worked
  // in. The pane in front is remembered too, so closing it (a restored pane
  // that misbehaves, say) still leaves its project as the next pane's folder.
  const activeCwd = terminals.find((t) => t.id === activeId)?.cwd ?? null;
  useEffect(() => {
    rememberFolder(activeCwd);
  }, [activeCwd, rememberFolder]);
  const newFolder = selectedPath ?? activeCwd ?? lastFolder;

  /** Rename a pane from the rail (v1.219.0).
   *
   *  The same two steps the pane header already does, in the same order and for
   *  the same reason: the local echo lands on this frame so the row does not
   *  snap back to the old name while a 2.5s poll catches up, and the PATCH is
   *  fire-and-forget because the poll is the source of truth and a failed
   *  rename is cosmetic. It is PARTIAL — `name` only — so renaming a pane
   *  cannot drop the `agent_cli` a launch recorded on it.
   */
  const renamePane = useCallback(
    (id: string, name: string) => {
      notePaneOverride(id, { name: name || undefined });
      patch(`/terminals/${id}`, { name }).catch(() => {
        /* offline / pane gone — the activity poll restores the truth */
      });
    },
    [notePaneOverride],
  );

  // Focus a terminal (the rail shows the focused one).
  const bringToFront = useCallback((id: string) => {
    setFocusedId(id);
  }, []);

  // Harness gaps (fix 2): a link to a terminal pressed while Build is open
  // (the Sign in press under a coding session's failed reply opened one):
  // the same-page navigation keeps this page's state, so the terminal is
  // announced (lib/buildLinks BUILD_TERMINAL_EVENT). The list was read once,
  // so a terminal started elsewhere is read again and added; then the
  // Terminals view shows it, like a `?focus=` link at load (not remembered).
  useEffect(() => {
    let alive = true;
    const onLink = (e: Event) => {
      const id = sessionOfEvent(e);
      if (!id) return;
      setShape("rail");
      get<{ terminals: TerminalInfo[] }>("/terminals")
        .then((res) => {
          if (!alive) return;
          const found = (res?.terminals ?? []).find((t) => t.id === id && t.alive);
          if (found) setTerminals((prev) => (prev.some((t) => t.id === id) ? prev : [...prev, found]));
          setFocusedId(id);
        })
        .catch(() => {
          if (alive) setFocusedId(id);
        });
    };
    window.addEventListener(BUILD_TERMINAL_EVENT, onLink);
    return () => {
      alive = false;
      window.removeEventListener(BUILD_TERMINAL_EVENT, onLink);
    };
  }, []);

  /** From the IJ harness view's Terminals group: open that terminal in the
   *  Terminals view, through the same focus the rail uses. */
  const openInTerminals = useCallback(
    (id: string) => {
      bringToFront(id);
      chooseShape("rail");
    },
    [bringToFront, chooseShape],
  );

  // Re-attach to existing sessions + load the shell list on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // THE PANES FIRST (v1.243.0). This used to await five requests together,
        // and the page showed nothing but "Attaching to sessions…" until the
        // slowest answered. Measured on the live install: `/terminals` in 5 ms,
        // `/models` in 2.1–3.4 s, a cold `/terminals/ai-clis` in 3.1 s — so
        // every return to Build waited seconds for catalogs that only fill the
        // per-pane pickers and the Launch menu. The panes render the moment
        // the list is in; the catalogs (below) arrive when they arrive.
        const terms = await get<{ terminals: TerminalInfo[]; restoring?: string[] }>("/terminals");
        if (cancelled) return;
        const alive = terms.terminals.filter((t) => t.alive);
        // v1.406.0 (part 6): panes the daemon is still reopening after a
        // restart (it stops waiting for a slow shell after a few seconds and
        // opens it the moment it starts). They are the user's panes: their
        // saved keys and hosts are kept, never pruned.
        const restoring = Array.isArray(terms.restoring)
          ? terms.restoring.filter((id): id is string => typeof id === "string")
          : [];
        const keep = [...alive.map((t) => t.id), ...restoring];
        // Terminals outlive this page (v1.243.0, components/terminal/paneHost):
        // a pane's xterm and socket stay open while you are elsewhere, so a
        // return is instant. This is where that ends for a pane the daemon no
        // longer offers — closed by an agent or another window, or a shell
        // that exited — so no socket stays open for a pane nobody can see.
        retainPaneHosts(keep);
        // v1.280.0: the per-pane keys (view, thread, rect) of panes the daemon
        // no longer offers are removed — they were never removed before, and
        // grew for the life of the install. Only after a SUCCESSFUL answer.
        prunePaneStorage(keep);
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
        } else {
          setFocusedId(alive[0]?.id ?? null);
        }
        setOffline(false);
        setLoading(false); // the rail is drawn; a late pane below only joins it
        // v1.406.0 (part 6): a pane still being reopened joins the rail when
        // its shell starts. Re-read a few times, adding only panes this page
        // does not hold (local adds and closes are never overwritten).
        let waitingFor = restoring;
        for (let i = 0; waitingFor.length > 0 && i < RESTORING_REREADS; i += 1) {
          await new Promise((r) => window.setTimeout(r, RESTORING_REREAD_MS));
          if (cancelled) return;
          let again: { terminals: TerminalInfo[]; restoring?: string[] };
          try {
            again = await get<{ terminals: TerminalInfo[]; restoring?: string[] }>("/terminals");
          } catch {
            continue;
          }
          if (cancelled) return;
          const opened = again.terminals.filter((t) => t.alive && waitingFor.includes(t.id));
          if (opened.length) {
            setTerminals((prev) => [
              ...prev,
              ...opened.filter((t) => !prev.some((p) => p.id === t.id)),
            ]);
          }
          waitingFor = Array.isArray(again.restoring)
            ? again.restoring.filter((id): id is string => typeof id === "string")
            : [];
        }
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 0) setOffline(true);
        else setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    // The catalogs, in the background (see above) — each on its own, so a slow
    // model list never holds up the Launch menu or the other way round, and
    // each degrades to empty on its own failure exactly as before.
    get<{ shells: Shell[] }>("/terminals/shells")
      .then((sh) => {
        if (cancelled) return;
        setShells(sh.shells);
        setShell((cur) => cur || (sh.shells[0]?.name ?? ""));
      })
      .catch(() => {
        /* no list — the selector says "default" and the daemon picks */
      });
    // The model catalog comes from the shared hook (v1.250.0, S-02) — see
    // `catalog` above — so it is no longer one of the slow catalogs this
    // effect waits on.
    get<{ clis: AiCli[] }>("/terminals/ai-clis")
      .then((clis) => {
        if (!cancelled) setAiClis(clis.clis);
      })
      .catch(() => {
        /* the Launch menu keeps saying "Detecting…" */
      });
    get<{ skills: Skill[] }>("/skills")
      .then((sks) => {
        if (!cancelled) setSkills(sks.skills);
      })
      .catch(() => {
        /* no skill picker in the assist bar */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const addTerminal = useCallback(
    async (cwd?: string | null, recipe?: string | null): Promise<TerminalInfo | null> => {
      setBusy(true);
      setError(null);
      try {
        // No explicit folder pick → the daemon falls back to the OS home dir.
        // No client-side path checks — if the daemon can't spawn there, its own
        // error surfaces below.
        //
        // `recipe` (v1.238.0) MUST be asked for HERE, at creation, and can never
        // be added to a pane that already exists. A recipe's whole job is to put
        // IRONJARVIS_MCP_URL and a pane-scoped token into the harness's
        // ENVIRONMENT, and the daemon merges that env before the shell is
        // spawned. The Launch menu types a command into an already-running
        // shell, so a pane launched the ordinary way can never be given those —
        // and the token must never be TYPED, because a shell keeps history and
        // scrollback. So "launch a harness with Jarvis capabilities" is a new
        // pane, not a command in this one.
        const info = await post<TerminalInfo>("/terminals", {
          cwd: cwd ?? undefined,
          shell: shell || undefined,
          recipe: recipe ?? undefined,
        });
        setTerminals((prev) => [...prev, info]);
        setFocusedId(info.id);
        setOffline(false);
        return info;
      } catch (e) {
        if (e instanceof ApiError && e.status === 0) setOffline(true);
        else setError(e instanceof ApiError ? e.message : String(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [shell],
  );

  /** v1.302.0: a pane the daemon started for this page (Launch → "as <another
   *  account>") — added and focused like one made with the + button. */
  const adoptPane = useCallback(
    (pane: { id: string }) => {
      const info = { alive: true, ...pane } as unknown as TerminalInfo;
      setTerminals((prev) => (prev.some((t) => t.id === info.id) ? prev : [...prev, info]));
      bringToFront(info.id);
    },
    [bringToFront],
  );

  const closeTerminal = useCallback((id: string) => {
    // v1.339.0: closing a terminal ends its chat's running turn too — the
    // daemon keeps a NAMED turn running when its pane merely goes away.
    const turn = paneTurn(id);
    if (turn) post(`/chat/turns/${encodeURIComponent(turn)}/stop`).catch(() => {});
    // Optimistically remove the pane, then kill server-side. Its terminal
    // and socket OUTLIVE an unmount (v1.243.0 — that is how a return to
    // Build is instant), so closing is the one place they are ended for good.
    disposePaneHost(id);
    setTerminals((prev) => prev.filter((t) => t.id !== id));
    setFocusedId((cur) => (cur === id ? null : cur));
    del(`/terminals/${id}`).catch(() => {
      /* already gone / offline — the pane is removed regardless */
    });
  }, []);

  /** ONE terminal body, drawn by the rail AND by a Build session's terminal
   *  tab (the IJ harness view): the page's own TerminalPane with the page's
   *  own state (names, launches, activity, accounts, writers, output). The
   *  pane host keeps its xterm and socket whichever of the two shows it. */
  const renderTerminalPane = (
    t: TerminalInfo,
    o: { focused: boolean; parked: boolean; reserveViewToggle: boolean; onClose: () => void },
  ) => {
    const act = paneActivity.get(t.id);
    const paneState: PaneState = resolveState(act?.state, Boolean(unseenTermOutput[t.id]));
    return (
      <TerminalPane
        info={t}
        focused={o.focused}
        parked={o.parked}
        reserveViewToggle={o.reserveViewToggle}
        paneName={paneOverrides[t.id]?.name ?? act?.name}
        paneState={paneState}
        agentCli={paneOverrides[t.id]?.cli ?? act?.agent_cli}
        paneStateLine={act?.state_line}
        onRenamed={(name) => notePaneOverride(t.id, { name: name || undefined })}
        onLaunched={(cli) => notePaneOverride(t.id, { cli })}
        onLaunchWithCapabilities={(cli) => void addTerminal(t.cwd, cli)}
        onOpenedPane={adoptPane}
        liveAccounts={livePaneAccounts(t, act)}
        paneLimit={paneLimitOf(act)}
        paneResumeFailed={resumeFailedOf(act)}
        paneSignInNeeded={signInNeededOf(act)}
        paneSignedIn={signedInOf(act)}
        onFocus={() => bringToFront(t.id)}
        onClose={o.onClose}
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
    );
  };

  /** What a Build session's terminal tab borrows: the live list, the page's
   *  own add path, and the body above. */
  const sessionTerminal: SessionTerminalApi = {
    find: (id) => terminals.find((t) => t.id === id && t.alive !== false) ?? null,
    open: (cwd) => addTerminal(cwd),
    // The pane's × asks first in the tab itself (HarnessPanes), like the
    // rail's own close dialog — never a native prompt.
    render: (info, { parked, onClose }) =>
      renderTerminalPane(info, { focused: !parked, parked, reserveViewToggle: false, onClose }),
    close: (id) => closeTerminal(id),
  };

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Build"
          // v1.369.0: the two views the switch beside it names.
          subtitle={
            shape === "harness"
              ? `${CODING_VIEW_NAME} work in a folder and keep going when you leave. Start one, or open a terminal from the list.`
              : "Live terminals on this PC. Press New terminal to open one, or pick a project folder and open a terminal in it."
          }
          // v1.370.2: the switch sits in the header's exact MIDDLE and does not
          // move between the two views ("centered and fixed in both modes");
          // the Terminals view's own controls take the right column.
          center={
            // THE SWITCH (v1.369.0): Terminals | Coding sessions (harness
            // gaps, fix 4: the view's everyday name, lib/buildWords — the
            // value "harness" and the test id stay). Plain text tabs like the
            // side panel's, the open one in ink.
            <div
              role="tablist"
              aria-label="Build view"
              data-testid="build-view-switch"
              className="flex items-center gap-1 rounded-lg bg-white/[0.04] p-0.5"
            >
              {(
                [
                  { value: "rail", label: "Terminals", icon: <SquareTerminal size={13} aria-hidden /> },
                  { value: "harness", label: CODING_VIEW_NAME, icon: <Bot size={13} aria-hidden /> },
                ] as const
              ).map((v) => {
                const selected = shape === v.value;
                return (
                  <button
                    key={v.value}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    tabIndex={selected ? 0 : -1}
                    data-testid={v.value === "rail" ? "build-view-terminals" : "build-view-harness"}
                    onClick={() => chooseShape(v.value)}
                    onKeyDown={(e) => {
                      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                      e.preventDefault();
                      const next = v.value === "rail" ? "harness" : "rail";
                      chooseShape(next);
                      const sel = next === "rail" ? "build-view-terminals" : "build-view-harness";
                      document.querySelector<HTMLButtonElement>(`[data-testid="${sel}"]`)?.focus();
                    }}
                    className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
                      selected ? "bg-white/[0.08] font-medium text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
                    }`}
                  >
                    {v.icon}
                    {v.label}
                  </button>
                );
              })}
            </div>
          }
          actions={
            shape === "rail" ? (
                <>
                  {/* v1.329.0 (calm chat wave 8): a quiet sentence-case label and a
                      borderless select that fills on hover, like the rail's own
                      footer, not a boxed field under an uppercase caption.
                      v1.370.2: below xl the WORD hides (a screen reader still
                      reads it) so the controls fit beside the centred switch
                      at 1024 px instead of wrapping onto a second line. */}
                  <label className="flex items-center gap-1.5 text-[13px] text-zinc-500">
                    <SquareTerminal size={14} className="text-zinc-500" />
                    <span className="sr-only xl:not-sr-only">Shell</span>
                  </label>
                  <select
                    aria-label="Shell"
                    data-testid="header-shell"
                    value={shell}
                    onChange={(e) => setShell(e.target.value)}
                    disabled={shells.length === 0}
                    className="h-8 w-auto cursor-pointer rounded-lg border-0 bg-transparent pl-2 pr-1 text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {shells.length === 0 && <option value="">default</option>}
                    {shells.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                  {/* v1.329.0 (calm chat): a quiet ghost, not a glowing accent
                      slab; wave 8 dropped its hairline box too, so it reads like
                      the rail's own "New terminal" and fills only on hover. */}
                  <button
                    type="button"
                    data-testid="header-new-terminal"
                    onClick={() => addTerminal(newFolder)}
                    disabled={busy}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {busy ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Plus size={14} />
                    )}
                    New terminal
                  </button>
                </>
              ) : null
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint detail="Terminals and the directory tree both need it running." />
        </Reveal>
      )}
      {/* v1.329.0 (calm chat): one plain line in the danger tone, never a
          red bordered card. The daemon's own words carry the path. */}
      {error && (
        <Reveal>
          <p
            role="alert"
            data-testid="build-error"
            className="break-words text-[13px] text-tone-danger"
          >
            {error}
          </p>
        </Reveal>
      )}

      {shape === "harness" ? (
        <Reveal>
          <IjHarnessView
            initialSession={linkSession}
            nightLink={linkNight}
            terminals={railPanes}
            onOpenTerminal={openInTerminals}
            defaultFolder={newFolder}
            terminal={sessionTerminal}
          />
        </Reveal>
      ) : (
      <Reveal>
        <div className="flex flex-col gap-5 lg:flex-row">
          {/* THE RAIL (v1.218.0) — Build's spine. Every pane in one column with
              its live state; clicking one brings it into focus in the workspace
              beside it. */}
          {/* v1.315.0 (phone): the rail's full height applied at EVERY width,
              so at 390 px the first screen was an empty PANES column and the
              stage sat a screen below. Below lg the rail is now capped
              (max-h-56; PaneRail's own list scrolls inside it); the full
              height — minus the demo strip, like every full-height module —
              is lg-only. Layout only: no terminal behaviour changes. */}
          {!loading && (
            <div className="flex w-full max-h-56 shrink-0 flex-col lg:w-56 lg:max-h-none lg:h-[calc(100vh-12rem-var(--ij-strip-h,0px))] lg:min-h-[480px] xl:w-64">
              <PaneRail
                panes={railPanes}
                focusedId={activeId}
                onFocus={bringToFront}
                onClose={(id) => setPendingClose(id)}
                onRename={renamePane}
                onNew={() => addTerminal(newFolder)}
                busy={busy}
              />
            </div>
          )}

          {/* The workspace: one box that every pane fills, with the focused
              one visible. */}
          <div className="min-w-0 flex-1">
            {loading ? (
              <Card>
                <Spinner label="Attaching to sessions…" />
              </Card>
            ) : (
              <div
                data-testid="build-stage"
                className="relative w-full overflow-hidden rounded-2xl border border-white/[0.06] bg-ink-900/40"
                // v1.315.0: subtracts the demo strip like the rail beside it
                // (they must end on the same line at lg); 480 px floor kept
                // at every width.
                style={{ height: "calc(100vh - 12rem - var(--ij-strip-h, 0px))", minHeight: 480 }}
              >
                {/* v1.313.0: the stage is a recessed THEME panel (ink-900), not
                    bg-black — black is the one neutral the themes do not remap,
                    so on Daylight it was a grey slab. The panes' own terminal
                    colours are untouched. */}
                {terminals.length === 0 ? (
                  // v1.314.0 (UX wave 2): the biggest thing on a fresh Build
                  // page was this stage holding one grey line and no button.
                  // It now teaches and offers the SAME door as the header —
                  // addTerminal(newFolder), the one code path. The phrase
                  // "No terminals yet" is kept (a review test matches it).
                  <div className="grid h-full place-items-center px-4">
                    <Empty
                      icon={<SquareTerminal size={26} />}
                      title="No terminals yet"
                      action={{ label: "New terminal", onClick: () => void addTerminal(newFolder), disabled: busy }}
                    >
                      {newFolder
                        ? `A terminal is a command line on this PC. A new one opens in ${newFolder}.`
                        : "A terminal is a command line on this PC. Open one here, or pick a project folder first to start in it."}
                    </Empty>
                  </div>
                ) : (
                  terminals.map((t) => {
                    // A pane with no cwd can't ground a chat — force terminal
                    // view even if a stale "chat" key survives in storage.
                    const view: PaneView = t.cwd ? (views[t.id] ?? "terminal") : "terminal";
                    // Toggle badges (v1.212.0): the HIDDEN layer's progress,
                    // painted on the button that would reveal it. Chat side
                    // only while the chat is hidden — an approval waiting
                    // OUTRANKS mere streaming (it blocks the turn for up to
                    // 180s and needs the user). Terminal side only while the
                    // chat is showing and unseen output arrived.
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
                        Date.now() - (termPeekAt.current[t.id] ?? peek.at) <
                          TERM_PEEK_QUIET_MS)
                        ? peek.lastLine
                        : null;
                    // RAIL: every pane fills the SAME box and only the focused
                    // one is visible. Not `display:none` and not an unmount —
                    // both are forbidden here for the reason v1.190.0 records:
                    // a terminal whose holder has no size wraps its replay into
                    // a default-sized buffer that no later fit can re-wrap, so
                    // a hidden pane must keep a real box. `visibility` also
                    // takes the hidden panes out of hit-testing and focus, so
                    // they cannot sit invisibly over the live one.
                    // v1.248.0: the hidden panes' TERMINALS also park in the
                    // paneHost lot (TerminalPane `parked`), so only the focused
                    // one renders or holds a GPU context; this box keeps its
                    // size, so an unpark fits to the real width.
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
                            {renderTerminalPane(t, {
                              focused: focusedId === t.id,
                              parked: activeId !== t.id,
                              reserveViewToggle: true,
                              onClose: () => setPendingClose(t.id),
                            })}
                          </div>

                          {/* Chat layer — same box, chat grounded in this
                              pane's folder. The pane (not the view) stays the
                              unit of focus, so the Files tab keeps following
                              this cwd. SYMMETRIC with the terminal layer:
                              mounted LAZILY on the first flip to chat, then
                              NEVER unmounted — only hidden via visibility
                              (never display:none, never an unmount). PaneChat
                              owns an in-flight turn's stream and saves;
                              unmounting it mid-turn leaves the turn finishing
                              in a dead closure whose late last-write-wins save
                              can erase a newer turn, or orphans the first
                              exchange if a remount races the CREATE save.
                              visibility:hidden also drops the hidden layer out
                              of hit-testing, focus, and scrolling, so it can't
                              sit invisibly over the terminal and steal input. */}
                          {(view === "chat" || chatOpened[t.id]) && (
                            <div
                              data-testid={`chat-layer-${t.id}`}
                              style={{ visibility: view === "chat" ? "visible" : "hidden" }}
                              className={`absolute inset-0 z-10 flex flex-col overflow-hidden rounded-2xl border bg-ink-900 transition-colors ${paneFrameClass(focusedId === t.id)}`}
                            >
                              <header
                                className={`flex shrink-0 items-center gap-2 border-b border-white/[0.06] bg-ink-900/60 px-3 py-2`}
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
                                {/* The Terminal ⇄ Chat toggle sits over this
                                    slot, in the header row (v1.329.0), never
                                    over the conversation below. */}
                                <span
                                  aria-hidden="true"
                                  data-testid="pane-toggle-slot"
                                  className={PANE_TOGGLE_SLOT}
                                />
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setPendingClose(t.id);
                                  }}
                                  title="Close terminal"
                                  className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-tone-danger/15 hover:text-tone-danger"
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

                          {/* Terminal ⇄ Chat toggle. v1.329.0: it sits IN the
                              header row, over the empty slot both headers keep
                              just left of their close button
                              (components/terminal/paneViewToggle.ts). Below the
                              header it covered the side chat's right-aligned
                              bubble. ONE element of buttons, so the focus stays
                              on it when the view flips. */}
                          <div
                            data-testid={`pane-view-toggle-${t.id}`}
                            className={`${PANE_TOGGLE_PLACE} z-20 flex items-center gap-0.5 rounded-lg p-0.5`}
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
                                  ? `Terminal view for ${t.id} — New terminal output`
                                  : `Terminal view for ${t.id}`
                              }
                              aria-pressed={view === "terminal"}
                              className={`relative grid h-5 w-5 place-items-center rounded-md transition-colors ${
                                view === "terminal"
                                  ? "bg-accent/15 text-accent"
                                  : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
                              }`}
                            >
                              <SquareTerminal size={12} />
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
                                    : "Chat view — talk to an agent grounded in this terminal's folder"
                              }
                              aria-label={
                                chatBadgeText
                                  ? `Chat view for ${t.id} — ${chatBadgeText}`
                                  : `Chat view for ${t.id}`
                              }
                              aria-pressed={view === "chat"}
                              className={`relative grid h-5 w-5 place-items-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                                view === "chat"
                                  ? "bg-accent/15 text-accent"
                                  : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
                              }`}
                            >
                              <MessageSquare size={12} />
                              {/* Amber pulse = an approval is WAITING; accent
                                  pulse = the chat is streaming a turn. */}
                              {chatBadge ? (
                                <span
                                  data-testid={`pane-chat-badge-${t.id}`}
                                  aria-hidden
                                  className={`absolute -right-0.5 -top-0.5 h-1.5 w-1.5 animate-pulse rounded-full ${
                                    chatBadge === "approval"
                                      ? "bg-tone-warn"
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
                              above). Renders ONLY with something true to say;
                              clicking flips to the view doing the talking. */}
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
                                  ? // v1.329.0: an opaque theme surface with the
                                    // warn ink, not a see-through tint. The strip
                                    // lies over the terminal canvas, which stays
                                    // dark in every theme; a tint over it read
                                    // dark-on-dark in Daylight.
                                    "border-tone-warn/40 bg-ink-900/85 text-tone-warn hover:bg-tone-warn/[0.12]"
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
                                  {/* v1.356.0 (G80): the bordered look, on
                                      purpose — this confirm card is a
                                      deliberately boxed dialog beside a
                                      bordered Cancel (calm is the default). */}
                                  <ConfirmButton
                                    variant="bordered"
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
                      </div>
                    );
                  })
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
            <div className="lg:sticky lg:top-0 lg:h-[calc(100vh-9rem-var(--ij-strip-h,0px))]">
              {treeCollapsed ? (
                <button
                  type="button"
                  onClick={() => changeTreeCollapsed(false)}
                  title="Show panel"
                  aria-label="Show panel"
                  className="flex w-full items-center justify-center gap-2 rounded-[10px] py-2 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 lg:h-full lg:flex-col lg:justify-start lg:py-3"
                >
                  <PanelLeftOpen size={16} />
                  <span className="lg:hidden">Show panel</span>
                </button>
              ) : (
                <div className="flex h-full flex-col gap-2">
                  {/* Folders (picker) / Files (live folder contents).
                      v1.329.0 (calm chat): plain text tabs like the chat top
                      bar's, the open one in ink and the other muted; no box,
                      no accent chip. Arrow keys move between them. */}
                  <div className="flex shrink-0 items-center gap-2 px-1">
                    <div
                      role="tablist"
                      aria-label="Folders and files"
                      data-testid="build-panel-tabs"
                      className="flex min-w-0 flex-1 items-center gap-2"
                    >
                      {BUILD_PANEL_TABS.map((tab) => {
                        const selected = treeTab === tab.value;
                        return (
                          <button
                            key={tab.value}
                            type="button"
                            role="tab"
                            id={`build-tab-${tab.value}`}
                            aria-selected={selected}
                            aria-controls="build-panel-body"
                            tabIndex={selected ? 0 : -1}
                            onClick={() => chooseTab(tab.value)}
                            onKeyDown={(e) => {
                              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                              e.preventDefault();
                              const tabs = Array.from(
                                e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
                                  '[role="tab"]',
                                ) ?? [],
                              );
                              const i = tabs.indexOf(e.currentTarget);
                              const step = e.key === "ArrowRight" ? 1 : -1;
                              tabs[(i + step + tabs.length) % tabs.length]?.focus();
                            }}
                            className={`rounded-md px-1.5 py-1 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
                              selected
                                ? "font-medium text-zinc-100"
                                : "text-zinc-500 hover:text-zinc-200"
                            }`}
                          >
                            {tab.label}
                          </button>
                        );
                      })}
                    </div>
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

                  <div
                    id="build-panel-body"
                    role="tabpanel"
                    aria-labelledby={`build-tab-${treeTab}`}
                    className="min-h-0 flex-1 px-2"
                  >
                    {/* v1.329.0 (calm chat wave 6): the CALM picker and file
                        list, the same look the chat's project drawer draws,
                        so one file browser has one look across the app. No
                        card; the terminal stage beside it stays dark. */}
                    {treeTab === "folders" ? (
                      <DirectoryTree
                        variant="calm"
                        selectedPath={selectedPath}
                        onSelect={pickFolder}
                        onOpenTerminal={(p) => addTerminal(p)}
                        onCollapse={() => changeTreeCollapsed(true)}
                        // v1.315.0: the tab bar above already has THE collapse
                        // button, and Build opens on the user's projects.
                        hideHeaderCollapse
                        showProjects
                      />
                    ) : (
                      <FilesPanel
                        variant="calm"
                        folder={focusedFolder}
                        onOpenTerminal={(p) => addTerminal(p)}
                        noFolderText="Focus a terminal to see the files in its folder, or pick a folder in the Folders tab."
                      />
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </Reveal>
      )}
    </PageShell>
  );
}
