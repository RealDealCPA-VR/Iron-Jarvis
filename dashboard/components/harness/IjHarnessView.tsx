"use client";

/**
 * Build's IJ HARNESS view (v1.369.0, plan Phase 6) — the screen for the
 * Phase 4 session backend: the left pane (New session, sessions grouped by
 * folder, the live Terminals), the session in the middle (HarnessSession) and
 * its panes on the right (HarnessPanes).
 *
 * The open session lives in the URL (`/terminals?session=<id>` — the stable
 * address the bell, the desktop toast and the Activity rows open) and in this
 * window's memory, so Build comes back to it. The list is polled only while
 * the window is visible (no event fires when a session changes).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageSquarePlus } from "lucide-react";
import { HarnessSidebar, type StartExtra } from "@/components/harness/HarnessSidebar";
import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import type { RailPane } from "@/components/terminal/PaneRail";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { BUILD_SESSION_EVENT, sessionOfEvent } from "@/lib/buildLinks";
import type { BranchDraft } from "@/lib/buildNext";
import { useHarnessKeys } from "@/lib/harnessKeys";
import { cachedFolders, keepFolders, viewFor } from "@/lib/turnCache";
import {
  LAST_SESSION_KEY,
  createSession,
  listSessionsWithRoot,
  renameFolder,
  type BuildFolder,
} from "@/lib/buildSessions";

/** How often the session list is read while the window is visible. */
export const SESSION_LIST_POLL_MS = 4000;

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

/** Put the open session in the address bar without a navigation (a router
 *  push would re-run the page). */
function writeUrlSession(id: string | null): void {
  try {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("session", id);
    else url.searchParams.delete("session");
    url.searchParams.delete("focus");
    window.history.replaceState(window.history.state, "", url.toString());
  } catch {
    /* no history API — the window's memory still holds it */
  }
}

