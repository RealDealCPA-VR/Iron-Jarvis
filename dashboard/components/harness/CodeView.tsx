"use client";

/**
 * THE CODE VIEW (v1.406.0 part 1): the coding sessions, on the chat page.
 *
 * The chat page's top switch reads Chat | Code (lib/chatSurface). Code is
 * what Build's "Coding sessions" view was — IjHarnessView, unchanged: the
 * sessions grouped by folder, the conversation, its plan / terminal /
 * changes / browser / files beside it. Build keeps only the terminals.
 *
 * What Build lent the view, this lends instead, from its own small state:
 *  - the live terminals for the list's Terminals group (GET /terminals once,
 *    GET /terminals/activity while the view shows) — pressing one opens it
 *    in Build (`/terminals?focus=<id>`), the one place terminals live now;
 *  - a session's terminal tab (SessionTerminalApi): a NORMAL Build terminal
 *    in the session's folder, drawn with the same TerminalPane, so it shows
 *    in Build too and outlives this view (components/terminal/paneHost);
 *  - the folder a new session starts in: the one Build last worked in
 *    (`ij.build.folder`).
 *
 * The session list goes where the chat list goes (lib/sidebarSlot): the
 * app sidebar's slot while it is on screen, else the view's own left pane.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { del, get, post } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import { useModels } from "@/lib/useModels";
import type { AiCli, TerminalInfo } from "@/lib/types";
import type { RailPane } from "@/components/terminal/PaneRail";
import { displayState, resolveState, type PaneActivity } from "@/components/terminal/PaneState";
import { livePaneAccounts, paneAccountBadge } from "@/lib/paneAccounts";
import { disposePaneHost } from "@/components/terminal/paneHost";
import { paneTurn } from "@/components/terminal/paneChatCore";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { IjHarnessView } from "@/components/harness/IjHarnessView";

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

/** The folder Build last worked in (the Build page writes it). */
const BUILD_FOLDER_KEY = "ij.build.folder";

/** Where a terminal opens: Build, on THAT terminal. */
export function buildTerminalHref(id: string): string {
  return `/terminals?focus=${encodeURIComponent(id)}`;
}

export function CodeView({
  initialSession,
  nightLink = null,
  listSlot = null,
  onListNavigate,
}: {
  /** `?session=<id>` the page was opened with (or a link announced). */
  initialSession: string | null;
  /** `?night=<date>` (Last night), `n` counting the presses. */
  nightLink?: { night: string; n: number } | null;
  /** Where the session list goes (the app sidebar's slot), else in the view. */
  listSlot?: HTMLElement | null;
  /** A row in the slotted list was followed (the phone drawer closes). */
  onListNavigate?: () => void;
}) {
  const router = useRouter();
  const [terminals, setTerminals] = useState<TerminalInfo[]>([]);
  const [aiClis, setAiClis] = useState<AiCli[]>([]);
  const { data: activityData } = usePolledApi<{ panes: PaneActivity[] }>("/terminals/activity", 2500);
  const catalog = useModels();
  const [defaultFolder, setDefaultFolder] = useState<string | null>(null);

  useEffect(() => {
    try {
      setDefaultFolder(window.localStorage.getItem(BUILD_FOLDER_KEY));
    } catch {
      /* storage blocked: no remembered folder */
    }
    let cancelled = false;
    get<{ terminals: TerminalInfo[] }>("/terminals")
      .then((r) => {
        if (!cancelled) setTerminals((r?.terminals ?? []).filter((t) => t.alive));
      })
      .catch(() => {
        /* no list — the group says "No terminals open." */
      });
    get<{ clis: AiCli[] }>("/terminals/ai-clis")
      .then((r) => {
        if (!cancelled) setAiClis(r?.clis ?? []);
      })
      .catch(() => {
        /* the Launch menu keeps saying "Detecting…" */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const activity = useMemo(() => {
    const m = new Map<string, PaneActivity>();
    for (const p of activityData?.panes ?? []) m.set(p.id, p);
    return m;
  }, [activityData]);

  const cliLabel = useCallback(
    (id: string | null | undefined) => (id ? (aiClis.find((c) => c.id === id)?.label ?? id) : null),
    [aiClis],
  );

  const panes: RailPane[] = useMemo(
    () =>
      terminals
        .filter((t) => t.alive)
        .map((t) => {
          const a = activity.get(t.id);
          const cli = a?.agent_cli ?? null;
          return {
            id: t.id,
            label: a?.name || t.shell || t.id,
            state: displayState(resolveState(a?.state, false), cli),
            cli: cliLabel(cli),
            cwd: t.cwd,
            capabilities: t.capabilities ?? null,
            account: paneAccountBadge(livePaneAccounts(t, a), cli),
            since: t.created_at ?? null,
          };
        }),
    [terminals, activity, cliLabel],
  );

  const addTerminal = useCallback(async (cwd?: string | null): Promise<TerminalInfo | null> => {
    try {
      const info = await post<TerminalInfo>("/terminals", { cwd: cwd ?? undefined });
      setTerminals((prev) => (prev.some((t) => t.id === info.id) ? prev : [...prev, info]));
      return info;
    } catch {
      return null;
    }
  }, []);

  const closeTerminal = useCallback((id: string) => {
    // As Build's own close: a named chat turn of the terminal ends too, and
    // its xterm and socket (which outlive an unmount) are ended for good.
    const turn = paneTurn(id);
    if (turn) post(`/chat/turns/${encodeURIComponent(turn)}/stop`).catch(() => {});
    disposePaneHost(id);
    setTerminals((prev) => prev.filter((t) => t.id !== id));
    del(`/terminals/${id}`).catch(() => {
      /* already gone / offline — removed regardless */
    });
  }, []);

  const terminal: SessionTerminalApi = {
    find: (id) => terminals.find((t) => t.id === id && t.alive !== false) ?? null,
    open: (cwd) => addTerminal(cwd),
    render: (info, { parked, onClose }) => {
      const a = activity.get(info.id);
      return (
        <TerminalPane
          info={info}
          focused={!parked}
          parked={parked}
          paneName={a?.name}
          paneState={resolveState(a?.state, false)}
          agentCli={a?.agent_cli}
          paneStateLine={a?.state_line}
          liveAccounts={livePaneAccounts(info, a)}
          onFocus={() => {}}
          onClose={onClose}
          models={catalog.usable}
          aiClis={aiClis}
          otherTerminals={terminals.map((x) => ({ id: x.id, shell: x.shell, cwd: x.cwd }))}
        />
      );
    },
    close: (id) => closeTerminal(id),
  };

  return (
    <IjHarnessView
      initialSession={initialSession}
      nightLink={nightLink}
      terminals={panes}
      onOpenTerminal={(id) => router.push(buildTerminalHref(id))}
      defaultFolder={defaultFolder}
      terminal={terminal}
      listSlot={listSlot}
      onListNavigate={onListNavigate}
    />
  );
}

export default CodeView;
