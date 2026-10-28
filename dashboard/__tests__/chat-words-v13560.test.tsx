/**
 * v1.356.0 (G75) — the chat's controls in plain words.
 *
 * ACCEPTANCE: a long chat offers "This chat is getting long. Summarize the
 * start…" with a Summarize button, and the tools menu lists tools by what
 * they do. Also: the compaction card says "summarized automatically because
 * the chat got long", a million-token model reads "long docs", the folder
 * tooltip says "Jarvis works on files in that folder", the reasoning chip's
 * "auto" reads "Model decides", the Local chip shows its word on phones, the
 * helper-agent rows wear the Bot (not Reflexes' Zap), and the first-run
 * model-server field reads "Your model server's address".
 *
 * Harness: the composer-card-v1326 mocks (ChatPage rendered for real; an
 * unmocked GET answers {}), plus a stream whose result can carry a context
 * report. Words only changed; the controls behave exactly as before.
 */

import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      responses: {} as Record<string, unknown>,
    },
    stream: { context: null as Record<string, unknown> | null },
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
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    if (path === "/chat/compact") return { covers: 4, stripped: 0, summary: "The start.", trigger: "manual" };
    return {};
  },
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
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: () =>
      Promise.resolve({
        reply: "Noted.",
        tools_used: [],
        ...(H.stream.context ? { context: H.stream.context } : {}),
      }),
    abort: () => {},
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
import { CompactionCard } from "@/components/chat/CompactionCard";
import { ModelRowChips } from "@/components/ModelRowBits";
import { AgentRunSettled } from "@/components/chat/AgentRunRow";
import {
  COMPACTION_OFFER_WORDS,
  armedToolWords,
  turnOffToolLabel,
  workingFolderTitle,
} from "@/lib/chatControlWords";
import { reasoningChipLabel, reasoningTitle } from "@/lib/reasoningChip";

/* ------------------------------------------------------------------ data */

const AT = "2026-10-01T10:00:00Z";
/** A chat with two tools turned on by hand: one the app can describe, one
 *  (an agent-made custom tool) it cannot vouch for. */
const ARMED = {
  id: "ta",
  title: "Armed chat",
  updated_at: AT,
  messages: [
    { role: "user", content: "read the ledger" },
    { role: "assistant", content: "The ledger is read." },
  ],
  // v1.388.0: the third tool is one the app cannot vouch for (not a
  // registered built-in — excel_query now reads "Query a workbook").
  setup: { tools: ["read_file", "mcp__gmail__search_messages", "quote_lookup"] },
};
/** A chat whose agent was handed work before v1.349.0's stamp (the line). */
const HANDED = {
  id: "th",
  title: "Handed chat",
  updated_at: AT,
  messages: [
    { role: "user", content: "summarise the ledger" },
    { role: "assistant", content: "", escalated: "needs the agent", escalatedTo: "the researcher" },
  ],
};
const LONG = {
  window: 200_000,
  used: 148_000,
  headroom: 52_000,
  dropped: 0,
  tools_trimmed: 0,
  clipped: false,
  recap: false,
  suggest_larger: false,
  percent: 74,
  level: "suggest",
  suggest_at: 70,
  auto_at: 92,
};

/* --------------------------------------------------------------- helpers */

