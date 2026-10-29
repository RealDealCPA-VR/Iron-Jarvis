/**
 * v1.406.0 part 1 REVIEW — the Chat | Code switch's loose ends.
 *
 *  1. The sidebar's Chat row pressed while the Code view is in front
 *     switches to Chat (it did nothing: Next keeps the page on a same-page
 *     navigation, and nothing told the surface). From home (`/`, a remount
 *     onto `/chat`) it opens Chat too — the window's memory says Chat.
 *  2. "Switch model" (Ctrl K / the demo strip, `ij:open-switcher`) behind the
 *     Code view brings the Chat view forward — the menu it opens is the chat
 *     composer's, and behind Code it opened invisibly.
 *  3. A chat link in the bell (`/chat?thread=`) pressed while Code is in
 *     front brings Chat forward; a coding-session row with no session named
 *     opens the CODE view, never Build (`/terminals`, where sessions no
 *     longer live) — and so do the coding sessions' background tasks.
 *
 * The behaviours of the hidden chat itself (its page-wide file drop and its
 * microphone) are pinned by source in the last block: the page is 14,000
 * lines and renders nowhere without its whole world.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const nav = vi.hoisted(() => ({ pathname: "/chat" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    if (path.startsWith("/sessions")) return { sessions: [] };
    if (path.startsWith("/chat/threads")) return { threads: [] };
    if (path.startsWith("/projects")) return { projects: [] };
    return {};
  },
  post: async () => ({}),
  put: async () => ({}),
  ApiError: class extends Error {},
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { version: "1.406.0", providers: [] }, refresh: () => {} }),
}));
// The surface's only dynamic import is the Code view: a stand-in.
vi.mock("next/dynamic", async () => {
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    default: () =>
      function CodeViewStub() {
        return R.createElement("div", { "data-testid": "code-view-stub" });
      },
  };
});

import { AppSidebar } from "@/components/AppSidebar";
import { ChatCodeSurface } from "@/components/chat/ChatCodeSurface";
import { announceChatViewLink, cachedView, resetViewMemory, CODE_VIEW_HREF } from "@/lib/chatSurface";
import { chatAskHref } from "@/components/NotificationBell";
import { LOOP_HREF } from "@/lib/loopWords";

function FakeChat() {
  return <div data-testid="fake-chat" />;
}

function renderBoth() {
  return render(
    <>
      <AppSidebar />
      <ChatCodeSurface renderChat={() => <FakeChat />} />
    </>,
  );
}

const view = () => screen.getByTestId("chat-surface").getAttribute("data-view");

beforeEach(() => {
  nav.pathname = "/chat";
  resetViewMemory();
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/chat");
});
afterEach(() => cleanup());

describe("the sidebar's Chat row", () => {
  it("switches to Chat while the Code view is in front", () => {
    renderBoth();
    fireEvent.click(screen.getByTestId("chat-view-code"));
    expect(view()).toBe("code");
    // A Link click in jsdom: the row's own onClick runs (the navigation is
    // Next's; jsdom does not navigate).
    act(() => {
      fireEvent.click(screen.getByTestId("sidebar-nav-chat"));
    });
    expect(view()).toBe("chat");
    expect(screen.queryByTestId("code-view-stub")).toBeNull();
    expect(screen.getByTestId("chat-view-chat").getAttribute("aria-selected")).toBe("true");
  });

  it("from home (a remount onto /chat) the window's memory says Chat", () => {
    nav.pathname = "/";
    window.history.replaceState(null, "", "/");
    renderBoth();
    fireEvent.click(screen.getByTestId("chat-view-code"));
    expect(cachedView()).toBe("code");
    act(() => {
      fireEvent.click(screen.getByTestId("sidebar-nav-chat"));
    });
    expect(cachedView()).toBe("chat");
  });

  it("does nothing to the view on another page", () => {
    nav.pathname = "/projects";
    window.history.replaceState(null, "", "/projects");
    render(<AppSidebar />);
    act(() => {
      fireEvent.click(screen.getByTestId("sidebar-nav-chat"));
    });
    expect(cachedView()).toBeNull();
  });
});

describe("other doors behind the Code view", () => {
  it("Switch model (ij:open-switcher) brings Chat forward", () => {
    render(<ChatCodeSurface renderChat={() => <FakeChat />} />);
    fireEvent.click(screen.getByTestId("chat-view-code"));
    expect(view()).toBe("code");
    act(() => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    expect(view()).toBe("chat");
  });

  it("a chat link announced (the bell's waiting chat) brings Chat forward", () => {
    render(<ChatCodeSurface renderChat={() => <FakeChat />} />);
    fireEvent.click(screen.getByTestId("chat-view-code"));
    act(() => {
      expect(announceChatViewLink("/chat?thread=t9")).toBe(true);
    });
    expect(view()).toBe("chat");
  });

  it("the bell's announce covers chat links, not only coding sessions (source)", () => {
    const src = readFileSync(join(__dirname, "..", "components", "NotificationBell.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toMatch(/if \(!announceBuildSessionLink\(href\)\) announceChatViewLink\(href\);/);
    const run = readFileSync(join(__dirname, "..", "components", "RunningNow.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(run).toMatch(/it\.kind === "chat"\) announceChatViewLink\(it\.href\)/);
  });
});

describe("coding-session links with no session named open the Code view, never Build", () => {
  it("a waiting coding session whose thread is unknown", () => {
    expect(chatAskHref({ id: "x", threadId: "", build: true })).toBe(CODE_VIEW_HREF);
    expect(chatAskHref({ id: "x", threadId: "t1", build: true })).toBe(`${CODE_VIEW_HREF}&session=t1`);
  });

  it("the bell's account-limit and library rows (source: no '/terminals' fallback left)", () => {
    const src = readFileSync(join(__dirname, "..", "components", "NotificationBell.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src).not.toMatch(/buildSessionHref\([^)]*\)\s*:\s*"\/terminals"/);
  });

  it("the coding sessions' background tasks", () => {
    for (const key of ["build_accounts", "build_resume", "night_shift"] as const) {
      expect(LOOP_HREF[key], key).toBe(CODE_VIEW_HREF);
    }
    // Build's own task still opens Build.
    expect(LOOP_HREF.rehydrate_terminals).toBe("/terminals");
  });
});

describe("the hidden chat leaves the Code view alone (source pins)", () => {
  const page = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
  it("a file dropped on the Code view is not this chat's attachment", () => {
    const drop = page.slice(page.indexOf("const onDrop = (e: DragEvent)"));
    const body = drop.slice(0, drop.indexOf("};"));
    expect(body).toMatch(/if \(surfaceHiddenRef\.current\) return;/);
    expect(body.indexOf("surfaceHiddenRef.current")).toBeLessThan(body.indexOf("addFilesRef.current"));
    // …and it is still prevented, so the browser never opens the file.
    expect(body.indexOf("e.preventDefault()")).toBeLessThan(body.indexOf("surfaceHiddenRef.current"));
  });
  it("the microphone and hands-free Voice Chat stop when the chat is hidden", () => {
    const at = page.indexOf("if (!surfaceHidden) return;");
    expect(at).toBeGreaterThan(0);
    const end = page.indexOf("}, [surfaceHidden]);", at);
    expect(end).toBeGreaterThan(at);
    const eff = page.slice(at, end);
    expect(eff).toMatch(/setVoiceMode\(false\)/);
    expect(eff).toMatch(/dictation\.stop\(\)/);
  });
});
