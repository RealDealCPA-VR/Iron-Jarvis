/**
 * The intelligence plan, release 6 — "What is filling this chat".
 *
 * Pressing the fullness meter in a PLAIN chat opens the list coding sessions
 * have had since v1.415.0 (components/harness/ContextBreakdown), in a plain
 * chat's words: your messages and the replies, files you attached, what Iron
 * Jarvis remembered, the tools it was given. Every size is an estimate, said
 * as a share ("about 30%"), never a count, and no engine word is on screen.
 *
 * The daemon's answers are the REAL ones: fixtures/intel-06-chat-filling.json
 * is written by tests/test_intel_06_chat_fullness.py (IJ_REGEN_INTEL_06=1)
 * from `GET /chat/threads/{id}/filling`.
 *
 * Harness for the page: the chat-words-v13560 mocks (ChatPage rendered for
 * real; an unmocked GET answers {}), with a stream whose result carries a
 * fullness report.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import fixture from "./fixtures/intel-06-chat-filling.json";
import harnessFixture from "./fixtures/context-breakdown-v1415-0.json";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      responses: {} as Record<string, unknown>,
      /** A GET that stays unanswered until the test lets it go. */
      held: {} as Record<string, (value: unknown) => void>,
    },
    stream: {
      context: null as Record<string, unknown> | null,
      /** True: a reply stays running until the test lets it end (`release`). */
      hold: false,
      release: null as null | (() => void),
      streaming: false,
      aborts: 0,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.responses[path];
    if (r === "HOLD") return new Promise((resolve) => (H.api.held[path] = resolve));
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    // Read when asked (the page's Stop reads it at the press), so a held
    // reply is "streaming" for as long as the test holds it.
    get streaming() {
      return H.stream.streaming;
    },
    text: "",
    tools: [],
    approval: null,
    run: () => {
      const result = {
        reply: "Noted.",
        tools_used: [],
        ...(H.stream.context ? { context: H.stream.context } : {}),
      };
      if (!H.stream.hold) return Promise.resolve(result);
      H.stream.streaming = true;
      return new Promise((resolve) => {
        H.stream.release = () => {
          H.stream.streaming = false;
          resolve(result);
        };
      });
    },
    abort: () => {
      H.stream.aborts += 1;
    },
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: true, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import { ChatFilling } from "@/components/chat/ChatFilling";
import { ContextBreakdown } from "@/components/harness/ContextBreakdown";
import { MORE_THAN_THE_ROOM, aboutShare, decodeBreakdown } from "@/lib/contextBreakdown";
import {
  FILLING_ESTIMATE,
  FILLING_FAILED,
  FILLING_LOADING,
  FILLING_NONE,
  FILLING_TITLE,
  OVER_SHARES,
  decodeChatFilling,
  fillingMeterLabel,
  fillingPath,
  leftOutWords,
  overLeftWords,
  overWords,
} from "@/lib/chatFilling";

/** The engine words a plain chat never shows. */
const ENGINE_WORDS = /token|context|prompt|cache|\bsystem\b/i;

const HALF = {
  window: 100_000, used: 43_000, headroom: 57_000, dropped: 0, tools_trimmed: 0, clipped: false,
  recap: false, suggest_larger: false, percent: 60, level: "ok", suggest_at: 70, auto_at: 92,
};

const box = async () => (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;

async function sendHello() {
  const b = await box();
  fireEvent.change(b, { target: { value: "hello" } });
  fireEvent.keyDown(b, { key: "Enter" });
  await screen.findAllByText("Noted.");
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.held = {};
  H.stream.context = null;
  H.stream.hold = false;
  H.stream.release = null;
  H.stream.streaming = false;
  H.stream.aborts = 0;
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ========================================================== the list itself */

describe("the list, from the daemon's real answer", () => {
  const open = (threadId: string | null = "chat_mine", onClose = () => {}) =>
    render(<ChatFilling threadId={threadId} onClose={onClose} />);

  it("is titled 'What is filling this chat' and says each part in plain words with its share", async () => {
    H.api.responses[fillingPath("chat_mine")] = fixture.filled;
    open();
    const parts = await screen.findByTestId("chat-filling-context-parts");
    expect(screen.getByTestId("chat-filling-title").textContent).toBe("What is filling this chat");
    expect(FILLING_TITLE).toBe("What is filling this chat");
    expect(screen.getByRole("dialog", { name: "What is filling this chat" })).toBeTruthy();
    // The first line is the meter's own sentence for the same reading.
    expect(screen.getByTestId("chat-filling-context-percent").textContent).toBe(
      "This chat fills about 43% of what this model can read at once.",
    );
    const rows = Array.from(parts.querySelectorAll("li")).map((li) => li.textContent);
    expect(rows).toEqual([
      "Your messages and the repliesabout 30%",
      "Files you attachedabout 8%",
      "What Iron Jarvis rememberedabout 2%",
      "Iron Jarvis's own instructionsabout 3%",
      "Room leftabout 57%",
    ]);
    // The tools: sent with each message, listed apart, with a share.
    const extra = screen.getByTestId("chat-filling-context-extra");
    expect(extra.textContent).toContain("Sent with each message, not counted above:");
    expect(extra.querySelector("li")?.textContent).toBe("Tools it was givenabout 7%");
    // Where they came from: Iron Jarvis's own, and each extension by name.
    const tools = screen.getByTestId("chat-filling-context-list-tools");
    expect(within(tools).getByRole("button").textContent).toBe("Where they came from3");
    fireEvent.click(within(tools).getByRole("button"));
    expect(Array.from(tools.querySelectorAll("li")).map((li) => li.textContent)).toEqual([
      "Iron Jarvis's own tools2 toolsless than 1%",
      "ledger12 toolsabout 5%",
      "mail3 toolsless than 1%",
    ]);
    // Iron Jarvis's own tools, each in the approval card's words: never an id.
    const own = screen.getByTestId("chat-filling-context-list-own_tools");
    fireEvent.click(within(own).getByRole("button"));
    expect(Array.from(own.querySelectorAll("li")).map((li) => li.textContent)).toEqual(["Read a file", "Look in a folder"]);
    expect(own.textContent).not.toMatch(/read_file|list_files/);
    // Earlier messages left out, and that the sizes are estimates.
    expect(screen.getByTestId("chat-filling-context-note").textContent).toBe(
      `At least 3 earlier messages were left out of the last reply so it fits. ${FILLING_ESTIMATE}`,
    );
  });

  it("shows no engine word and no count anywhere in the list", async () => {
    H.api.responses[fillingPath("chat_mine")] = fixture.filled;
    open();
    const panel = await screen.findByTestId("chat-filling");
    await screen.findByTestId("chat-filling-context-parts");
    for (const fold of panel.querySelectorAll<HTMLButtonElement>("[aria-expanded='false']")) fireEvent.click(fold);
    expect(panel.textContent ?? "").not.toMatch(ENGINE_WORDS);
    // Sizes are shares only: none of the record's counts is drawn, in any form.
    expect(panel.textContent ?? "").not.toMatch(/30,?000|30k|43,?000|43k|100k|57k|6\.6k|5\.1k|\b900\b/);
    // Every hover and screen-reader word too.
    const spoken = Array.from(panel.querySelectorAll("[title],[aria-label]"))
      .map((n) => `${n.getAttribute("title") ?? ""} ${n.getAttribute("aria-label") ?? ""}`)
      .join(" ");
    expect(spoken).not.toMatch(ENGINE_WORDS);
    expect(spoken).not.toMatch(/30k|43k|100k/);
    // The coding session's own controls are not here: no fold on the title, no Compact, no Context word.
    expect(screen.queryByTestId("chat-filling-context-toggle")).toBeNull();
    expect(screen.queryByTestId("chat-filling-context-compact")).toBeNull();
    expect(screen.queryByTestId("chat-filling-context-window")).toBeNull();
  });

  it("a room that is a safe size says so, and a reply sent no tools has no tools row", async () => {
    H.api.responses[fillingPath("chat_small")] = fixture.no_tools_safe_size;
    open("chat_small");
    await screen.findByTestId("chat-filling-context-parts");
    expect(screen.getByTestId("chat-filling-context-percent").textContent).toBe(
      "This chat fills about 52% of what Iron Jarvis sends this model at once. This model's own limit is not known, so a safe size is used.",
    );
    expect(screen.queryByTestId("chat-filling-context-extra")).toBeNull();
    expect(screen.queryByTestId("chat-filling-context-list-tools")).toBeNull();
    expect(screen.getByTestId("chat-filling-context-note").textContent).toBe(FILLING_ESTIMATE);
  });

  it("says it is reading, then that there is no list yet, in plain words", async () => {
    H.api.responses[fillingPath("chat_new")] = "HOLD";
    open("chat_new");
    expect((await screen.findByTestId("chat-filling-context-none")).textContent).toBe(FILLING_LOADING);
    H.api.held[fillingPath("chat_new")](fixture.none);
    await waitFor(() => expect(screen.getByTestId("chat-filling-context-none").textContent).toBe(FILLING_NONE));
    expect(screen.getByTestId("chat-filling-title").textContent).toBe("What is filling this chat");
    expect(screen.queryByTestId("chat-filling-context-parts")).toBeNull();
    expect(screen.getByTestId("chat-filling").textContent ?? "").not.toMatch(ENGINE_WORDS);
  });

  it("a chat that is not saved yet asks nothing of the daemon and says there is no list yet", async () => {
    open(null);
    expect((await screen.findByTestId("chat-filling-context-none")).textContent).toBe(FILLING_NONE);
    expect(H.api.gets.filter((p) => p.includes("/filling"))).toEqual([]);
  });

  it("a read that fails says so and never shows a made-up list", async () => {
    H.api.responses[fillingPath("chat_mine")] = new H.FakeApiError("boom", 500);
    open();
    await waitFor(() => expect(screen.getByTestId("chat-filling-context-none").textContent).toBe(FILLING_FAILED));
    expect(screen.queryByTestId("chat-filling-context-parts")).toBeNull();
    expect(screen.getByTestId("chat-filling").textContent).not.toContain("boom");
  });

  it("Escape and the Close press close it", async () => {
    H.api.responses[fillingPath("chat_mine")] = fixture.filled;
    const onClose = vi.fn();
    open("chat_mine", onClose);
    await screen.findByTestId("chat-filling-context-parts");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("chat-filling-close"));
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("chat-filling-close").getAttribute("aria-label")).toBe("Close");
  });

  it("fits a phone: the panel is never wider than the screen less its margins, and scrolls inside", async () => {
    H.api.responses[fillingPath("chat_mine")] = fixture.filled;
    open();
    const panel = await screen.findByTestId("chat-filling");
    expect(panel.className).toContain("w-[min(26rem,calc(100vw-2rem))]");
    expect(panel.className).toContain("overflow-y-auto");
    expect(panel.className).toContain("max-h-[min(70vh,560px)]");
    // A long row's words shrink (truncate) before the share is pushed off the edge.
    await screen.findByTestId("chat-filling-context-parts");
    const row = screen.getByTestId("chat-filling-context-parts").querySelector("li") as HTMLElement;
    expect(row.children[1].className).toContain("min-w-0");
    expect(row.children[1].className).toContain("truncate");
    expect(row.children[2].className).toContain("shrink-0");
  });
});

/* ================================== a chat that holds more than its room */

/** Every "N%" a piece of text says. */
const percents = (text: string): number[] => Array.from(text.matchAll(/(\d+)\s*%/g)).map((m) => Number(m[1]));

describe("a chat that holds more than its room: one record, one set of words", () => {
  const said = fixture.said as Record<"filled" | "no_tools_safe_size" | "over_full", string[]>;

  /** The list for one of the fixture's chats, every fold opened. */
  async function list(name: "filled" | "no_tools_safe_size" | "over_full") {
    H.api.responses[fillingPath("chat_x")] = fixture[name];
    render(<ChatFilling threadId="chat_x" onClose={() => {}} />);
    const panel = await screen.findByTestId("chat-filling");
    await screen.findByTestId("chat-filling-context-parts");
    for (const fold of panel.querySelectorAll<HTMLButtonElement>("[aria-expanded='false']")) fireEvent.click(fold);
    return panel;
  }

  it("says so in place of a percent, each part as its share of what the chat holds, and the room left as none", async () => {
    const panel = await list("over_full");
    expect(screen.getByTestId("chat-filling-context-percent").textContent).toBe(
      "This chat holds more than this model can read at once.",
    );
    const rows = Array.from(screen.getByTestId("chat-filling-context-parts").querySelectorAll("li")).map((li) => li.textContent);
    expect(rows).toEqual([
      "Your messages and the repliesabout 95%", // 8,000 of the 8,400 it holds (133% of its room)
      "Iron Jarvis's own instructionsabout 5%",
      "Room leftnone",
    ]);
    // The tools are sent beside the chat: their share is of the room, and says so.
    expect(screen.getByTestId("chat-filling-context-extra").querySelector("li")?.textContent).toBe(
      "Tools it was givenabout 12% of the room",
    );
    expect(Array.from(screen.getByTestId("chat-filling-context-list-tools").querySelectorAll("li")).map((li) => li.textContent)).toEqual([
      "Iron Jarvis's own tools2 toolsabout 5% of the room",
      "ledger12 toolsabout 7% of the room",
    ]);
    // Said once: the oldest messages are left out, with the saved count.
    expect(screen.getByTestId("chat-filling-context-note").textContent).toBe(
      `Iron Jarvis leaves the oldest messages out of each reply so it fits (at least 22 in the newest reply). ${OVER_SHARES} ${FILLING_ESTIMATE}`,
    );
    expect(overLeftWords(0)).toBe("Iron Jarvis leaves the oldest messages out of each reply so it fits.");
    // No figure above 100 anywhere: on screen, in a hover, or for a screen reader.
    const spoken = Array.from(panel.querySelectorAll("[title],[aria-label]"))
      .map((n) => `${n.getAttribute("title") ?? ""} ${n.getAttribute("aria-label") ?? ""}`)
      .join(" ");
    for (const n of percents(`${panel.textContent ?? ""} ${spoken}`)) expect(n).toBeLessThanOrEqual(100);
    expect(`${panel.textContent ?? ""} ${spoken}`).not.toMatch(/133|140|about 100%/);
    // The bar is drawn on the same shares (it adds up to the whole bar).
    const widths = Array.from(screen.getByTestId("chat-filling-context-stack").children).map((c) => parseFloat((c as HTMLElement).style.width));
    expect(Math.round(widths.reduce((a, b) => a + b, 0))).toBe(100);
    // The screen reader's bar label follows the same words.
    expect(screen.getByTestId("chat-filling-context-stack").getAttribute("aria-label")).toBe(
      "This chat holds more than this model can read at once. Your messages and the replies about 95%, Iron Jarvis's own instructions about 5%",
    );
  });

  it.each(["filled", "no_tools_safe_size", "over_full"] as const)(
    "the list and the chat's own answer say the same words for the same reply (%s)",
    async (name) => {
      const panel = await list(name);
      const lines = said[name];
      // The first line of the list is the answer's first line.
      expect(screen.getByTestId("chat-filling-context-percent").textContent).toBe(lines[1]);
      // Every row of the list is a line of the answer: the same name, the same share.
      const rows = Array.from(screen.getByTestId("chat-filling-context-parts").querySelectorAll("li"));
      const used = rows.filter((li) => li.getAttribute("data-part") !== "free");
      expect(used.length).toBeGreaterThan(0);
      for (const li of used) {
        const [label, share] = [li.children[1].textContent, li.children[2].textContent];
        expect(lines).toContain(`- ${label}: ${share}`);
      }
      const free = rows.find((li) => li.getAttribute("data-part") === "free") as HTMLElement;
      if (name === "over_full") {
        expect(lines).toContain(`- Room left: ${free.children[2].textContent}`);
        // The sentence about the oldest messages, word for word, once in each.
        const note = screen.getByTestId("chat-filling-context-note").textContent ?? "";
        const line = lines.find((l) => l.includes("leaves the oldest messages out")) as string;
        expect(note.startsWith(line.replace(/^- /, ""))).toBe(true);
      }
      // The tools' share, where the reply was given any.
      const tools = screen.queryByTestId("chat-filling-context-extra")?.querySelector("li");
      const toolLine = lines.find((l) => l.startsWith("- Tools it was given"));
      expect(!!tools).toBe(!!toolLine);
      if (tools && toolLine) expect(toolLine).toContain(`, ${tools.children[2].textContent}: `);
      // And neither says a figure above 100.
      for (const n of percents(`${panel.textContent ?? ""} ${lines.join(" ")}`)) expect(n).toBeLessThanOrEqual(100);
    },
  );

  it("a chat inside its room keeps its percent and its shares of the room", async () => {
    await list("filled");
    expect(decodeChatFilling(fixture.filled)?.over).toBe(false);
    expect(decodeChatFilling(fixture.over_full)?.over).toBe(true);
    expect(screen.getByTestId("chat-filling-context-extra").querySelector("li")?.textContent).toBe("Tools it was givenabout 7%");
    expect(screen.getByTestId("chat-filling-context-stack").getAttribute("aria-label")).toBe(
      "This chat fills about 43% of what this model can read at once. Your messages and the replies about 30%, Files you attached about 8%, What Iron Jarvis remembered about 2%, Iron Jarvis's own instructions about 3%",
    );
  });
});

/* ============================================================== the decoder */

describe("the decoder and the words", () => {
  it("keeps the daemon's numbers and words Iron Jarvis's own tools through the cards' table", () => {
    const got = decodeChatFilling(fixture.filled);
    expect(got?.found).toBe(true);
    expect(got?.leftOut).toBe(3);
    expect(got?.windowKnown).toBe(true);
    expect(got?.reading?.parts.map((p) => [p.key, p.tokens])).toEqual([
      ["messages", 30000], ["attachments", 8000], ["memory", 2000], ["system", 3000], ["free", 57000],
    ]);
    expect(got?.reading?.lists.map((l) => [l.key, l.label, l.count])).toEqual([
      ["tools", "Where they came from", 3],
      ["own_tools", "Iron Jarvis's own tools", 2],
    ]);
  });

  it("a tool the table cannot word is left out of the names, never shown by its id", () => {
    const raw = JSON.parse(JSON.stringify(fixture.filled));
    raw.reading.tool_names = ["read_file", "zz_made_up_tool", "mcp__ledger__post", 7, "read_file"];
    const own = decodeChatFilling(raw)?.reading?.lists.find((l) => l.key === "own_tools");
    expect(own?.items.map((i) => i.name)).toEqual(["Read a file"]);
    expect(own?.count).toBe(2);
  });

  it("an answer that is not found, or is junk, has no list", () => {
    expect(decodeChatFilling(fixture.none)).toEqual({
      found: false, reading: null, windowKnown: true, leftOut: 0, over: false,
    });
    expect(decodeChatFilling(null)).toBeNull();
    expect(decodeChatFilling("nope")).toBeNull();
    expect(decodeChatFilling({ found: true, reading: { window: 0 } })?.found).toBe(false);
  });

  it("a share is always 'about', and a sliver is 'less than 1%', never 0", () => {
    expect(aboutShare(30_000, 100_000)).toBe("about 30%");
    expect(aboutShare(900, 100_000)).toBe("less than 1%");
    expect(aboutShare(0, 100_000)).toBe("about 0%");
    expect(aboutShare(5, 0)).toBe("");
    expect(leftOutWords(1)).toBe("At least 1 earlier message was left out of the last reply so it fits.");
    expect(leftOutWords(0)).toBe("");
    expect(fillingMeterLabel(60)).toBe("This chat is about 60% full. Press to see what is filling it.");
    expect(fillingMeterLabel(100)).toBe("This chat is about 100% full. Press to see what is filling it.");
    // Over its room: the list's own first words, never "about 100% full" or a percent above 100.
    expect(fillingMeterLabel(162)).toBe(
      "This chat holds more than this model can read at once. Press to see what is filling it.",
    );
    expect(fillingMeterLabel(162, false)).toBe(`${overWords(false)} Press to see what is filling it.`);
    // A size bigger than what it is a share of is said in words, never as a percent above 100.
    expect(aboutShare(162_000, 100_000)).toBe(MORE_THAN_THE_ROOM);
    expect(aboutShare(100_000, 100_000)).toBe("about 100%");
    expect(fillingPath("a b/c")).toBe("/chat/threads/a%20b%2Fc/filling");
  });
});

/* ============================================ the coding session is unchanged */

describe("the coding session's drop-down is what it was", () => {
  it("without the plain words it keeps its fold, its counts and its own words", () => {
    const reading = decodeBreakdown((harnessFixture as { harness_line_context: { breakdown: unknown } }).harness_line_context.breakdown);
    expect(reading).not.toBeNull();
    render(<ContextBreakdown prefix="session-rail" reading={reading} emptyWords="none" />);
    const toggle = screen.getByTestId("session-rail-context-toggle");
    expect(toggle.textContent).toBe("Context");
    expect(screen.queryByTestId("session-rail-title")).toBeNull();
    expect(screen.getByTestId("session-rail-context-percent").textContent).toBe(`${reading!.percent}% used`);
    expect(screen.getByTestId("session-rail-context-window").textContent).toMatch(/ \/ /);
    const first = screen.getByTestId("session-rail-context-parts").querySelector("li") as HTMLElement;
    // label, the count, the percent with one decimal: three cells after the swatch.
    expect(first.children.length).toBe(4);
    expect(first.children[3].textContent).toMatch(/^(<0\.1|\d+\.\d)%$/);
    expect(first.textContent).not.toContain("about");
  });
});

/* ======================================================= the meter opens it */

describe("the fullness meter in a plain chat opens the list", () => {
  it("is a press from half full; pressing it reads this chat's record and shows the list", async () => {
    H.stream.context = HALF;
    H.api.responses[fillingPath("t1")] = fixture.filled;
    render(<ChatPage />);
    await sendHello();
    const meter = await screen.findByTestId("chat-fullness-meter");
    expect(meter.tagName).toBe("BUTTON");
    expect(meter.textContent).toBe("60%");
    expect(meter.getAttribute("aria-label")).toBe("This chat is about 60% full. Press to see what is filling it.");
    expect(meter.getAttribute("aria-expanded")).toBe("false");
    // Shown at phone width too (it used to be hidden below the small breakpoint).
    expect(meter.className).not.toMatch(/\bhidden\b/);
    expect(screen.queryByTestId("chat-filling")).toBeNull();
    expect(H.api.gets.filter((p) => p.includes("/filling"))).toEqual([]);

    fireEvent.click(meter);
    expect(await screen.findByTestId("chat-filling-title")).toBeTruthy();
    expect(screen.getByTestId("chat-filling-title").textContent).toBe("What is filling this chat");
    await screen.findByTestId("chat-filling-context-parts");
    expect(H.api.gets.filter((p) => p.includes("/filling"))).toEqual([fillingPath("t1")]);
    expect(meter.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("chat-filling").textContent).toContain("Your messages and the replies");
    expect(screen.getByTestId("chat-filling").textContent ?? "").not.toMatch(ENGINE_WORDS);

    // Pressing the meter again closes it; opening it again reads the record again.
    fireEvent.click(meter);
    expect(screen.queryByTestId("chat-filling")).toBeNull();
    fireEvent.click(meter);
    await screen.findByTestId("chat-filling-context-parts");
    expect(H.api.gets.filter((p) => p.includes("/filling"))).toEqual([fillingPath("t1"), fillingPath("t1")]);
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("chat-filling")).toBeNull());
  });

  it("stays quiet below half full: no meter, no list, no read", async () => {
    H.stream.context = { ...HALF, percent: 30 };
    render(<ChatPage />);
    await sendHello();
    expect(screen.queryByTestId("chat-fullness-meter")).toBeNull();
    expect(screen.queryByTestId("chat-filling")).toBeNull();
    expect(H.api.gets.filter((p) => p.includes("/filling"))).toEqual([]);
  });

  it("in a chat that is not half full, the chat's own menu opens the same list", async () => {
    H.stream.context = { ...HALF, percent: 12 };
    H.api.responses[fillingPath("t1")] = fixture.filled;
    render(<ChatPage />);
    await sendHello();
    expect(screen.queryByTestId("chat-fullness-meter")).toBeNull();
    fireEvent.click(await screen.findByTestId("chat-more-trigger"));
    const row = await screen.findByTestId("chat-more-filling");
    expect(row.textContent).toBe("What is filling this chat");
    fireEvent.click(row);
    // The menu closes and the one list opens, read for this chat.
    expect(await screen.findByTestId("chat-filling-title")).toBeTruthy();
    await screen.findByTestId("chat-filling-context-parts");
    expect(screen.queryByTestId("chat-more-filling")).toBeNull();
    expect(H.api.gets.filter((p) => p.includes("/filling"))).toEqual([fillingPath("t1")]);
    expect(screen.getAllByTestId("chat-filling").length).toBe(1);
    fireEvent.click(screen.getByTestId("chat-filling-close"));
    expect(screen.queryByTestId("chat-filling")).toBeNull();
  });

  it("a chat that is not saved yet has no such row (there is no record to read)", async () => {
    render(<ChatPage />);
    fireEvent.click(await screen.findByTestId("chat-more-trigger"));
    await waitFor(() => expect(screen.getByTestId("chat-more-trigger").getAttribute("aria-expanded")).toBe("true"));
    expect(screen.queryByTestId("chat-more-filling")).toBeNull();
  });

  it("a chat over its room: the meter's name says so, never 'about 100% full'", async () => {
    H.stream.context = { ...HALF, percent: 162 };
    render(<ChatPage />);
    await sendHello();
    const meter = await screen.findByTestId("chat-fullness-meter");
    expect(meter.getAttribute("aria-label")).toBe(
      "This chat holds more than this model can read at once. Press to see what is filling it.",
    );
    expect(meter.getAttribute("title")).toBe("This chat holds more than this model can read at once.");
    expect(`${meter.getAttribute("aria-label")} ${meter.getAttribute("title")}`).not.toMatch(/162|100/);
  });
});