const box = async () => (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
const toolbar = () => screen.getByTestId("composer-toolbar");

async function sendHello() {
  const b = await box();
  fireEvent.change(b, { target: { value: "hello" } });
  fireEvent.keyDown(b, { key: "Enter" });
  await screen.findAllByText("Noted.");
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.stream.context = null;
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/ta": ARMED,
    "/chat/threads/th": HANDED,
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

/* ===================================================== the long-chat offer */

describe("a long chat offers to summarize its start, in plain words", () => {
  it("says 'This chat is getting long. Summarize the start…' with a Summarize button", async () => {
    H.stream.context = LONG;
    render(<ChatPage />);
    await sendHello();
    const offer = await screen.findByTestId("compaction-offer");
    expect(offer.textContent).toContain("This chat is getting long. Summarize the start");
    expect(screen.getByTestId("compaction-offer-words").textContent).toBe(COMPACTION_OFFER_WORDS);
    expect(COMPACTION_OFFER_WORDS).toMatch(/^This chat is getting long\. Summarize the start\b.*\? The full transcript is kept\.$/);
    // No engine words on the offer.
    expect(offer.textContent).not.toMatch(/context window|Compact now|token/i);
    expect(within(offer).queryByRole("button", { name: "Compact now" })).toBeNull();
    // The press still does what Compact now did.
    fireEvent.click(within(offer).getByRole("button", { name: "Summarize" }));
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/chat/compact")).toBe(true));
  });

  it("'Not now' says what happens if the user does nothing, without a percentage", async () => {
    H.stream.context = LONG;
    render(<ChatPage />);
    await sendHello();
    const offer = await screen.findByTestId("compaction-offer");
    const notNow = within(offer).getByRole("button", { name: "Not now" });
    expect(notNow.getAttribute("title")).toMatch(/summarizes the start by itself/);
    expect(notNow.getAttribute("title")).not.toMatch(/%/);
  });

  it("CONTROL: a short chat shows no offer", async () => {
    H.stream.context = { ...LONG, percent: 30, level: "ok" };
    render(<ChatPage />);
    await sendHello();
    expect(screen.queryByTestId("compaction-offer")).toBeNull();
  });
});

/* The composer's fill gauge (ContextMeter) shows from half full; its hover is
 * the only words it has. With no offer on screen (level "ok") the gauge is the
 * one element carrying them. */
describe("the fill gauge's hover says how full the chat is in plain words", () => {
  const gaugeTitle = async (fragment: RegExp) =>
    waitFor(() => {
      const el = Array.from(document.querySelectorAll<HTMLElement>("[title]")).find((n) =>
        fragment.test(n.getAttribute("title") ?? ""),
      );
      expect(el).toBeTruthy();
      return (el as HTMLElement).getAttribute("title") ?? "";
    });

  it("a filling chat: 'This chat fills about N% …', never 'context window' or 'token'", async () => {
    H.stream.context = { ...LONG, percent: 60, level: "ok" };
    render(<ChatPage />);
    await sendHello();
    const title = await gaugeTitle(/of what this model can read at once/);
    expect(title).toBe("This chat fills about 60% of what this model can read at once.");
    expect(screen.queryByTestId("compaction-offer")).toBeNull();
    expect(document.body.innerHTML).not.toMatch(/context window|-token window/);
  });

  it("a trimmed chat says what was summarized, in the same words", async () => {
    H.stream.context = { ...LONG, percent: 60, level: "ok", dropped: 2 };
    render(<ChatPage />);
    await sendHello();
    const title = await gaugeTitle(/earlier messages? (was|were) summarized/);
    expect(title).toBe(
      "2 earlier messages were summarized so this chat fits what this model can read at once. A model that reads longer chats would keep them.",
    );
    expect(document.body.innerHTML).not.toMatch(/context window|-token window/);
  });
});

describe("the compaction card says who started the summary in plain words", () => {
  it("auto: 'summarized automatically because the chat got long'; manual: 'summarized at your request'", () => {
    const { unmount } = render(
      <CompactionCard info={{ found: true, summary: "x", covers: 4, trigger: "auto" }} onClose={() => {}} />,
    );
    expect(screen.getByText(/summarized automatically because the chat got long/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/context ceiling|compacted/);
    unmount();
    render(<CompactionCard info={{ found: true, summary: "x", covers: 4, trigger: "manual" }} onClose={() => {}} />);
    expect(screen.getByText(/summarized at your request/)).toBeInTheDocument();
  });
});

/* =========================================================== tools menu */

describe("the tools menu lists tools by what they do", () => {
  async function openMenu() {
    window.history.replaceState({}, "", "/chat?thread=ta");
    render(<ChatPage />);
    await screen.findByText("The ledger is read.");
    const chip = within(toolbar()).getByRole("button", { name: /^Tools: / });
    await waitFor(() => expect(chip.textContent).toContain("Auto tools + 3"));
    fireEvent.click(chip);
    return screen.findByTestId("composer-tools-menu");
  }

  it("each turned-on tool reads its words; the exact id is its title; an unknown id shows as itself", async () => {
    const menu = await openMenu();
    const rows = within(menu).getAllByTestId("armed-tool-row");
    expect(rows.map((r) => r.textContent)).toEqual(["Read files", "gmail: search messages", "quote_lookup"]);
    expect(rows.map((r) => r.getAttribute("title"))).toEqual([
      "read_file",
      "mcp__gmail__search_messages",
      "quote_lookup",
    ]);
    // A described tool is plain text; only the id the app cannot vouch for
    // keeps the monospace look of an id.
    expect(within(rows[0]).getByText("Read files").className).not.toContain("font-mono");
    expect(within(rows[2]).getByText("quote_lookup").className).toContain("font-mono");
    expect(within(menu).queryByText("read_file")).toBeNull();
  });

  it("the buttons say Turn off, never Disarm, and still turn the tool off", async () => {
    const menu = await openMenu();
    expect(menu.innerHTML).not.toMatch(/[Dd]isarm|[^a-z]arm(ed)? /);
    const off = within(menu).getByRole("button", { name: "Turn off Read files (read_file)" });
    expect(off.getAttribute("title")).toBe("Turn off for this chat");
    fireEvent.click(off);
    await waitFor(() => expect(within(menu).getAllByTestId("armed-tool-row")).toHaveLength(2));
    const web = within(menu).getByRole("switch", { name: /Web & research/ });
    expect(web.getAttribute("title")).toBe("Turn on web research for this chat");
  });
});

describe("the words for a turned-on tool (lib/chatControlWords)", () => {
  it.each([
    ["write_file", "Save a file"],
    ["read_file", "Read files"],
    ["read_document", "Read files"],
    ["grep", "Search in files"],
    ["web_search", "Search the web"],
    ["shell", "Run a command"],
    ["browser_click", "Act in a browser tab"],
    ["mcp__github__list_issues", "github: list issues"],
    // v1.388.0 (readiness 12A): a built-in neither the card table nor a kind
    // knows reads in the generated words of every registered built-in.
    ["excel_query", "Query a workbook"],
    ["quote_lookup", null],
    ["", null],
  ])("%j reads %j", (id, words) => {
    expect(armedToolWords(id)).toBe(words);
  });

  it("the Turn off name keeps the exact id beside the words (two tools can share words)", () => {
    expect(turnOffToolLabel("read_document")).toBe("Turn off Read files (read_document)");
    expect(turnOffToolLabel("quote_lookup")).toBe("Turn off quote_lookup");
  });
});

/* ================================================================ folder */

describe("the working folder says what it is for", () => {
  it("'+' menu: Pick a folder. Jarvis works on files in that folder.", async () => {
    render(<ChatPage />);
    await box();
    fireEvent.click(within(toolbar()).getByRole("button", { name: "Open the chat menu" }));
    const pick = await screen.findByRole("button", { name: "Choose a working folder" });
    expect(pick.getAttribute("title")).toBe("Pick a folder. Jarvis works on files in that folder.");
    expect(pick.getAttribute("title")).not.toMatch(/tools make|land in/);
  });

  it("a chosen folder names itself", () => {
    expect(workingFolderTitle("C:/Work/Books")).toBe("Jarvis works on files in that folder: C:/Work/Books");
    expect(workingFolderTitle("")).toBe("Pick a folder. Jarvis works on files in that folder.");
  });
});

/* ========================================================== the Local chip */

describe("the Local chip shows its word on a phone, on or off", () => {
  it("off: the phone word 'Local' is there (sm:hidden), the desktop words stay", async () => {
    render(<ChatPage />);
    const c = await screen.findByTestId("local-only-chip");
    const words = Array.from(c.querySelectorAll("span"));
    expect(words.map((w) => w.textContent)).toEqual(["Local", "Local only"]);
    expect(words[0].className.split(/\s+/)).toContain("sm:hidden");
    expect(words[0].className.split(/\s+/)).not.toContain("hidden");
    expect(c.getAttribute("aria-pressed")).toBe("false");
  });
});

/* ============================================================ model rows */

describe("a million-token model reads 'long docs', never '1M'", () => {
  it("the chip says long docs with a plain hover; a smaller model has no chip", () => {
    const { rerender, container } = render(<ModelRowChips m={{ model: "opus", context_window: 1_000_000 }} />);
    const chip = screen.getByTestId("model-1m");
    expect(chip.textContent).toBe("long docs");
    expect(chip.getAttribute("title")).toBe("Reads very long documents and chats at once");
    expect(chip.className).toContain("text-[10px]");
    rerender(<ModelRowChips m={{ model: "haiku", context_window: 200_000 }} />);
    expect(container.textContent).toBe("");
  });
});

/* ======================================================= reasoning chip */

describe("the reasoning chip: a model that decides reads 'Model decides'", () => {
  it("never a third control called Auto", () => {
    expect(reasoningChipLabel("", "auto")).toBe("Model decides");
    // v1.401.2: the list's "Default (…)" row is gone (moved on purpose).
    expect(reasoningTitle("auto")).toContain("decides for itself");
    // CONTROL: the other words are unchanged.
    expect(reasoningChipLabel("", "medium")).toBe("Medium");
    expect(reasoningChipLabel("high", "auto")).toBe("High");
  });
});

/* ===================================================== helper-agent rows */

describe("a helper agent wears the Bot, not Reflexes' Zap", () => {
  it("the settled agent row", () => {
    const { container } = render(
      <AgentRunSettled
        name="The researcher"
        run={{ session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 }}
        why="needs the agent"
      />,
    );
    expect(container.querySelector("svg.lucide-bot")).not.toBeNull();
    expect(container.querySelector("svg.lucide-zap")).toBeNull();
  });

  it("the hand-off line on the chat page", async () => {
    window.history.replaceState({}, "", "/chat?thread=th");
    render(<ChatPage />);
    const said = await screen.findByText(/researcher/);
    const line = said.closest("div") as HTMLElement;
    expect(line.querySelector("svg.lucide-bot")).not.toBeNull();
    expect(line.querySelector("svg.lucide-zap")).toBeNull();
  });
});
