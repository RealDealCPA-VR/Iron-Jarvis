import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * v1.325.0 — "Ask Jarvis about this page".
 *
 * Pinned here:
 *  - lib/pageContext's capture reads the page's OWN content (#main-content,
 *    else <main>), never a value being typed, never a dialog / nav / hidden /
 *    `data-no-page-context` part, with block line breaks and table cells kept
 *    apart, capped with an honest "…(cut)" marker;
 *  - the sessionStorage hand-off: take = read + remove, stale after 5 minutes,
 *    malformed / refused storage → null, never a throw;
 *  - the palette row: found by what people say ("explain this page"), absent
 *    on the chat surface, NOT on the empty screen (the five verbs stay five),
 *    and Enter stashes the page and goes to /chat?about=page through the
 *    palette's own router.
 */

const routerMock = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }));

vi.mock("@/lib/api", () => ({
  get: () => Promise.resolve({}),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
  };
});

import {
  ABOUT_PAGE_HREF,
  CHAT_SURFACE_PATHS,
  CUT_MARKER,
  PAGE_CONTEXT_KEY,
  PAGE_CONTEXT_MAX_CHARS,
  PAGE_CONTEXT_TTL_MS,
  capturePageContext,
  isChatSurface,
  stashPageContext,
  takePageContext,
} from "@/lib/pageContext";
import { CommandPalette } from "@/components/CommandPalette";

function setPage(html: string, path = "/usage") {
  window.history.pushState({}, "", path);
  document.body.innerHTML = html;
}

beforeEach(() => {
  routerMock.push.mockReset();
  window.sessionStorage.clear();
  document.title = "";
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
  window.history.pushState({}, "", "/");
  vi.restoreAllMocks();
});

// ── capture ───────────────────────────────────────────────────────────────────

describe("capturePageContext — what the page shows, nothing else", () => {
  it("reads #main-content: title from the page's h1, path, block text", () => {
    setPage(`
      <aside id="sidebar">Chats Projects Settings</aside>
      <main>
        <div id="simulated">Replies are a demo</div>
        <div id="main-content">
          <h1>Usage</h1>
          <p>Tokens this month</p><p>Spend by provider</p>
        </div>
      </main>`);
    const ctx = capturePageContext();
    expect(ctx.title).toBe("Usage");
    expect(ctx.path).toBe("/usage");
    // Two blocks are two lines — words from neighbouring blocks never weld.
    expect(ctx.text).toBe("Usage\nTokens this month\nSpend by provider");
    expect(ctx.text).not.toContain("Chats Projects");
    expect(ctx.text).not.toContain("Replies are a demo");
  });

  it("falls back to <main>, and to document.title when there is no h1", () => {
    setPage(`<main><p>Just text</p></main>`);
    document.title = "Iron Jarvis — Files";
    const ctx = capturePageContext();
    expect(ctx.text).toBe("Just text");
    expect(ctx.title).toBe("Iron Jarvis — Files");
  });

  it("never reads what the user is typing, nor a select's every option", () => {
    setPage(`
      <div id="main-content">
        <label>Subject</label>
        <input value="SSN 123-45-6789" />
        <textarea>draft to the client: my password is hunter2</textarea>
        <select><option>Option A</option><option>Option B</option></select>
        <div contenteditable="true">typed in a rich box</div>
        <p>Visible paragraph</p>
      </div>`);
    // A typed value is a property, not markup — set it the way typing does.
    (document.querySelector("input") as HTMLInputElement).value = "typed secret";
    const { text } = capturePageContext();
    expect(text).toContain("Subject");
    expect(text).toContain("Visible paragraph");
    for (const leak of ["123-45-6789", "typed secret", "hunter2", "Option A", "typed in a rich box"]) {
      expect(text).not.toContain(leak);
    }
  });

  it("skips dialogs, nav, hidden, aria-hidden, inert, data-no-page-context and terminal screens", () => {
    setPage(`
      <div id="main-content">
        <p>Keep me</p>
        <div role="dialog">Search Iron Jarvis</div>
        <nav>Tab one Tab two</nav>
        <div hidden>hidden attr</div>
        <div style="display:none">display none</div>
        <div style="visibility:hidden">visibility hidden</div>
        <div aria-hidden="true">decorative</div>
        <div inert>parked pane</div>
        <div data-no-page-context>private panel</div>
        <div class="xterm"><div class="xterm-rows">export API_KEY=sk-live</div></div>
        <script>var x = "code";</script>
      </div>`);
    const { text } = capturePageContext();
    expect(text).toBe("Keep me");
  });

  it("keeps table cells apart and rows on their own lines; alt text counts", () => {
    setPage(`
      <div id="main-content">
        <table><tr><th>Provider</th><th>Cost</th></tr><tr><td>claude</td><td>$4.20</td></tr></table>
        <img alt="Spend chart" src="x.png" />
      </div>`);
    const { text } = capturePageContext();
    expect(text).toContain("Provider | Cost");
    expect(text).toContain("claude | $4.20");
    expect(text).toContain("Spend chart");
  });

  it("collapses whitespace and caps at 12,000 chars with an honest marker", () => {
    const para = "word ".repeat(4000); // 20,000 chars
    setPage(`<div id="main-content"><p>   a   \t b  </p><p>${para}</p></div>`);
    const { text } = capturePageContext();
    expect(text.startsWith("a b\n")).toBe(true);
    expect(text.length).toBeLessThanOrEqual(PAGE_CONTEXT_MAX_CHARS);
    expect(text.endsWith(CUT_MARKER)).toBe(true);
    expect(text).not.toMatch(/ {2}/);
  });

  it("a page within the cap carries no marker", () => {
    setPage(`<div id="main-content"><p>short</p></div>`);
    expect(capturePageContext().text).toBe("short");
  });

  it("never throws on a document it cannot read", () => {
    const broken = {
      defaultView: null,
      getElementById: () => {
        throw new Error("boom");
      },
    } as unknown as Document;
    expect(capturePageContext(broken)).toEqual({ title: "", path: "", text: "" });
  });
});

