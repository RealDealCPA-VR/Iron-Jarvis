/**
 * v1.406.0 part 1 — Chat | Code (the user's decision, 2026-10-29: "the chat
 * will contain the box at the top for chat vs coding and the build will only
 * be for terminal sessions using other harnesses").
 *
 * Pinned here, through the REAL surface component (components/chat/
 * ChatCodeSurface) with the Code view stood in (it is the IJ harness, pinned
 * by its own files):
 *
 *   - the switch reads Chat | Code, sits on its own row, centred, above BOTH
 *     views (the pixel half is e2e/chat-code-switch.spec);
 *   - Code shows the coding sessions; the chat stays MOUNTED behind it
 *     (hidden + inert), so a draft survives a switch;
 *   - the address carries the view (`?view=code&session=<id>`) and opens
 *     it; this window remembers the view (sessionStorage); a chat link
 *     (`?thread=`) opens Chat whatever was remembered;
 *   - a session link announced while the page is open switches to Code on
 *     that session; the sidebar's New chat switches to Chat;
 *   - the sidebar's list slot belongs to whichever view is in front;
 *   - the link helpers (lib/buildLinks, lib/chatSurface): every producer's
 *     address, the old `/terminals?session=` still READ and redirected.
 */

import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const seen = vi.hoisted(() => ({ codeProps: [] as Record<string, unknown>[], codeMounts: 0 }));

// The surface's only dynamic import is the Code view: a stand-in that says
// what it was handed.
vi.mock("next/dynamic", async () => {
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    default: () =>
      function CodeViewStub(props: Record<string, unknown>) {
        seen.codeProps.push(props);
        R.useEffect(() => {
          seen.codeMounts += 1;
        }, []);
        const slot = props.listSlot as HTMLElement | null;
        return R.createElement(
          "div",
          { "data-testid": "code-view-stub", "data-initial": String(props.initialSession ?? "") },
          slot ? "list in the sidebar" : "list in the view",
        );
      },
  };
});

import { ChatCodeSurface } from "@/components/chat/ChatCodeSurface";
import {
  CHAT_VIEW_KEY,
  announceChatViewLink,
  resetViewMemory,
  viewFromSearch,
  viewOfHref,
  useChatSurfaceView,
} from "@/lib/chatSurface";
import {
  BUILD_SESSION_EVENT,
  announceBuildSessionLink,
  buildSessionHref,
  codeHrefForOldBuildSearch,
  lastNightHref,
  nightOfHref,
  sessionLinkFor,
  sessionOfHref,
} from "@/lib/buildLinks";
import { NEW_CHAT_EVENT, setChatSlot } from "@/lib/sidebarSlot";

/** A chat stand-in with a draft box: its value survives only if it stays
 *  mounted. */
function FakeChat({ hidden }: { hidden: boolean }) {
  const [draft, setDraft] = useState("");
  return (
    <div data-testid="fake-chat" data-hidden={String(hidden)}>
      <input aria-label="draft" value={draft} onChange={(e) => setDraft(e.target.value)} />
    </div>
  );
}

function renderSurface() {
  return render(<ChatCodeSurface renderChat={(hidden) => <FakeChat hidden={hidden} />} />);
}

function SidebarView() {
  return <span data-testid="sidebar-view">{useChatSurfaceView()}</span>;
}

beforeEach(() => {
  seen.codeProps.length = 0;
  seen.codeMounts = 0;
  window.sessionStorage.clear();
  resetViewMemory();
  window.history.replaceState(null, "", "/chat");
  setChatSlot(null);
});
afterEach(() => {
  cleanup();
  setChatSlot(null);
});

