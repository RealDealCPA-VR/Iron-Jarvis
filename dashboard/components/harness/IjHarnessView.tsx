"use client";

/**
 * The IJ HARNESS view (v1.369.0, plan Phase 6) — the screen for the Phase 4
 * session backend: the left pane (New session, sessions grouped by folder,
 * the live Terminals), the session in the middle (HarnessSession) and its
 * panes on the right (HarnessPanes). v1.406.0 (part 1): it is the chat
 * page's CODE view (components/harness/CodeView, lib/chatSurface); Build
 * keeps only the terminals.
 *
 * The open session lives in the URL (`/chat?view=code&session=<id>` — the
 * stable address the bell, the desktop toast and the Activity rows open,
 * lib/buildLinks) and in this window's memory, so the view comes back to
 * it. The list is polled only while the window is visible (no event fires
 * when a session changes). v1.406.0: the list itself goes into the app
 * sidebar's slot when one is given (`listSlot`) — where the chat page's own
 * list goes in the Chat view — else it is this view's left pane.
 *
 * v1.406.0 part 7: TWO SESSIONS SIDE BY SIDE (lib/codeSplit). A row's "Open
 * beside" (or Shift+Enter, or dragging the row to the right edge) opens a
 * second session in a column beside the first (`&beside=<id>` in the
 * address). Each column is a whole HarnessSession — its own box, cards,
 * Stop, plan and receipts — with a small header (its name, ×). The panes on
 * the right are the FOCUSED column's (one shared column: at 1440 px two
 * sets of panes would leave each conversation ~250 px), and its header says
 * whose they are. Keys belong to the focused column only. Below
 * `SPLIT_MIN_WIDTH` a split shows ONE column with a tab per session; the
 * other stays mounted (hidden + inert) so its reply streams on.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { createPortal } from "react-dom";
import { Columns2, MessageSquarePlus, X } from "lucide-react";
import { HarnessSidebar, type StartExtra } from "@/components/harness/HarnessSidebar";
import { HarnessSession } from "@/components/harness/HarnessSession";
import { TonightList } from "@/components/harness/TonightList";
import { LastNightList } from "@/components/harness/LastNightList";
import { choosePaneNext, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import type { RailPane } from "@/components/terminal/PaneRail";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { BUILD_SESSION_EVENT, sessionOfEvent } from "@/lib/buildLinks";
import type { BranchDraft } from "@/lib/buildNext";
import { useHarnessKeys } from "@/lib/harnessKeys";
import { cachedFolders, dropEndedTurns, keepFolders, viewFor } from "@/lib/turnCache";
import {
  ONE,
  SESSION_DRAG_TYPE,
  SPLIT_COLUMN_ATTR,
  SPLIT_QUERY,
  closeSide,
  dropSession,
  focusSide,
  focusedSession,
  keepSplit,
  openBeside,
  openSession,
  rememberedSplit,
  splitFromSearch,
  writeSplitToAddress,
  type CodeSplit,
  type SplitSide,
} from "@/lib/codeSplit";
import {
  LAST_SESSION_KEY,
  createSession,
  listSessionsWithRoot,
  renameFolder,
  type BuildFolder,
} from "@/lib/buildSessions";

/** How often the session list is read while the window is visible. */
export const SESSION_LIST_POLL_MS = 4000;

/** v1.406.0 part 7: the words over the shared pane column. */
export const PANES_FOR_PREFIX = "Views for";

function readLastSession(): string | null {
  try {
    return window.localStorage.getItem(LAST_SESSION_KEY);
  } catch {
    return null;
  }
}

function writeLastSession(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(LAST_SESSION_KEY, id);
    else window.localStorage.removeItem(LAST_SESSION_KEY);
  } catch {
    /* the URL still holds it */
  }
}

/** Does the window have room for two columns? (No `matchMedia`: assume so.) */
function wideNow(): boolean {
  try {
    return typeof window === "undefined" || !window.matchMedia ? true : window.matchMedia(SPLIT_QUERY).matches;
  } catch {
    return true;
  }
}

