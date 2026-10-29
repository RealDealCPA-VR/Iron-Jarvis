"use client";

/**
 * CHAT | CODE — the chat page's top switch (v1.406.0 part 1).
 *
 * The user's decision (2026-10-29): "the chat will contain the box at the
 * top for chat vs coding and the build will only be for terminal sessions
 * using other harnesses. The box at the top will be just like it is in build
 * now, but have the switch between chat and code."
 *
 *  - The switch is Build's old view switch, word for word in its look (plain
 *    text tabs in one quiet box), in the page's exact MIDDLE, on a row of its
 *    own above BOTH views — so it never moves when the view changes (the
 *    v1.370.2 rule, pinned in pixels by e2e chat-code-switch.spec).
 *  - CHAT is the chat page, unchanged. It stays MOUNTED behind the Code view
 *    (hidden + `inert`), so a draft, a running reply and the open chat are
 *    exactly as they were when the user comes back. While it is hidden it
 *    does not hold the sidebar's list slot.
 *  - CODE is the coding sessions (components/harness/CodeView). It mounts
 *    when shown and unmounts when Chat is chosen — exactly as Build's view
 *    did; a running reply survives through lib/turnCache, and a session's
 *    terminal through components/terminal/paneHost.
 *  - The address carries the view (`?view=code`, `&session=`, `&night=`) and
 *    the window remembers it (lib/chatSurface). A link to a session pressed
 *    while this page is open is ANNOUNCED (lib/buildLinks), since Next keeps
 *    the page on a query-only navigation.
 *
 * Every full-height thing under the switch subtracts its row
 * (`--ij-switch-h`), as every full-height module subtracts the demo strip.
 */

import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { Code2, Loader2, MessageSquare } from "lucide-react";
import {
  CHAT_VIEW_EVENT,
  cachedView,
  keepView,
  rememberedView,
  viewFromSearch,
  viewOfEvent,
  writeViewToAddress,
  type ChatSurfaceView,
} from "@/lib/chatSurface";
import { BUILD_NIGHT_EVENT, BUILD_SESSION_EVENT, sessionOfEvent } from "@/lib/buildLinks";
import { CHAT_VIEW_NAME, CODE_VIEW_NAME } from "@/lib/buildWords";
import { NEW_CHAT_EVENT, closeChatSlot, useChatSlot } from "@/lib/sidebarSlot";
import { ViewSwitch } from "@/components/ViewSwitch";

// The Code view: browser only, loaded when it is first shown (it brings the
// whole IJ harness and xterm with it; the chat never pays for that).
const CodeView = dynamic(() => import("@/components/harness/CodeView").then((m) => m.CodeView), {
  ssr: false,
  loading: () => (
    <div className="grid h-48 place-items-center text-zinc-600">
      <Loader2 size={18} className="animate-spin" />
    </div>
  ),
});

/** components/ModelMenuBridge OPEN_SWITCHER_EVENT, spelled here: importing
 *  the bridge would pull the whole app sidebar into this module. */
const OPEN_SWITCHER_EVENT = "ij:open-switcher";

/** The switch's own row: h-9 plus its mb-2 (what the views below subtract). */
export const SWITCH_ROW_HEIGHT = "2.75rem";

const VIEWS: { value: ChatSurfaceView; label: string; icon: ReactNode }[] = [
  { value: "chat", label: CHAT_VIEW_NAME, icon: <MessageSquare size={13} aria-hidden /> },
  { value: "code", label: CODE_VIEW_NAME, icon: <Code2 size={13} aria-hidden /> },
];

function linkParams(): { session: string | null; night: string | null } {
  try {
    const q = new URLSearchParams(window.location.search);
    const session = (q.get("session") ?? "").trim() || null;
    const night = (q.get("night") ?? "").trim() || null;
    return { session, night };
  } catch {
    return { session: null, night: null };
  }
}

