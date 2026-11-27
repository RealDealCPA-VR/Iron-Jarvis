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
      puts: [] as { path: string; body: Record<string, unknown> }[],
      posts: [] as { path: string; body: unknown }[],
      runs: [] as unknown[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      /** What the NEXT reply's done frame carries as `checked` (already
       *  decoded by the hook, as the real one hands it over). */
      checked: null as unknown,
      doubleCheck: null as unknown,
      absent: false,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    if (path.startsWith("/checks/double-check/run/")) {
      const next = H.api.runs.shift();
      if (next === undefined) throw new H.FakeApiError("no more runs", 404);
      return next;
    }
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body?: unknown) => {
    H.api.posts.push({ path, body });
    return H.api.postResponses[path] ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
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
    run: async (_body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      if (H.stream.absent) throw new H.FakeStreamError("no stream", 404);
      await new Promise<void>((r) => setTimeout(r, 0));
      const reply = "I saved summary.xlsx.";
      onDelta(reply, reply);
      return {
        reply,
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
        tools_used: ["write_document"],
        ...(H.stream.checked ? { checked: H.stream.checked } : {}),
        ...(H.stream.doubleCheck ? { doubleCheck: H.stream.doubleCheck } : {}),
      };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
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
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import ChatPage from "@/app/chat/page";
import { CheckedWithDoubleCheck, DoubleCheckOffer, DoubleCheckRow } from "@/components/chat/DoubleCheck";
import { CHECK_CODES, decodeChecked } from "@/lib/checked";
import { NOTHING_TO_OFFER, RUN_ASKS_MAX, RUN_ASK_MS, YES, decodeDoubleCheck, waitingWords } from "@/lib/doubleCheck";
import FIXTURE from "./fixtures/intel-05-checked.json";

/*
 * The intelligence plan, 5b: a second model double-checks finished work.
 *
 * What carries weight here:
 *  - the reply's `double_check` is decoded by whitelist (an offer or a run);
 *  - "<model> is double-checking this…" is drawn ONLY while the daemon says
 *    the run is running, and the line becomes the daemon's new one when it
 *    lands; nothing is said after a restart or past the last ask;
 *  - the once-only card shows the daemon's own words only while the offer is
 *    unanswered, and Yes sends the exact choice it names;
 *  - the Settings row says how it stands; with one account it offers nothing;
 *  - both chat lanes save `doubleCheck` on the reply.
 */

const GOOD = FIXTURE.good;
const OFFER_WORDS = "Use Sonnet 4.5 to double-check finished work? About one small request per task on “Work”.";
const CHOICE = "acct-work|claude-cli|claude-sonnet-4-5";
const VIEW_UNSET = {
  state: "unset",
  words: "Not set",
  choice: null,
  offer: { choice: CHOICE, model: "Sonnet 4.5", account: "Work", words: OFFER_WORDS },
};
const TOKEN = "0123456789abcdef0123456789abcdef";
const LANDED = {
  ...GOOD,
  line: `${GOOD.line} Double-checked by Sonnet 4.5: it found nothing wrong.`,
  checks: [
    ...GOOD.checks,
    {
      check: "double_check",
      label: "The double-check",
      said: "it found nothing wrong",
      result: "passed",
      result_words: "Checked",
      who: "by Sonnet 4.5",
    },
  ],
};

const CHAT_GETS = () => ({
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
  "/checks/double-check": VIEW_UNSET as unknown,
});

beforeEach(() => {
  H.api.puts.length = 0;
  H.api.posts.length = 0;
  H.api.runs.length = 0;
  H.stream.checked = null;
  H.stream.doubleCheck = null;
  H.stream.absent = false;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = CHAT_GETS();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("the reply's double_check, decoded by whitelist", () => {
  it("keeps an offer or a run, and nothing else", () => {
    expect(decodeDoubleCheck({ offer: VIEW_UNSET.offer })).toEqual({ kind: "offer", ...VIEW_UNSET.offer });
    expect(decodeDoubleCheck({ token: TOKEN, who: "Sonnet 4.5" })).toEqual({
      kind: "running",
      token: TOKEN,
      who: "Sonnet 4.5",
    });
    for (const raw of [null, "x", {}, { token: "../../x", who: "a" }, { token: TOKEN }, { offer: { choice: CHOICE } }]) {
      expect(decodeDoubleCheck(raw)).toBeNull();
    }
    // What the page saves decodes back to the same thing (a reopened chat).
    const saved = decodeDoubleCheck({ token: TOKEN, who: "Sonnet 4.5" })!;
    expect(decodeDoubleCheck(JSON.parse(JSON.stringify(saved)))).toEqual(saved);
    const offer = decodeDoubleCheck({ offer: VIEW_UNSET.offer })!;
    expect(decodeDoubleCheck(JSON.parse(JSON.stringify(offer)))).toEqual(offer);
  });

  it("knows the daemon's new check code", () => {
    expect(CHECK_CODES).toContain("double_check");
  });
});

describe("the line while a second model double-checks", () => {
  it("says it is double-checking only while the daemon says so, then shows the daemon's new line", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    H.api.runs.push({ running: true, checked: null }, { running: false, checked: LANDED });
    render(<CheckedWithDoubleCheck mark={decodeChecked(GOOD)} doubleCheck={{ token: TOKEN, who: "Sonnet 4.5" }} />);
    expect(screen.getByTestId("checked-line-words").textContent).toBe(GOOD.line);
    expect(screen.queryByTestId("double-check-waiting")).toBeNull();
    expect((await screen.findByTestId("double-check-waiting")).textContent).toBe(waitingWords("Sonnet 4.5"));
    await vi.advanceTimersByTimeAsync(RUN_ASK_MS);
    await waitFor(() => expect(screen.getByTestId("checked-line-words").textContent).toBe(LANDED.line));
    expect(screen.queryByTestId("double-check-waiting")).toBeNull();
  });

  it("says nothing when the daemon does not know the run (a restart), and keeps the line", async () => {
    H.api.runs.push({ running: false, checked: null });
    render(<CheckedWithDoubleCheck mark={decodeChecked(GOOD)} doubleCheck={{ token: TOKEN, who: "Sonnet 4.5" }} />);
    await waitFor(() => expect(H.api.runs).toHaveLength(0));
    expect(screen.queryByTestId("double-check-waiting")).toBeNull();
    expect(screen.getByTestId("checked-line-words").textContent).toBe(GOOD.line);
  });

  it("stops saying it after asking at most RUN_ASKS_MAX times", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    for (let i = 0; i < RUN_ASKS_MAX + 5; i++) H.api.runs.push({ running: true, checked: null });
    render(<CheckedWithDoubleCheck mark={decodeChecked(GOOD)} doubleCheck={{ token: TOKEN, who: "Sonnet 4.5" }} />);
    await screen.findByTestId("double-check-waiting");
    await vi.advanceTimersByTimeAsync(RUN_ASK_MS * (RUN_ASKS_MAX + 2));
    await waitFor(() => expect(screen.queryByTestId("double-check-waiting")).toBeNull());
    expect(H.api.runs).toHaveLength(5);
  });
});

describe("the once-only card", () => {
  it("shows the daemon's words while unanswered, and Yes sends the choice it names", async () => {
    H.api.postResponses["/checks/double-check"] = { note: "Sonnet 4.5 on “Work” double-checks finished work." };
    render(<DoubleCheckOffer />);
    expect((await screen.findByTestId("double-check-offer-words")).textContent).toBe(OFFER_WORDS);
    fireEvent.click(screen.getByText(YES));
    await screen.findByTestId("double-check-offer-said");
    expect(H.api.posts).toEqual([{ path: "/checks/double-check", body: { answer: CHOICE } }]);
  });

  it("No sends no", async () => {
    H.api.postResponses["/checks/double-check"] = { note: "Iron Jarvis's own checks only." };
    render(<DoubleCheckOffer />);
    fireEvent.click(await screen.findByTestId("double-check-no"));
    await screen.findByTestId("double-check-offer-said");
    expect(H.api.posts).toEqual([{ path: "/checks/double-check", body: { answer: "no" } }]);
  });

  it("draws nothing once answered anywhere, or with nothing to offer", async () => {
    H.api.getResponses["/checks/double-check"] = { ...VIEW_UNSET, state: "off" };
    const { unmount } = render(<DoubleCheckOffer />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("double-check-offer")).toBeNull();
    unmount();
    H.api.getResponses["/checks/double-check"] = { ...VIEW_UNSET, offer: null };
    render(<DoubleCheckOffer />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("double-check-offer")).toBeNull();
  });
});

describe("Settings › Models › Second model double-check", () => {
  it("with one account it offers nothing and says why", async () => {
    H.api.getResponses["/checks/double-check"] = { state: "unset", words: "Not set", choice: null, offer: null };
    render(<DoubleCheckRow />);
    expect((await screen.findByTestId("double-check-row-words")).textContent).toBe(NOTHING_TO_OFFER);
    expect(screen.queryByTestId("double-check-yes")).toBeNull();
  });

  it("on: names the model and account and turns off", async () => {
    H.api.getResponses["/checks/double-check"] = {
      state: "on",
      words: "Sonnet 4.5 on “Work”",
      choice: { id: CHOICE, model: "Sonnet 4.5", account: "Work" },
      offer: VIEW_UNSET.offer,
    };
    H.api.postResponses["/checks/double-check"] = { note: "x", view: { ...VIEW_UNSET, state: "off" } };
    render(<DoubleCheckRow />);
    expect((await screen.findByTestId("double-check-row-words")).textContent).toBe("On: Sonnet 4.5 on “Work”.");
    fireEvent.click(screen.getByTestId("double-check-off"));
    await waitFor(() => expect(H.api.posts).toEqual([{ path: "/checks/double-check", body: { answer: "no" } }]));
  });
});

async function send(text: string, reply: string) {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await screen.findByText(reply);
}

const lastSaved = () =>
  H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)!.body.messages as Record<string, unknown>[];

describe("the chat page (both lanes)", () => {
  it("stream lane: the card is under the reply, and the offer is saved on it", async () => {
    H.stream.checked = decodeChecked(GOOD);
    H.stream.doubleCheck = decodeDoubleCheck({ offer: VIEW_UNSET.offer });
    render(<ChatPage />);
    await send("make me a summary sheet", "I saved summary.xlsx.");
    expect((await screen.findByTestId("double-check-offer-words")).textContent).toBe(OFFER_WORDS);
    await waitFor(() => expect(lastSaved().at(-1)!.doubleCheck).toEqual({ kind: "offer", ...VIEW_UNSET.offer }));
  });

  it("POST lane: the running double-check is saved on the reply and asked about", async () => {
    H.stream.absent = true;
    H.api.runs.push({ running: false, checked: LANDED });
    H.api.postResponses["/chat"] = {
      reply: "I saved summary.xlsx.",
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
      remembered: [],
      checked: GOOD,
      double_check: { token: TOKEN, who: "Sonnet 4.5" },
    };
    render(<ChatPage />);
    await send("make me a summary sheet", "I saved summary.xlsx.");
    await waitFor(() => expect(screen.getByTestId("checked-line-words").textContent).toBe(LANDED.line));
    await waitFor(() =>
      expect(lastSaved().at(-1)!.doubleCheck).toEqual({ kind: "running", token: TOKEN, who: "Sonnet 4.5" }),
    );
  });

  it("a reply without the key draws no card and saves no key", async () => {
    H.stream.checked = decodeChecked(GOOD);
    render(<ChatPage />);
    await send("make me a summary sheet", "I saved summary.xlsx.");
    await waitFor(() => expect(lastSaved().at(-1)!.content).toBe("I saved summary.xlsx."));
    expect(screen.queryByTestId("double-check-offer")).toBeNull();
    expect("doubleCheck" in lastSaved().at(-1)!).toBe(false);
  });
});