export function IjHarnessView({
  initialSession,
  terminals,
  onOpenTerminal,
  defaultFolder,
  terminal,
}: {
  /** `?session=<id>` the page was opened with. */
  initialSession: string | null;
  terminals: RailPane[];
  /** Open a terminal in the Terminals view (the page's own focus path). */
  onOpenTerminal: (id: string) => void;
  /** Where a new session starts when nothing else is picked. */
  defaultFolder: string | null;
  terminal: SessionTerminalApi;
}) {
  // v1.384.0: a RETURN to Build paints the list and the session this window
  // had open at once (lib/turnCache — this window's own memory of them),
  // then reads both again. A first visit waits for the list, as before.
  const [initial] = useState(() => {
    const kept = cachedFolders<{ folders: BuildFolder[]; workRoot: string }>();
    const wanted = initialSession ?? readLastSession();
    const known = !!wanted && (viewFor(wanted) !== null || !!kept?.folders.some((f) => f.sessions.some((s) => s.id === wanted)));
    return { kept, pick: known ? wanted : null };
  });
  const [folders, setFolders] = useState<BuildFolder[] | null>(initial.kept?.folders ?? null);
  // v1.379.0: where a session with no folder keeps its files (the list says).
  const [workRoot, setWorkRoot] = useState(initial.kept?.workRoot ?? "");
  const [listError, setListError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(initial.pick);
  // The first pick (the address, else the session this window had open) is
  // made when the list answers. Until then `selected` is only "not picked
  // yet" and must not be written back: the write ran BEFORE the pick and
  // erased the remembered session, so Build came back to the newest session
  // instead of the one it had open (v1.369.0 review). v1.384.0: a session
  // this window already knows is the pick at once.
  const pickedRef = useRef(initial.pick !== null);

  const refresh = useCallback(async () => {
    try {
      const { folders: list, workRoot: root } = await listSessionsWithRoot();
      setFolders(list);
      setWorkRoot(root);
      setListError(null);
      keepFolders({ folders: list, workRoot: root });
      return list;
    } catch (e) {
      setListError(
        e && typeof e === "object" && "status" in e && (e as { status?: number }).status === 404
          ? "This version of Iron Jarvis has no Build sessions yet."
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
      const wanted = initialSession ?? readLastSession();
      const known = list?.some((f) => f.sessions.some((s) => s.id === wanted));
      // A session from the URL is opened even when the list does not have it
      // yet (it may be archived); a remembered one only while it is listed.
      const pick = initialSession ?? (known ? wanted : null) ?? list?.[0]?.sessions[0]?.id ?? null;
      pickedRef.current = true;
      setSelected(pick);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useVisibleInterval(() => void refresh(), SESSION_LIST_POLL_MS);

  // A link to a session pressed while this view is showing (the bell): the
  // page keeps its state on that navigation, so the link announces the
  // session and this view opens it (lib/buildLinks, v1.369.0 review).
  useEffect(() => {
    const onLink = (e: Event) => {
      const id = sessionOfEvent(e);
      if (!id) return;
      pickedRef.current = true;
      setSelected(id);
      void refresh();
    };
    window.addEventListener(BUILD_SESSION_EVENT, onLink);
    return () => window.removeEventListener(BUILD_SESSION_EVENT, onLink);
  }, [refresh]);

  useEffect(() => {
    if (!pickedRef.current) return;
    writeLastSession(selected);
    writeUrlSession(selected);
  }, [selected]);

  const listRunning = useMemo(
    () => !!folders?.some((f) => f.sessions.some((s) => s.id === selected && s.running)),
    [folders, selected],
  );
  // v1.378.0 (part 2, Phase 7, the walkthrough): New session opens on the
  // folder of the session in front — its group's folder, the one the user
  // picked (a session on its own branch is grouped there too). The Terminals
  // view's folder was the default before, so a second session in the same
  // folder meant pasting the folder again. No session open: as before.
  // v1.379.0 review (the merge with v1.378.0): the session in front has NO
  // folder — New session opens the same way, with no folder (the box empty,
  // the folder list closed: Start makes another session with no folder); it
  // never falls back to the Terminals view's folder.
  const frontGroup = useMemo(
    () => folders?.find((f) => f.sessions.some((s) => s.id === selected)) ?? null,
    [folders, selected],
  );
  const sessionFolder = frontGroup ? (frontGroup.noFolder ? "" : frontGroup.path) : null;
  const frontHasNoFolder = frontGroup?.noFolder === true;

  // v1.377.0 (part 2, Phase 6): a session just BRANCHED from a question opens
  // with that question in its box (handed over once, for that session only).
  const [draftFor, setDraftFor] = useState<{ id: string; draft: BranchDraft } | null>(null);
  function openBranched(id: string, draft: BranchDraft | null) {
    pickedRef.current = true;
    setDraftFor(draft ? { id, draft } : null);
    setSelected(id);
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
      pickedRef.current = true;
      setSelected(made.id);
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

  return (
    <div
      data-testid="ij-harness-view"
      className="flex flex-col gap-4 xl:h-[calc(100vh-12rem-var(--ij-strip-h,0px))] xl:min-h-[480px] xl:flex-row"
    >
      {/* v1.379.0: below xl the left pane is capped, and it SCROLLS — New
          session with its folder list open is taller than the cap and was
          drawn over the session below it at phone width (measured, 390 px). */}
      <aside className="flex max-h-[22rem] w-full shrink-0 flex-col overflow-y-auto xl:max-h-none xl:w-56 xl:overflow-visible 2xl:w-64">
        <HarnessSidebar
          folders={folders}
          listError={listError}
          selectedId={selected}
          onSelect={setSelected}
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
      </aside>
      <div className="min-w-0 flex-1 xl:min-h-0">
        {selected ? (
          <HarnessSession
            key={selected}
            threadId={selected}
            terminal={terminal}
            listRunning={listRunning}
            onChanged={() => void refresh()}
            onGone={() => {
              setSelected(null);
              void refresh();
            }}
            onBranched={openBranched}
            initialDraft={draftFor && draftFor.id === selected ? draftFor.draft : null}
            onOpenSession={(id) => {
              pickedRef.current = true;
              setSelected(id);
            }}
            // v1.379.0: "/clear" starts a new session on the same folder (or
            // with no folder), "/resume" puts the caret in the search.
            onNewSession={(folder) => start(folder)}
            onFindSessions={() => setSearchRequest((n) => n + 1)}
          />
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
                with the coding tools, keeps going when you leave Build, and shows its plan, its terminal and
                what it changed beside the conversation.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