describe("the switch", () => {
  it("reads Chat | Code on its own centred row, Chat first by default", () => {
    renderSurface();
    const row = screen.getByTestId("chat-view-switch-row");
    expect(row.className).toContain("justify-center");
    const list = screen.getByRole("tablist", { name: "Chat or code" });
    expect(row.contains(list)).toBe(true);
    const tabs = Array.from(list.querySelectorAll('[role="tab"]')).map((t) => t.textContent);
    expect(tabs).toEqual(["Chat", "Code"]);
    expect(screen.getByTestId("chat-view-chat").getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByTestId("code-view-stub")).toBeNull();
    // The switch row sits ABOVE both panels (never inside one of them), so
    // it cannot move when the view changes.
    const panel = screen.getByTestId("chat-view-panel-chat");
    expect(panel.contains(row)).toBe(false);
    expect(row.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("Code shows the coding sessions and keeps the chat mounted behind it (a draft survives)", () => {
    renderSurface();
    fireEvent.change(screen.getByLabelText("draft"), { target: { value: "half a question" } });
    fireEvent.click(screen.getByTestId("chat-view-code"));
    expect(screen.getByTestId("code-view-stub")).toBeTruthy();
    const chatPanel = screen.getByTestId("chat-view-panel-chat");
    expect(chatPanel.hidden).toBe(true);
    expect(chatPanel.hasAttribute("inert")).toBe(true);
    expect(screen.getByTestId("fake-chat").getAttribute("data-hidden")).toBe("true");
    expect(window.location.search).toBe("?view=code");
    fireEvent.click(screen.getByTestId("chat-view-chat"));
    expect(screen.queryByTestId("code-view-stub")).toBeNull();
    expect((screen.getByLabelText("draft") as HTMLInputElement).value).toBe("half a question");
    expect(screen.getByTestId("chat-view-panel-chat").hidden).toBe(false);
    expect(window.location.search).toBe("");
  });

  it("arrow keys move between the two, like Build's old switch", () => {
    renderSurface();
    fireEvent.keyDown(screen.getByTestId("chat-view-chat"), { key: "ArrowRight" });
    expect(screen.getByTestId("chat-view-code").getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(screen.getByTestId("chat-view-code"));
  });
});

describe("the address and the window's memory", () => {
  it("a session link opens Code on THAT session", () => {
    window.history.replaceState(null, "", "/chat?view=code&session=bs_7");
    renderSurface();
    expect(screen.getByTestId("code-view-stub").getAttribute("data-initial")).toBe("bs_7");
  });

  it("an old-style address that names only a session still opens Code", () => {
    window.history.replaceState(null, "", "/?session=bs_8");
    renderSurface();
    expect(screen.getByTestId("code-view-stub").getAttribute("data-initial")).toBe("bs_8");
  });

  it("the window remembers its view (sessionStorage), and writes it into the address", () => {
    window.sessionStorage.setItem(CHAT_VIEW_KEY, "code");
    renderSurface();
    expect(screen.getByTestId("code-view-stub")).toBeTruthy();
    expect(window.location.search).toBe("?view=code");
  });

  it("a choice of Code survives a reload of this window (module state gone, sessionStorage kept)", () => {
    renderSurface();
    fireEvent.click(screen.getByTestId("chat-view-code"));
    cleanup();
    resetViewMemory(); // a reload: the module's memory is new
    window.history.replaceState(null, "", "/chat");
    renderSurface();
    expect(screen.getByTestId("code-view-stub")).toBeTruthy();
    // Choosing Chat clears it again (a window that never opened Code keeps
    // its sessionStorage as before).
    fireEvent.click(screen.getByTestId("chat-view-chat"));
    expect(window.sessionStorage.getItem(CHAT_VIEW_KEY)).toBeNull();
  });

  it("a chat link (?thread=) opens Chat whatever the window remembered", () => {
    window.sessionStorage.setItem(CHAT_VIEW_KEY, "code");
    window.history.replaceState(null, "", "/chat?thread=t1");
    renderSurface();
    expect(screen.queryByTestId("code-view-stub")).toBeNull();
    expect(screen.getByTestId("chat-view-chat").getAttribute("aria-selected")).toBe("true");
  });

  it("choosing Chat spends the link: Code comes back to the window's own session", () => {
    window.history.replaceState(null, "", "/chat?view=code&session=bs_7");
    renderSurface();
    fireEvent.click(screen.getByTestId("chat-view-chat"));
    fireEvent.click(screen.getByTestId("chat-view-code"));
    expect(screen.getByTestId("code-view-stub").getAttribute("data-initial")).toBe("");
  });
});

describe("links pressed while the page is open", () => {
  it("a session link announced in Chat switches to Code on that session", () => {
    renderSurface();
    act(() => {
      expect(announceBuildSessionLink(buildSessionHref("bs 9"))).toBe(true);
    });
    expect(screen.getByTestId("code-view-stub").getAttribute("data-initial")).toBe("bs 9");
  });

  it("a plain Code link (Ctrl K's row) is announced too", () => {
    renderSurface();
    act(() => {
      expect(announceChatViewLink("/chat?view=code")).toBe(true);
    });
    expect(screen.getByTestId("code-view-stub")).toBeTruthy();
  });

  it("the sidebar's New chat brings Chat to the front", () => {
    window.history.replaceState(null, "", "/chat?view=code");
    renderSurface();
    expect(screen.getByTestId("code-view-stub")).toBeTruthy();
    act(() => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    expect(screen.queryByTestId("code-view-stub")).toBeNull();
  });

  it("an announcement on another page does nothing (the navigation opens it)", () => {
    window.history.replaceState(null, "", "/terminals");
    const heard: string[] = [];
    const on = (e: Event) => heard.push(String((e as CustomEvent).detail?.id));
    window.addEventListener(BUILD_SESSION_EVENT, on);
    expect(announceBuildSessionLink(buildSessionHref("x"))).toBe(false);
    window.removeEventListener(BUILD_SESSION_EVENT, on);
    expect(heard).toEqual([]);
  });
});

describe("the sidebar's list slot belongs to the view in front", () => {
  it("Code gets the slot; the sidebar's view store follows the switch", () => {
    const slot = document.createElement("div");
    document.body.appendChild(slot);
    setChatSlot(slot);
    render(
      <>
        <SidebarView />
        <ChatCodeSurface renderChat={(hidden) => <FakeChat hidden={hidden} />} />
      </>,
    );
    expect(screen.getByTestId("sidebar-view").textContent).toBe("chat");
    fireEvent.click(screen.getByTestId("chat-view-code"));
    expect(screen.getByTestId("sidebar-view").textContent).toBe("code");
    expect(screen.getByTestId("code-view-stub").textContent).toBe("list in the sidebar");
    // The chat is told it is hidden, so it gives the slot up (the chat page
    // reads `surfaceHidden` and stops portaling its rail).
    expect(screen.getByTestId("fake-chat").getAttribute("data-hidden")).toBe("true");
    slot.remove();
  });
});

describe("the link helpers", () => {
  it("every coding-session address is the Code view's", () => {
    expect(buildSessionHref("bs 7")).toBe("/chat?view=code&session=bs%207");
    expect(lastNightHref("2026-10-26")).toBe("/chat?view=code&night=2026-10-26");
    expect(sessionLinkFor("build:bs_1")).toBe("/chat?view=code&session=bs_1");
    expect(sessionLinkFor("sess_2")).toBe("/sessions/sess_2");
  });

  it("the parsers read the new address AND the old Build one", () => {
    expect(sessionOfHref("/chat?view=code&session=a")).toBe("a");
    expect(sessionOfHref("/?view=code&session=a")).toBe("a");
    expect(sessionOfHref("/terminals?session=b")).toBe("b");
    expect(sessionOfHref("/sessions?session=b")).toBeNull();
    expect(nightOfHref("/chat?view=code&night=2026-10-26")).toBe("2026-10-26");
    expect(nightOfHref("/terminals?night=2026-10-26")).toBe("2026-10-26");
  });

  it("an old Build address maps onto the Code view; a terminal link stays on Build", () => {
    expect(codeHrefForOldBuildSearch("?session=bs_1")).toBe("/chat?view=code&session=bs_1");
    expect(codeHrefForOldBuildSearch("?night=2026-10-26")).toBe("/chat?view=code&night=2026-10-26");
    expect(codeHrefForOldBuildSearch("?focus=t1")).toBeNull();
    expect(codeHrefForOldBuildSearch("")).toBeNull();
  });

  it("the view an address or a link names", () => {
    expect(viewFromSearch("?view=code")).toBe("code");
    expect(viewFromSearch("?session=x")).toBe("code");
    expect(viewFromSearch("?view=chat")).toBe("chat");
    expect(viewFromSearch("?thread=t")).toBe("chat");
    expect(viewFromSearch("")).toBeNull();
    expect(viewOfHref("/chat?view=code")).toBe("code");
    expect(viewOfHref("/terminals?view=code")).toBeNull();
    expect(viewOfHref("/chat")).toBeNull();
  });
});