/* ============================================ one Escape, one thing closed */

describe("Escape closes the list and nothing else", () => {
  it("with a reply running and the message box focused: one Escape closes the list, the next stops the reply", async () => {
    H.stream.context = HALF;
    H.api.responses[fillingPath("t1")] = fixture.filled;
    render(<ChatPage />);
    await sendHello();
    // A second reply, held running.
    H.stream.hold = true;
    const b = await box();
    fireEvent.change(b, { target: { value: "and another" } });
    fireEvent.keyDown(b, { key: "Enter" });
    await waitFor(() => expect(H.stream.release).not.toBeNull());
    // Opened from the meter (focus is on it, as after a real press), then the reader moves to the message box.
    const meter = await screen.findByTestId("chat-fullness-meter");
    meter.focus();
    fireEvent.click(meter);
    await screen.findByTestId("chat-filling-context-parts");
    const busy = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    busy.focus();
    expect(document.activeElement).toBe(busy);
    const pulledBack = vi.spyOn(meter, "focus");

    // One Escape: the list closes; the reply is not stopped and the box never sees the key.
    const reached: string[] = [];
    const boxSaw = (e: KeyboardEvent) => e.key === "Escape" && reached.push("box");
    busy.addEventListener("keydown", boxSaw);
    try {
      const notPrevented = fireEvent.keyDown(busy, { key: "Escape" });
      expect(notPrevented).toBe(false);
    } finally {
      busy.removeEventListener("keydown", boxSaw);
    }
    await waitFor(() => expect(screen.queryByTestId("chat-filling")).toBeNull());
    expect(reached).toEqual([]);
    expect(H.stream.aborts).toBe(0);
    // The reader stays in the message box: focus is never pulled back to the meter, not even for a moment.
    expect(pulledBack).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(busy);

    // The next Escape is the message box's own: it stops the reply.
    fireEvent.keyDown(busy, { key: "Escape" });
    await waitFor(() => expect(H.stream.aborts).toBe(1));
  });
});

