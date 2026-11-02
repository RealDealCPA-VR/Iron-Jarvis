/**
 * v1.426.0 (Wave 6, E2) — A REQUEST IS NEVER RETYPED.
 *
 * The audit: "chat's 'use a coding session' offer opens Build › Code empty:
 * the request is retyped". The door (the daemon's coding offer, both chat
 * lanes) now opens Chat › Code (`/chat?view=code`), and the press carries the
 * user's request into the start composer's box — and the chat's project
 * folder — through this tab's sessionStorage (lib/codingStart), never the
 * address. Nothing is made or sent until the user presses Send there.
 *
 *  - lib/codingStart: the request before the reply (never the app's hidden
 *    "carry on" turn or a note sent while it worked), the stage / peek /
 *    clear of the record, its age limit and its size limit;
 *  - DoorsStrip: a door the host handles does not navigate (the chat page
 *    switches to Code in place); every other door is a plain link;
 *  - the REAL IjHarnessView: a staged request opens the composer with the
 *    words in the box and the folder chosen, makes NOTHING on the daemon until
 *    Send, and is taken once (gone after it is shown).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    if (method === "GET") {
      if (!(path in api.gets)) return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
      return Promise.resolve(api.gets[path]);
    }
    if (method === "POST" && path === "/build/sessions") {
      const b = body as { folder?: string };
      return Promise.resolve({
        id: "new1",
        title: "New session",
        updated_at: "2026-11-02T10:00:00",
        running: false,
        waiting: false,
        archived: false,
        folder: b.folder ?? "",
        folder_name: "shop",
        no_folder: !b.folder,
        is_project: false,
        project_id: null,
        mode: "act",
        plan: null,
        setup: {},
        messages: [],
        session_id: "build:new1",
        queued: [],
        turn_id: "",
      });
    }
    return Promise.resolve({});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
  refresh: () => {},
};
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
const MODELS = {
  models: [{ provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true }],
  usable: [{ provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true }],
  loading: false,
};
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { DoorsStrip } from "@/components/chat/DoorsStrip";
import { resetTurnCache } from "@/lib/turnCache";
import { CHAT_VIEW_EVENT, viewOfEvent } from "@/lib/chatSurface";
import {
  CODING_OFFER_HREF,
  CODING_START_KEY,
  CODING_START_MAX_CHARS,
  CODING_START_TTL_MS,
  clearCodingStart,
  isCodingOffer,
  peekCodingStart,
  requestBefore,
  stageCodingStart,
  startCodingFromDoor,
} from "@/lib/codingStart";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };
const DOOR = { href: CODING_OFFER_HREF, label: "Work on this in a coding session (Chat › Code)" };
const REQUEST = "build me a todo app in react, with a page per list";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetTurnCache();
  window.history.replaceState(null, "", "/chat");
  api.calls = [];
  api.gets = {
    "/build/sessions": { folders: [], work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis" },
    "/build/sessions?archived=1": { folders: [] },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(new ReadableStream({ start() {} }), { status: 200 })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const chat = [
  { role: "user", content: "hello" },
  { role: "assistant", content: "Hi." },
  { role: "user", content: REQUEST },
  { role: "user", content: "and dark mode", steer: true },
  { role: "assistant", content: "Sure — here is how.", doors: [DOOR] },
  { role: "user", content: "Continue.", continuation: true },
  { role: "assistant", content: "…and the rest." },
];

describe("lib/codingStart", () => {
  it("the request is the user's own message before the reply (never a note or a hidden turn)", () => {
    expect(requestBefore(chat, 4)).toBe(REQUEST);
    expect(requestBefore(chat, 6)).toBe(REQUEST);
    expect(requestBefore(chat, 1)).toBe("hello");
    expect(requestBefore(chat, 0)).toBe("");
  });

  it("stages, peeks and clears, and only the coding door is handled", () => {
    expect(isCodingOffer(CODING_OFFER_HREF)).toBe(true);
    expect(isCodingOffer("/terminals?view=code")).toBe(false);
    expect(startCodingFromDoor(chat, 4, "/memory", "C:\\work\\shop")).toBe(false);
    expect(sessionStorage.getItem(CODING_START_KEY)).toBeNull();
    stageCodingStart({ text: REQUEST, folder: "C:\\work\\shop" });
    expect(peekCodingStart()).toEqual({ text: REQUEST, folder: "C:\\work\\shop" });
    expect(peekCodingStart()).not.toBeNull(); // a peek never takes it
    clearCodingStart();
    expect(peekCodingStart()).toBeNull();
  });

  it("an old record is ignored, and a long one is cut", () => {
    const t0 = 1_000_000;
    stageCodingStart({ text: "x".repeat(CODING_START_MAX_CHARS + 50), folder: null }, t0);
    expect(peekCodingStart(t0 + 1000)?.text.length).toBe(CODING_START_MAX_CHARS);
    expect(peekCodingStart(t0 + CODING_START_TTL_MS + 1)).toBeNull();
  });

  it("the request never rides the address", () => {
    expect(CODING_OFFER_HREF).toBe("/chat?view=code");
    startCodingFromDoor(chat, 4, DOOR.href, null);
    expect(window.location.href).not.toContain("todo");
  });

  it("on the chat page the press stages the request and switches to the Code view in place", () => {
    const seen: string[] = [];
    const on = (e: Event) => seen.push(viewOfEvent(e) ?? "");
    window.addEventListener(CHAT_VIEW_EVENT, on);
    try {
      expect(startCodingFromDoor(chat, 4, DOOR.href, "C:\\work\\shop")).toBe(true);
    } finally {
      window.removeEventListener(CHAT_VIEW_EVENT, on);
    }
    expect(seen).toEqual(["code"]);
    expect(peekCodingStart()).toEqual({ text: REQUEST, folder: "C:\\work\\shop" });
  });

  it("a blocked storage never throws (the Code view then opens empty)", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => stageCodingStart({ text: REQUEST, folder: null })).not.toThrow();
    expect(peekCodingStart()).toBeNull();
  });
});

describe("DoorsStrip", () => {
  it("a door the host handles does not navigate; any other door is a plain link", () => {
    const seen: string[] = [];
    render(
      <DoorsStrip
        doors={[DOOR, { href: "/memory", label: "Memory" }]}
        onOpen={(d) => {
          seen.push(d.href);
          return d.href === CODING_OFFER_HREF;
        }}
      />,
    );
    const coding = screen.getByRole("link", { name: DOOR.label });
    const handled = fireEvent.click(coding);
    expect(handled).toBe(false); // preventDefault was called
    const plain = fireEvent.click(screen.getByRole("link", { name: "Memory" }));
    expect(plain).toBe(true);
    expect(seen).toEqual([CODING_OFFER_HREF, "/memory"]);
  });
});

describe("the Code view opens with the request typed", () => {
  function renderView() {
    return render(
      <IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
    );
  }

  it("the words are in the box, the folder chosen, and nothing is made until Send", async () => {
    stageCodingStart({ text: REQUEST, folder: "C:\\work\\shop" });
    renderView();
    const box = (await screen.findByTestId("harness-new-box")) as HTMLTextAreaElement;
    expect(box.value).toBe(REQUEST);
    expect(api.calls.filter((c) => c.method === "POST")).toEqual([]);
    // Taken once: a later composer never gets it again.
    await waitFor(() => expect(peekCodingStart()).toBeNull());
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === "/build/sessions")).toBe(true));
    const created = api.calls.find((c) => c.method === "POST" && c.path === "/build/sessions");
    expect((created?.body as { folder?: string }).folder).toBe("C:\\work\\shop");
  });

  it("v1.426.0 review: a request from a Local only chat opens the composer Local only", async () => {
    stageCodingStart({ text: REQUEST, folder: null, localOnly: true });
    renderView();
    await screen.findByTestId("harness-new-box");
    expect(screen.getByTestId("harness-new-local-only")).toHaveAttribute("aria-pressed", "true");
  });

  it("v1.426.0 review: an ordinary chat's request leaves Local only as the user left it (off)", async () => {
    stageCodingStart({ text: REQUEST, folder: null });
    expect(peekCodingStart()).toEqual({ text: REQUEST, folder: null });
    renderView();
    await screen.findByTestId("harness-new-box");
    expect(screen.getByTestId("harness-new-local-only")).toHaveAttribute("aria-pressed", "false");
  });

  it("with nothing staged the composer opens empty, as before", async () => {
    renderView();
    const box = (await screen.findByTestId("harness-new-box")) as HTMLTextAreaElement;
    expect(box.value).toBe("");
  });
});