/** v1.406.0 part 7: is the window wide enough for two columns? */
function useSplitRoom(): boolean {
  const [wide, setWide] = useState(wideNow);
  useEffect(() => {
    let mq: MediaQueryList | null = null;
    try {
      mq = window.matchMedia ? window.matchMedia(SPLIT_QUERY) : null;
    } catch {
      mq = null;
    }
    if (!mq) return;
    const on = () => setWide(mq!.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq!.removeEventListener?.("change", on);
  }, []);
  return wide;
}

/** The split a first frame starts from: the address (its `beside` only with
 *  the session the page was opened with), else this window's split, else
 *  the session this window had open. */
function startingSplit(initialSession: string | null): CodeSplit {
  if (initialSession) {
    let fromAddress: CodeSplit | null = null;
    try {
      fromAddress = splitFromSearch(window.location.search);
    } catch {
      fromAddress = null;
    }
    const right = fromAddress && fromAddress.left === initialSession ? fromAddress.right : null;
    return right && right !== initialSession
      ? { left: initialSession, right, focus: "left" }
      : { left: initialSession, right: null, focus: "left" };
  }
  return rememberedSplit() ?? { left: readLastSession(), right: null, focus: "left" };
}

export function IjHarnessView({
  initialSession,
  nightLink = null,
  terminals,
  onOpenTerminal,
  defaultFolder,
  terminal,
  listSlot = null,
  onListNavigate,
}: {
  /** `?session=<id>` the page was opened with. */
  initialSession: string | null;
  /** v1.399.0 (night shift N2): a link to Last night (`?night=<date>`, the
   *  bell's row, the tray notice) — `n` counts the presses, so a second press
   *  of the same link opens it again. */
  nightLink?: { night: string; n: number } | null;
  terminals: RailPane[];
  /** Open a terminal in the Terminals view (the page's own focus path). */
  onOpenTerminal: (id: string) => void;
  /** Where a new session starts when nothing else is picked. */
  defaultFolder: string | null;
  terminal: SessionTerminalApi;
  /** v1.406.0: the app sidebar's slot (lib/sidebarSlot) — the session list
   *  is portaled there while one is given, else drawn in the view. */
  listSlot?: HTMLElement | null;
  /** A row in the slotted list was followed (the phone drawer closes). */
  onListNavigate?: () => void;
}) {
  // v1.384.0: a RETURN to Build paints the list and the session this window
  // had open at once (lib/turnCache — this window's own memory of them),
  // then reads both again. A first visit waits for the list, as before.
  const [initial] = useState(() => {
    const kept = cachedFolders<{ folders: BuildFolder[]; workRoot: string }>();
    const start = startingSplit(initialSession);
    const wanted = start.left;
    const isKnown = (id: string | null) =>
      !!id && (viewFor(id) !== null || !!kept?.folders.some((f) => f.sessions.some((s) => s.id === id)));
    const known = isKnown(wanted);
    // v1.406.0 part 7: the column beside comes back with the first one (from
    // the address always; from this window's memory while it is known).
    const right = known && start.right && (initialSession || isKnown(start.right)) ? start.right : null;
    const split: CodeSplit = known ? { left: wanted, right, focus: right ? start.focus : "left" } : ONE;
    return { kept, split, start };
  });
  const [folders, setFolders] = useState<BuildFolder[] | null>(initial.kept?.folders ?? null);
  // v1.379.0: where a session with no folder keeps its files (the list says).
  const [workRoot, setWorkRoot] = useState(initial.kept?.workRoot ?? "");
  const [listError, setListError] = useState<string | null>(null);
  // v1.406.0 part 7: the open session(s) — one column, or two side by side.
  const [split, setSplit] = useState<CodeSplit>(initial.split);
  const selected = split.left;
  const front = focusedSession(split);
  const two = !!split.right;
  const wide = useSplitRoom();
  // The first pick (the address, else the session this window had open) is
  // made when the list answers. Until then `selected` is only "not picked
  // yet" and must not be written back: the write ran BEFORE the pick and
  // erased the remembered session, so Build came back to the newest session
  // instead of the one it had open (v1.369.0 review). v1.384.0: a session
  // this window already knows is the pick at once.
  const pickedRef = useRef(initial.split.left !== null);
  const open = useCallback((id: string) => {
    pickedRef.current = true;
    setSplit((s) => openSession(s, id));
  }, []);
  // v1.398.0 (night shift N1): Tonight in the middle instead of a session
  // (this window's memory only; picking a session leaves it). v1.399.0 (N2):
  // or Last night — opened by its row, or by a link to it.
  const [middle, setMiddle] = useState<"session" | "tonight" | "night">(nightLink ? "night" : "session");
  const tonight = middle === "tonight";
  const lastNight = middle === "night";
  const nightPress = nightLink?.n ?? 0;
  useEffect(() => {
    if (nightPress > 0) setMiddle("night");
  }, [nightPress]);
  function openFromNight(id: string, opts?: { changes?: boolean }) {
    // "Changes" opens the task's session on its Changes pane (a live
    // branch's Branch view: the branch's whole diff).
    if (opts?.changes) choosePaneNext("changes");
    setMiddle("session");
    open(id);
    void refresh();
  }

  const refresh = useCallback(async () => {
    try {
      const { folders: list, workRoot: root } = await listSessionsWithRoot();
      setFolders(list);
      setWorkRoot(root);
      setListError(null);
      keepFolders({ folders: list, workRoot: root });
      // v1.384.0 review: a kept reply of a session that no longer answers is
      // dropped now, not only when that session is opened again.
      dropEndedTurns(new Set(list.flatMap((f) => f.sessions.filter((s) => s.running).map((s) => s.id))));
      return list;
    } catch (e) {
      setListError(
        e && typeof e === "object" && "status" in e && (e as { status?: number }).status === 404
          ? "This version of Iron Jarvis has no coding sessions yet."
          : e instanceof Error
            ? e.message
            : String(e),
      );
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh().then((list) => {
      // A session started before the list answered is already the pick.
      if (pickedRef.current) return;
      const { start } = initial;
      const wanted = start.left;
      const listed = (id: string | null) => !!id && !!list?.some((f) => f.sessions.some((s) => s.id === id));
      // A session from the URL is opened even when the list does not have it
      // yet (it may be archived); a remembered one only while it is listed.
      const pick = initialSession ?? (listed(wanted) ? wanted : null) ?? list?.[0]?.sessions[0]?.id ?? null;
      const right =
        pick && start.left === pick && start.right && (initialSession || listed(start.right)) ? start.right : null;
      pickedRef.current = true;
      setSplit(pick ? { left: pick, right, focus: right ? start.focus : "left" } : ONE);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useVisibleInterval(() => void refresh(), SESSION_LIST_POLL_MS);

  // A link to a session pressed while this view is showing (the bell): the
  // page keeps its state on that navigation, so the link announces the
  // session and this view opens it (lib/buildLinks, v1.369.0 review). A
  // session already in one of the two columns is FOCUSED (part 7).
  useEffect(() => {
    const onLink = (e: Event) => {
      const id = sessionOfEvent(e);
      if (!id) return;
      // v1.399.0: the session comes to the front (Tonight / Last night step aside).
      setMiddle("session");
      open(id);
      void refresh();
    };
    window.addEventListener(BUILD_SESSION_EVENT, onLink);
    return () => window.removeEventListener(BUILD_SESSION_EVENT, onLink);
  }, [refresh, open]);

  useEffect(() => {
    if (!pickedRef.current) return;
    writeLastSession(selected);
    keepSplit(split);
    // v1.399.0: Last night in front keeps its own address (a reload opens it).
    if (middle === "night") return;
    writeSplitToAddress(split);
  }, [split, selected, middle]);

  const runningIds = useMemo(
    () => new Set((folders ?? []).flatMap((f) => f.sessions.filter((s) => s.running).map((s) => s.id))),
    [folders],
  );
  // v1.406.0 part 7: a session waiting on the user (its tab or header says
  // so, even while it is the hidden tab of a narrow window).
  const waitingIds = useMemo(
    () => new Set((folders ?? []).flatMap((f) => f.sessions.filter((s) => s.waiting).map((s) => s.id))),
    [folders],
  );
  const titleOf = useCallback(
    (id: string) => {
      for (const f of folders ?? []) {
        const s = f.sessions.find((x) => x.id === id);
        if (s) return s.title || "Session";
      }
      return "Session";
    },
    [folders],
  );
  // v1.378.0 (part 2, Phase 7, the walkthrough): New session opens on the
  // folder of the session in front — its group's folder, the one the user
  // picked (a session on its own branch is grouped there too). The Terminals
  // view's folder was the default before, so a second session in the same
  // folder meant pasting the folder again. No session open: as before.
  // v1.379.0 review (the merge with v1.378.0): the session in front has NO
  // folder — New session opens the same way, with no folder (the box empty,
  // the folder list closed: Start makes another session with no folder); it
  // never falls back to the Terminals view's folder. v1.406.0 part 7: "in
  // front" is the FOCUSED column's session.
  const frontGroup = useMemo(
    () => folders?.find((f) => f.sessions.some((s) => s.id === front)) ?? null,
    [folders, front],
  );
  const sessionFolder = frontGroup ? (frontGroup.noFolder ? "" : frontGroup.path) : null;
  const frontHasNoFolder = frontGroup?.noFolder === true;

  // v1.377.0 (part 2, Phase 6): a session just BRANCHED from a question opens
  // with that question in its box (handed over once, for that session only).
  const [draftFor, setDraftFor] = useState<{ id: string; draft: BranchDraft } | null>(null);
  function openBranched(side: SplitSide, id: string, draft: BranchDraft | null) {
    pickedRef.current = true;
    setDraftFor(draft ? { id, draft } : null);
    // The branch opens in the column it was made from.
    setSplit((s) => openSession(focusSide(s, side), id));
    void refresh();
  }
  // Ctrl+N (lib/harnessKeys): New session in the left pane.
  const [newRequest, setNewRequest] = useState(0);
  // v1.379.0: "/resume" — the caret in the session search.
  const [searchRequest, setSearchRequest] = useState(0);
  useHarnessKeys({ onNew: () => setNewRequest((n) => n + 1) }, () => ({ busy: false, asking: false }));

  async function start(folder: string, extra?: StartExtra): Promise<string | null> {
    try {
      // v1.375.0: New session's branch switch rides only when it was shown.
      const made = await (extra ? createSession(folder, { ...extra }) : createSession(folder));
      if (!made) return "The session could not be started.";
      setMiddle("session");
      open(made.id);
      void refresh();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  }

  async function rename(path: string, name: string): Promise<string | null> {
    try {
      await renameFolder(path, name);
      void refresh();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  }

  const beside = useCallback((id: string) => {
    pickedRef.current = true;
    setMiddle("session");
    setSplit((s) => openBeside(s, id));
  }, []);

  // ---- drag a session row to the right edge (v1.406.0 part 7) ----
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    const carries = (e: globalThis.DragEvent) => {
      try {
        return !!e.dataTransfer && Array.from(e.dataTransfer.types ?? []).includes(SESSION_DRAG_TYPE);
      } catch {
        return false;
      }
    };
    const onStart = (e: globalThis.DragEvent) => {
      if (carries(e)) setDragging(true);
    };
    const onEnd = () => setDragging(false);
    document.addEventListener("dragstart", onStart);
    document.addEventListener("dragend", onEnd);
    document.addEventListener("drop", onEnd);
    return () => {
      document.removeEventListener("dragstart", onStart);
      document.removeEventListener("dragend", onEnd);
      document.removeEventListener("drop", onEnd);
    };
  }, []);
  function onDropBeside(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    let id = "";
    try {
      id = e.dataTransfer.getData(SESSION_DRAG_TYPE).trim();
    } catch {
      id = "";
    }
    if (id) beside(id);
  }

  // ---- the shared pane column of a split ----
  const [paneSlot, setPaneSlot] = useState<HTMLDivElement | null>(null);

  const sidebar = (
    <HarnessSidebar
      folders={folders}
      listError={listError}
      selectedId={middle === "session" ? front : null}
      besideId={middle === "session" && two ? (front === split.left ? split.right : split.left) : null}
      onSelect={(id) => {
        setMiddle("session");
        open(id);
        if (listSlot) onListNavigate?.();
      }}
      onOpenBeside={(id) => {
        beside(id);
        if (listSlot) onListNavigate?.();
      }}
      tonightOpen={tonight}
      onTonight={() => {
        setMiddle("tonight");
        if (listSlot) onListNavigate?.();
      }}
      lastNightOpen={lastNight}
      onLastNight={() => {
        setMiddle("night");
        if (listSlot) onListNavigate?.();
      }}
      onStart={start}
      onRename={rename}
      defaultFolder={sessionFolder ?? defaultFolder}
      startWithNoFolder={frontHasNoFolder}
      terminals={terminals}
      onOpenTerminal={onOpenTerminal}
      newRequest={newRequest}
      searchRequest={searchRequest}
      workRoot={workRoot}
    />
  );

  const columns: { side: SplitSide; id: string }[] = [];
  if (split.left) columns.push({ side: "left", id: split.left });
  if (split.right) columns.push({ side: "right", id: split.right });
  const tabs = two && !wide;

  return (
    <div
      data-testid="ij-harness-view"
      className="relative flex flex-col gap-4 xl:h-[calc(100vh-4.5rem-var(--ij-strip-h,0px)-var(--ij-switch-h,0px))] xl:min-h-[480px] xl:flex-row"
    >
      {/* v1.379.0: below xl the left pane is capped, and it SCROLLS — New
          session with its folder list open is taller than the cap and was
          drawn over the session below it at phone width (measured, 390 px). */}
      {listSlot ? (
        createPortal(sidebar, listSlot)
      ) : (
        <aside
          data-testid="harness-list-aside"
          className="flex max-h-[22rem] w-full shrink-0 flex-col overflow-y-auto xl:max-h-none xl:w-56 xl:overflow-visible 2xl:w-64"
        >
          {sidebar}
        </aside>
      )}
      <div className="min-w-0 flex-1 xl:min-h-0">
        {tonight ? (
          <div className="h-full overflow-y-auto">
            <TonightList
              folders={(folders ?? []).filter((f) => !f.noFolder && f.path).map((f) => f.path)}
              onOpenSession={(id) => {
                setMiddle("session");
                open(id);
                void refresh();
              }}
            />
          </div>
        ) : lastNight ? (
          <div className="h-full overflow-y-auto">
            <LastNightList onOpenSession={openFromNight} focusNight={nightLink?.night ?? null} />
          </div>
        ) : selected ? (
          <div
            data-testid="harness-columns"
            data-split={two ? (tabs ? "tabs" : "columns") : "one"}
            className={two && !tabs ? "flex h-full min-h-0 min-w-0 gap-3" : "flex h-full min-h-0 min-w-0 flex-col gap-2"}
          >
            {tabs ? (
              <SplitTabs
                split={split}
                titleOf={titleOf}
                dotOf={(id) => (waitingIds.has(id) ? "waiting" : runningIds.has(id) ? "running" : "quiet")}
                onFocus={(side) => setSplit((s) => focusSide(s, side))}
                onClose={(side) => setSplit((s) => closeSide(s, side))}
              />
            ) : null}
            {columns.map(({ side, id }) => {
              const focused = split.focus === side;
              const hiddenTab = tabs && !focused;
              const colProps = two
                ? { [SPLIT_COLUMN_ATTR]: side, "data-focused": focused ? "true" : "false" }
                : {};
              return (
                <div
                  key={id}
                  {...colProps}
                  data-testid={two ? `harness-column-${side}` : "harness-column"}
                  data-session-id={id}
                  hidden={hiddenTab}
                  inert={hiddenTab}
                  onPointerDownCapture={two ? () => setSplit((s) => focusSide(s, side)) : undefined}
                  onFocusCapture={two ? () => setSplit((s) => focusSide(s, side)) : undefined}
                  className={
                    two && !tabs
                      ? `flex min-h-0 min-w-0 flex-1 flex-col border-t-2 pt-1.5 ${focused ? "border-accent/60" : "border-transparent"}`
                      : "h-full min-h-0 min-w-0"
                  }
                >
                  {two && !tabs ? (
                    <ColumnHead
                      side={side}
                      title={titleOf(id)}
                      running={runningIds.has(id)}
                      waiting={waitingIds.has(id)}
                      focused={focused}
                      onFocus={() => setSplit((s) => focusSide(s, side))}
                      onClose={() => setSplit((s) => closeSide(s, side))}
                    />
                  ) : null}
                  <div className={two && !tabs ? "min-h-0 flex-1" : "h-full min-h-0"}>
                    <HarnessSession
                      threadId={id}
                      terminal={terminal}
                      listRunning={runningIds.has(id)}
                      onChanged={() => void refresh()}
                      onGone={() => {
                        setSplit((s) => dropSession(s, id));
                        void refresh();
                      }}
                      onBranched={(next, draft) => openBranched(side, next, draft)}
                      initialDraft={draftFor && draftFor.id === id ? draftFor.draft : null}
                      onOpenSession={(next) => {
                        pickedRef.current = true;
                        setSplit((s) => openSession(focusSide(s, side), next));
                      }}
                      // v1.379.0: "/clear" starts a new session on the same folder (or
                      // with no folder), "/resume" puts the caret in the search.
                      onNewSession={(folder) => start(folder)}
                      onFindSessions={() => setSearchRequest((n) => n + 1)}
                      onScreen={!hiddenTab}
                      panesInto={two && !tabs ? (focused ? paneSlot : null) : hiddenTab ? null : undefined}
                    />
                  </div>
                </div>
              );
            })}
            {two && !tabs ? (
              <aside
                data-testid="harness-split-panes"
                className="flex min-h-0 w-[300px] shrink-0 flex-col gap-1.5 2xl:w-[360px]"
              >
                <p
                  data-testid="harness-split-panes-for"
                  className="flex min-w-0 items-center gap-1.5 px-1 text-[12px] text-zinc-500"
                  title="The plan, changes and terminal on the right belong to the session with the focus. Click the other column to see its own."
                >
                  <Columns2 size={12} aria-hidden className="shrink-0 text-accent-soft" />
                  <span className="shrink-0">{PANES_FOR_PREFIX}</span>{" "}
                  <span className="min-w-0 truncate text-zinc-300">{titleOf(front ?? "")}</span>
                </p>
                <div ref={setPaneSlot} className="flex min-h-0 flex-1 flex-col" />
              </aside>
            ) : null}
          </div>
        ) : (
          <div
            data-testid="harness-no-session"
            className="grid h-full min-h-[240px] place-items-center rounded-2xl px-6 text-center"
          >
            <div className="max-w-sm space-y-2">
              <MessageSquarePlus size={24} className="mx-auto text-zinc-600" aria-hidden />
              <p className="text-[14px] font-medium text-zinc-200">Start a session to build in a folder</p>
              <p className="text-[13px] text-zinc-500">
                A session works in one folder (or in a folder of its own, when you start it with no folder)
                with the coding tools, keeps going when you leave, and shows its plan, its terminal and
                what it changed beside the conversation.
              </p>
            </div>
          </div>
        )}
      </div>
      {dragging && selected && middle === "session" && wide ? (
        <div
          data-testid="harness-drop-beside"
          onDragOver={(e) => {
            e.preventDefault();
            try {
              e.dataTransfer.dropEffect = "move";
            } catch {
              /* the drop still lands */
            }
          }}
          onDrop={onDropBeside}
          className="absolute inset-y-0 right-0 z-20 grid w-40 place-items-center rounded-2xl border-2 border-dashed border-accent/50 bg-accent/[0.06] text-center text-[12px] text-accent-soft"
        >
          Drop to open beside
        </div>
      ) : null}
    </div>
  );
}

type SessionDotState = "waiting" | "running" | "quiet";

/** A session's dot in a split: waiting on you (warn, with words for a screen
 *  reader), working (accent), quiet — the list's own colours. */
function SplitDot({ state }: { state: SessionDotState }) {
  const cls =
    state === "waiting" ? "animate-pulse bg-tone-warn" : state === "running" ? "animate-pulse bg-accent" : "bg-zinc-600";
  return (
    <>
      <span aria-hidden data-dot={state} className={`h-1.5 w-1.5 shrink-0 rounded-full ${cls}`} />
      {state === "waiting" ? <span className="sr-only">(needs you)</span> : null}
    </>
  );
}

/** A column's own header in a split: its name, whose panes, ×. */
function ColumnHead({
  side,
  title,
  running,
  waiting,
  focused,
  onFocus,
  onClose,
}: {
  side: SplitSide;
  title: string;
  running: boolean;
  waiting: boolean;
  focused: boolean;
  onFocus: () => void;
  onClose: () => void;
}) {
  return (
    <div data-testid={`harness-column-head-${side}`} className="mb-1.5 flex min-w-0 items-center gap-1.5 px-1">
      <button
        type="button"
        data-testid={`harness-column-name-${side}`}
        onClick={onFocus}
        aria-pressed={focused}
        title={focused ? "This session's plan, changes and terminal are on the right" : "Show this session's plan, changes and terminal on the right"}
        className={`flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-[12px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
          focused ? "font-medium text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
        }`}
      >
        <SplitDot state={waiting ? "waiting" : running ? "running" : "quiet"} />
        <span className="min-w-0 truncate">{title}</span>
        {focused ? (
          <span data-testid={`harness-column-focused-${side}`} className="shrink-0 text-[11px] font-normal text-accent-soft">
            · on the right
          </span>
        ) : null}
      </button>
      <button
        type="button"
        data-testid={`harness-column-close-${side}`}
        aria-label={`Close ${title}`}
        title="Close this column (the session keeps going)"
        onClick={onClose}
        className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
      >
        <X size={13} aria-hidden />
      </button>
    </div>
  );
}

/** A narrow window's split: one column shown, a tab per session. */
function SplitTabs({
  split,
  titleOf,
  dotOf,
  onFocus,
  onClose,
}: {
  split: CodeSplit;
  titleOf: (id: string) => string;
  dotOf: (id: string) => SessionDotState;
  onFocus: (side: SplitSide) => void;
  onClose: (side: SplitSide) => void;
}) {
  const sides: SplitSide[] = ["left", "right"];
  return (
    <div
      role="tablist"
      aria-label="Two coding sessions"
      data-testid="harness-split-tabs"
      className="flex min-w-0 shrink-0 items-center gap-1 rounded-lg bg-white/[0.04] p-0.5"
    >
      {sides.map((side) => {
        const id = side === "left" ? split.left : split.right;
        if (!id) return null;
        const selected = split.focus === side;
        const title = titleOf(id);
        return (
          <div key={side} className="flex min-w-0 flex-1 items-center">
            <button
              type="button"
              role="tab"
              aria-selected={selected}
              tabIndex={selected ? 0 : -1}
              data-testid={`harness-split-tab-${side}`}
              onClick={() => onFocus(side)}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const next: SplitSide = side === "left" ? "right" : "left";
                onFocus(next);
                document.querySelector<HTMLButtonElement>(`[data-testid="harness-split-tab-${next}"]`)?.focus();
              }}
              className={`flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 py-1 text-left text-[13px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
                selected ? "bg-white/[0.08] font-medium text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
              }`}
            >
              <SplitDot state={dotOf(id)} />
              <span className="min-w-0 truncate">{title}</span>
            </button>
            <button
              type="button"
              data-testid={`harness-split-tab-close-${side}`}
              aria-label={`Close ${title}`}
              onClick={() => onClose(side)}
              className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-zinc-500 hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
            >
              <X size={13} aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