// ── hand-off ──────────────────────────────────────────────────────────────────

describe("stash / take — one landing, one use, five minutes", () => {
  const ctx = { title: "Usage", path: "/usage", text: "Tokens" };

  it("take returns the stash once and removes it", () => {
    expect(stashPageContext(ctx, 1_000)).toBe(true);
    expect(window.sessionStorage.getItem(PAGE_CONTEXT_KEY)).not.toBeNull();
    expect(takePageContext(2_000)).toEqual(ctx);
    expect(window.sessionStorage.getItem(PAGE_CONTEXT_KEY)).toBeNull();
    expect(takePageContext(2_000)).toBeNull();
  });

  it("older than five minutes is null (and still removed)", () => {
    stashPageContext(ctx, 0);
    expect(takePageContext(PAGE_CONTEXT_TTL_MS + 1)).toBeNull();
    expect(window.sessionStorage.getItem(PAGE_CONTEXT_KEY)).toBeNull();
  });

  it("exactly at the edge is still fresh", () => {
    stashPageContext(ctx, 0);
    expect(takePageContext(PAGE_CONTEXT_TTL_MS)).toEqual(ctx);
  });

  it("malformed or wrong-shaped stashes are null, and over-long fields are re-bounded", () => {
    window.sessionStorage.setItem(PAGE_CONTEXT_KEY, "{not json");
    expect(takePageContext()).toBeNull();
    window.sessionStorage.setItem(PAGE_CONTEXT_KEY, JSON.stringify({ ctx: "x", at: Date.now() }));
    expect(takePageContext()).toBeNull();
    window.sessionStorage.setItem(PAGE_CONTEXT_KEY, JSON.stringify({ ctx }));
    expect(takePageContext()).toBeNull();
    window.sessionStorage.setItem(
      PAGE_CONTEXT_KEY,
      JSON.stringify({ ctx: { title: "t".repeat(500), path: 7, text: "x".repeat(20_000) }, at: Date.now() }),
    );
    const got = takePageContext();
    expect(got?.title.length).toBeLessThanOrEqual(200);
    expect(got?.path).toBe("");
    expect(got?.text.length).toBeLessThanOrEqual(PAGE_CONTEXT_MAX_CHARS);
    expect(got?.text.endsWith(CUT_MARKER)).toBe(true);
  });

  it("refused storage is false / null, never a throw", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceeded");
    });
    expect(stashPageContext(ctx)).toBe(false);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(takePageContext()).toBeNull();
  });
});