describe("the chat kept behind the Code view", () => {
  it("source: the list is closed and not drawn there (its Escape is a window key that chat never answers)", () => {
    // The page's own precedent for this rule is a source pin
    // (wave-d3-review-questions-v1450): the Code view is not mounted here.
    const src = readFileSync(join(process.cwd(), "app/chat/page.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src.split("{fillingOpen && !surfaceHidden ? (").length).toBe(2);
    expect(src).not.toMatch(/\{fillingOpen \? </);
    const i = src.indexOf("const fillingReturnTarget = useCallback(");
    expect(i).toBeGreaterThan(0);
    expect(src.slice(i, i + 500)).toContain(
      "useEffect(() => {\n    if (surfaceHidden) setFillingOpen(false);\n  }, [surfaceHidden]);",
    );
  });
});

/* ====================================== one corner, focus, and a fresh list */

describe("the list and the conversation map share one corner", () => {
  async function twoQuestions() {
    H.stream.context = HALF;
    H.api.responses[fillingPath("t1")] = fixture.filled;
    render(<ChatPage />);
    await sendHello();
    const b = await box();
    fireEvent.change(b, { target: { value: "hello again" } });
    fireEvent.keyDown(b, { key: "Enter" });
    await waitFor(() => expect((screen.getByTestId("open-conversation-map") as HTMLButtonElement).disabled).toBe(false));
  }

  it("opening one closes the other, from the meter, the map's button and the menu's row", async () => {
    await twoQuestions();
    const map = screen.getByTestId("open-conversation-map");
    const meter = await screen.findByTestId("chat-fullness-meter");
    fireEvent.click(map);
    expect(await screen.findByTestId("conversation-map")).toBeTruthy();
    // The meter opens the list: the map closes.
    fireEvent.click(meter);
    expect(await screen.findByTestId("chat-filling")).toBeTruthy();
    expect(screen.queryByTestId("conversation-map")).toBeNull();
    expect(map.getAttribute("aria-expanded")).toBe("false");
    // The map's button opens the map: the list closes.
    fireEvent.click(map);
    expect(await screen.findByTestId("conversation-map")).toBeTruthy();
    expect(screen.queryByTestId("chat-filling")).toBeNull();
    expect(meter.getAttribute("aria-expanded")).toBe("false");
    // The menu's row opens the list: the map closes.
    fireEvent.click(screen.getByTestId("chat-more-trigger"));
    fireEvent.click(await screen.findByTestId("chat-more-filling"));
    expect(await screen.findByTestId("chat-filling")).toBeTruthy();
    expect(screen.queryByTestId("conversation-map")).toBeNull();
  });

  it("focus moves into the list when it opens and back to the meter when it closes", async () => {
    await twoQuestions();
    const meter = await screen.findByTestId("chat-fullness-meter");
    meter.focus();
    fireEvent.click(meter);
    const panel = await screen.findByTestId("chat-filling");
    expect(document.activeElement).toBe(panel);
    fireEvent.keyDown(panel, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("chat-filling")).toBeNull());
    expect(document.activeElement).toBe(meter);
    // Closed by its own Close press: back to the meter too.
    fireEvent.click(meter);
    const again = await screen.findByTestId("chat-filling");
    const close = screen.getByTestId("chat-filling-close");
    close.focus();
    expect(again.contains(document.activeElement)).toBe(true);
    fireEvent.click(close);
    await waitFor(() => expect(screen.queryByTestId("chat-filling")).toBeNull());
    expect(document.activeElement).toBe(meter);
  });

  it("opened from the chat's menu, focus goes back to the menu's button (its row is gone)", async () => {
    await twoQuestions();
    const trigger = screen.getByTestId("chat-more-trigger");
    fireEvent.click(trigger);
    const row = await screen.findByTestId("chat-more-filling");
    row.focus();
    fireEvent.click(row);
    const panel = await screen.findByTestId("chat-filling");
    expect(document.activeElement).toBe(panel);
    fireEvent.click(screen.getByTestId("chat-filling-close"));
    await waitFor(() => expect(screen.queryByTestId("chat-filling")).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it("a reply that ends while the list is open is read again, and the list stays on screen meanwhile", async () => {
    H.stream.context = HALF;
    H.api.responses[fillingPath("t1")] = fixture.filled;
    render(<ChatPage />);
    await sendHello();
    fireEvent.click(await screen.findByTestId("chat-fullness-meter"));
    await screen.findByTestId("chat-filling-context-parts");
    expect(H.api.gets.filter((p) => p.includes("/filling"))).toEqual([fillingPath("t1")]);
    expect(screen.getByTestId("chat-filling-context-percent").textContent).toContain("about 43%");

    // The next reply: held running, then ended. Its record is another one.
    H.stream.hold = true;
    const b = await box();
    fireEvent.change(b, { target: { value: "one more" } });
    fireEvent.keyDown(b, { key: "Enter" });
    await waitFor(() => expect(H.stream.release).not.toBeNull());
    expect(H.api.gets.filter((p) => p.includes("/filling"))).toEqual([fillingPath("t1")]);
    H.api.responses[fillingPath("t1")] = "HOLD";
    H.stream.release?.();
    await waitFor(() => expect(H.api.gets.filter((p) => p.includes("/filling")).length).toBe(2));
    // While the new answer is on its way the list is still there: no "Reading…" under the reader.
    expect(screen.getByTestId("chat-filling-context-percent").textContent).toContain("about 43%");
    expect(screen.queryByTestId("chat-filling-context-none")).toBeNull();
    H.api.held[fillingPath("t1")](fixture.over_full);
    await waitFor(() =>
      expect(screen.getByTestId("chat-filling-context-percent").textContent).toBe(
        "This chat holds more than this model can read at once.",
      ),
    );
  });
});