export function ChatCodeSurface({ renderChat }: { renderChat: (hidden: boolean) => ReactNode }) {
  // The first frame: what this WINDOW last showed (module state — null on a
  // fresh load, exactly as the server renders it), else Chat. The address
  // and the remembered choice are read right after.
  const [view, setView] = useState<ChatSurfaceView>(() => cachedView() ?? "chat");
  const [linkSession, setLinkSession] = useState<string | null>(null);
  const [linkNight, setLinkNight] = useState<{ night: string; n: number } | null>(null);
  const slot = useChatSlot();

  useEffect(() => {
    let fromAddress: ChatSurfaceView | null = null;
    try {
      fromAddress = viewFromSearch(window.location.search);
    } catch {
      fromAddress = null;
    }
    const { session, night } = linkParams();
    if (session) setLinkSession(session);
    else if (night) setLinkNight({ night, n: 1 });
    const next = fromAddress ?? rememberedView() ?? "chat";
    setView(next);
    keepView(next);
    // A remembered Code view puts itself in the address, so a reload or a
    // copied link opens it; an address that already said so is left alone.
    if (next === "code" && fromAddress !== "code") writeViewToAddress("code");
  }, []);

  const choose = useCallback((next: ChatSurfaceView) => {
    setView(next);
    keepView(next);
    writeViewToAddress(next);
    if (next === "chat") {
      // The link the page was opened with is spent: coming back to Code
      // opens the session this window had open (the v1.369.0 review rule).
      setLinkSession(null);
      setLinkNight(null);
    }
  }, []);

  // Links pressed while this page is open (the bell, Running now, Ctrl K):
  // Next keeps the page, so the link announces itself (lib/buildLinks,
  // lib/chatSurface) and this switches.
  useEffect(() => {
    const onSession = (e: Event) => {
      const id = sessionOfEvent(e);
      if (!id) return;
      setLinkSession(id);
      choose("code");
    };
    const onNight = (e: Event) => {
      const night = sessionOfEvent(e);
      if (!night) return;
      setLinkNight((cur) => ({ night, n: (cur?.n ?? 0) + 1 }));
      choose("code");
    };
    const onView = (e: Event) => {
      const v = viewOfEvent(e);
      if (v) choose(v);
    };
    // The sidebar's New chat starts a chat — in the Chat view.
    const onNewChat = () => choose("chat");
    // v1.406.0 review: "Switch model" (Ctrl K) and "Choose a model" (the demo
    // strip) open the CHAT composer's model menu (ModelMenuBridge). Behind
    // the Code view that menu opened invisibly — the press did nothing the
    // user could see — so the chat comes to the front with it.
    const onSwitcher = () => choose("chat");
    window.addEventListener(BUILD_SESSION_EVENT, onSession);
    window.addEventListener(BUILD_NIGHT_EVENT, onNight);
    window.addEventListener(CHAT_VIEW_EVENT, onView);
    window.addEventListener(NEW_CHAT_EVENT, onNewChat);
    window.addEventListener(OPEN_SWITCHER_EVENT, onSwitcher);
    return () => {
      window.removeEventListener(BUILD_SESSION_EVENT, onSession);
      window.removeEventListener(BUILD_NIGHT_EVENT, onNight);
      window.removeEventListener(CHAT_VIEW_EVENT, onView);
      window.removeEventListener(NEW_CHAT_EVENT, onNewChat);
      window.removeEventListener(OPEN_SWITCHER_EVENT, onSwitcher);
    };
  }, [choose]);

  const code = view === "code";
  return (
    <div
      data-testid="chat-surface"
      data-view={view}
      style={{ "--ij-switch-h": SWITCH_ROW_HEIGHT } as CSSProperties}
    >
      <div data-testid="chat-view-switch-row" className="mb-2 flex h-9 shrink-0 items-center justify-center">
        {/* THE SWITCH: Build's old view switch, its look kept — plain text
            tabs in one quiet box, the open one in ink. v1.409.0: lifted into
            components/ViewSwitch, which Build's Code | Terminal switch shares
            (one component, so the two can never drift). */}
        <ViewSwitch views={VIEWS} value={view} onChange={choose} label="Chat or code" prefix="chat-view" />
      </div>
      <div
        id="chat-view-panel-chat"
        role="tabpanel"
        aria-labelledby="chat-view-tab-chat"
        data-testid="chat-view-panel-chat"
        hidden={code}
        inert={code}
      >
        {renderChat(code)}
      </div>
      {code ? (
        <div
          id="chat-view-panel-code"
          role="tabpanel"
          aria-labelledby="chat-view-tab-code"
          data-testid="chat-view-panel-code"
        >
          <CodeView initialSession={linkSession} nightLink={linkNight} listSlot={slot} onListNavigate={closeChatSlot} />
        </div>
      ) : null}
    </div>
  );
}

export default ChatCodeSurface;
