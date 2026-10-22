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
import { HarnessSidebar } from "@/components/harness/HarnessSidebar";
import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import type { RailPane } from "@/components/terminal/PaneRail";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { BUILD_SESSION_EVENT, sessionOfEvent } from "@/lib/buildLinks";
import {
  LAST_SESSION_KEY,
  createSession,
  listSessions,
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
  const [folders, setFolders] = useState<BuildFolder[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  // The first pick (the address, else the session this window had open) is
  // made when the list answers. Until then `selected` is only "not picked
  // yet" and must not be written back: the write ran BEFORE the pick and
  // erased the remembered session, so Build came back to the newest session
  // instead of the one it had open (v1.369.0 review).
  const pickedRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const list = await listSessions();
      setFolders(list);
      setListError(null);
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

  async function start(folder: string): Promise<string | null> {
    try {
      const made = await createSession(folder);
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
      className="flex flex-col gap-4 lg:h-[calc(100vh-12rem-var(--ij-strip-h,0px))] lg:min-h-[480px] lg:flex-row"
    >
      <aside className="flex max-h-[22rem] w-full shrink-0 flex-col lg:max-h-none lg:w-60 xl:w-64">
        <HarnessSidebar
          folders={folders}
          listError={listError}
          selectedId={selected}
          onSelect={setSelected}
          onStart={start}
          onRename={rename}
          defaultFolder={defaultFolder}
          terminals={terminals}
          onOpenTerminal={onOpenTerminal}
        />
      </aside>
      <div className="min-w-0 flex-1 lg:min-h-0">
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
                A session works in one folder with the coding tools, keeps going when you leave Build, and
                shows its plan, its terminal and what it changed beside the conversation.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