describe("the chat surface", () => {
  it("is / and /chat (a trailing slash too), nothing else", () => {
    expect(isChatSurface("/")).toBe(true);
    expect(isChatSurface("/chat")).toBe(true);
    expect(isChatSurface("/chat/")).toBe(true);
    expect(isChatSurface("/usage")).toBe(false);
    expect(isChatSurface("/chatter")).toBe(false);
  });

  it("agrees with the sidebar's CHAT_PATHS (one list, copied on purpose)", () => {
    const src = readFileSync(
      join(process.cwd(), "components", "AppSidebar.tsx"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    const m = src.match(/export const CHAT_PATHS = (\[[^\]]*\]);/);
    expect(m).not.toBeNull();
    expect(JSON.parse((m as RegExpMatchArray)[1])).toEqual([...CHAT_SURFACE_PATHS]);
  });
});

// ── the palette row ──────────────────────────────────────────────────────────

const ROW = "Ask Jarvis about this page";
const box = () => screen.getByRole("combobox") as HTMLInputElement;
const labels = () => screen.queryAllByRole("option").map((o) => o.textContent || "");

async function openPalette() {
  await act(async () => {
    window.dispatchEvent(new Event("ij:open-palette"));
  });
}
async function type(value: string) {
  await act(async () => {
    fireEvent.change(box(), { target: { value } });
  });
}

describe("CommandPalette — Ask Jarvis about this page", () => {
  it.each(["explain this page", "summarize this page", "what is this", "help with this page", "about this page"])(
    "is found by %j",
    async (q) => {
      setPage(`<div id="main-content"><h1>Usage</h1></div>`, "/usage");
      render(<CommandPalette />);
      await openPalette();
      await type(q);
      expect(labels()[0]).toContain(ROW);
    },
  );

  it("Enter captures the page, stashes it and opens /chat?about=page", async () => {
    setPage(`<div id="main-content"><h1>Usage</h1><p>Spend by provider</p></div>`, "/usage");
    render(<CommandPalette />);
    await openPalette();
    await type("explain this page");
    await act(async () => {
      fireEvent.keyDown(box(), { key: "Enter" });
    });
    expect(routerMock.push).toHaveBeenCalledWith(ABOUT_PAGE_HREF);
    expect(ABOUT_PAGE_HREF).toBe("/chat?about=page");
    const got = takePageContext();
    expect(got?.title).toBe("Usage");
    expect(got?.path).toBe("/usage");
    expect(got?.text).toContain("Spend by provider");
    // The palette itself (a dialog) never lands in the capture.
    expect(got?.text).not.toContain("Search");
  });

  it.each(["/", "/chat"])("is not offered on the chat surface (%s)", async (path) => {
    setPage(`<div id="main-content"><h1>Chat</h1></div>`, path);
    render(<CommandPalette />);
    await openPalette();
    await type("explain this page");
    expect(labels().some((l) => l.includes(ROW))).toBe(false);
  });

  it("is not on the empty screen: the five verbs stay five", async () => {
    setPage(`<div id="main-content"><h1>Usage</h1></div>`, "/usage");
    render(<CommandPalette />);
    await openPalette();
    expect(labels().some((l) => l.includes(ROW))).toBe(false);
    expect(screen.getByText("Do something")).toBeInTheDocument();
    expect(labels()).toHaveLength(5);
  });

  it("follows the page the palette is opened on", async () => {
    setPage(`<div id="main-content"><h1>Usage</h1></div>`, "/chat");
    render(<CommandPalette />);
    await openPalette();
    await type("explain this page");
    expect(labels().some((l) => l.includes(ROW))).toBe(false);
    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    window.history.pushState({}, "", "/usage");
    await openPalette();
    await type("explain this page");
    expect(labels()[0]).toContain(ROW);
  });
});
