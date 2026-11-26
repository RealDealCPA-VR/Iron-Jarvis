/**
 * The intelligence plan, release 5: every finished job says what was checked.
 *
 * Plain words: under a chat reply (or a task's result) that made or changed
 * a file there is ONE quiet line, in the daemon's own words from its record
 * of checks: "Checked by Iron Jarvis: the spreadsheet opens, no error cells,
 * 3 totals add up." Pressing it lists the checks, each with its result and
 * who checked. A failed check reads in the warn tone. A reply that made no
 * file shows no line.
 *
 * What carries weight here, each from the daemon's REAL payload (the fixture
 * is written by tests/test_intel_05_record.py from `checks/record.payload`):
 *  - the decoder keeps only a valid shape, and what the page saves decodes
 *    back to the same thing (a reopened chat);
 *  - the line is drawn word for word; the list is closed until pressed;
 *  - the chat page: the stream lane's and the POST lane's `checked` reach
 *    the reply and are SAVED on it; a reopened chat shows the line; a reply
 *    without the key draws none;
 *  - the task result card draws the same line from the result's `checked`.
 */

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
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      /** What the NEXT reply's done frame carries as `checked` (already
       *  decoded by the hook, as the real one hands it over). */
      checked: null as unknown,
      absent: false,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string) => H.api.postResponses[path] ?? {},
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
import { CheckedLine } from "@/components/chat/CheckedLine";
import { RunResultCard, type RunResult } from "@/components/chat/RunResultCard";
import {
  CHECKED_CLOSE,
  CHECKED_LIST_MAX,
  CHECKED_OPEN,
  checkedForSave,
  decodeChecked,
  isWarnResult,
  seenText,
} from "@/lib/checked";
import FIXTURE from "./fixtures/intel-05-checked.json";

const GOOD = FIXTURE.good;
const BAD = FIXTURE.bad;
const MANY = FIXTURE.many;
const NOT_CHECKED = FIXTURE.not_checked;
/** A total Iron Jarvis added up itself and found different: "worth a look". */
const LOOK = FIXTURE.look;
/** A problem the file holds AND something worth a look, with what was not checked. */
const BOTH = FIXTURE.both;
/** The same spoiled payload the daemon's own cleaning is tested on, and what it must become. */
const UNSEEN = FIXTURE.unseen;

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
});

beforeEach(() => {
  H.api.puts.length = 0;
  H.stream.checked = null;
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
});

// ------------------------------------------------------------- the decoder

describe("the daemon's `checked`, decoded by whitelist", () => {
  it("keeps the line, the tone and each check, in the daemon's own words", () => {
    const mark = decodeChecked(BAD)!;
    expect(mark.line).toBe(BAD.line);
    expect(mark.tone).toBe("warn");
    expect(mark.checks.map((c) => [c.label, c.said, c.result, c.resultWords, c.who])).toEqual(
      BAD.checks.map((c) => [c.label, c.said, c.result, c.result_words, c.who]),
    );
    expect(decodeChecked(GOOD)!.tone).toBe("quiet");
    expect(decodeChecked(MANY)!.more).toBe(MANY.more);
    expect(decodeChecked(MANY)!.moreWords).toBe(MANY.more_words);
    expect(MANY.more).toBeGreaterThan(0);
  });

  it("draws nothing for a reply that has none, or for anything malformed", () => {
    for (const raw of [undefined, null, "", 3, [], {}, { line: "" }, { line: "   " }, { checks: GOOD.checks }]) {
      expect(decodeChecked(raw)).toBeNull();
    }
  });

  it("drops a check with no label or an unknown result, never trusts a tone it does not know, caps the list", () => {
    const mark = decodeChecked({
      line: "Checked by Iron Jarvis: the file opens.",
      tone: "celebrate",
      more: -4,
      more_words: "and -4 more checks",
      checks: [
        { label: "a.txt", said: "opens", result: "passed", result_words: "Checked", who: "by rule" },
        { label: "", said: "x", result: "passed" },
        { label: "b.txt", said: "x", result: "wonderful" },
        "junk",
        null,
      ],
    })!;
    expect(mark.tone).toBe("quiet");
    expect(mark.more).toBe(0);
    expect(mark.moreWords).toBe("");
    expect(mark.checks.map((c) => c.label)).toEqual(["a.txt"]);
    const long = decodeChecked({
      line: "x",
      checks: Array.from({ length: 80 }, (_, i) => ({ label: `f${i}`, result: "passed" })),
    })!;
    expect(long.checks).toHaveLength(CHECKED_LIST_MAX);
  });

  it("keeps worth a look as its own result, never folded into a problem or a pass", () => {
    const mark = decodeChecked(LOOK)!;
    expect(mark.tone).toBe("warn");
    expect(mark.checks[0]).toEqual({
      label: "summary.xlsx",
      said: "B4 on Summary says 650 and the cells above it add up to 600",
      result: "look",
      resultWords: "Worth a look",
      who: "by rule",
    });
    expect(mark.checks.filter((c) => c.result === "failed")).toHaveLength(0);
    expect(decodeChecked(BOTH)!.checks.map((c) => c.result)).toEqual(["failed", "look", "not_checked", "passed"]);
    expect([isWarnResult("failed"), isWarnResult("look"), isWarnResult("not_checked"), isWarnResult("passed")]).toEqual(
      [true, true, false, false],
    );
  });

  it("strips the characters nobody sees from every text, as the daemon does (the same inputs)", () => {
    const mark = decodeChecked(UNSEEN.raw)!;
    const clean = UNSEEN.clean;
    expect(mark.line).toBe(clean.line);
    expect(mark.moreWords).toBe(clean.more_words);
    expect(mark.checks.map((c) => [c.label, c.said, c.result, c.resultWords, c.who])).toEqual(
      clean.checks.map((c) => [c.label, c.said, c.result, c.result_words, c.who]),
    );
    // The fixture's raw side really carries them (NUL, ESC, the right-to-left override, an isolate).
    const raw = UNSEEN.raw.line as string;
    for (const code of [0x00, 0x1b, 0x202e, 0x2066]) {
      expect(raw.includes(String.fromCharCode(code))).toBe(true);
      expect(mark.line.includes(String.fromCharCode(code))).toBe(false);
    }
    const every = [mark.line, mark.moreWords, ...mark.checks.flatMap((c) => [c.label, c.said, c.resultWords, c.who])];
    for (const text of every) {
      expect(seenText(text)).toBe(text);
      expect(/[\u0000-\u001f]/.test(text)).toBe(false);
    }
    expect(seenText("a" + String.fromCharCode(0x202e) + "b\tc\r\nd")).toBe("ab c d");
  });

  it("what the page saves on the reply decodes back to the same mark (a reopened chat)", () => {
    for (const raw of [GOOD, BAD, LOOK, BOTH, MANY, NOT_CHECKED]) {
      const mark = decodeChecked(raw)!;
      const saved = checkedForSave(mark);
      expect(saved).toEqual(raw);
      expect(decodeChecked(JSON.parse(JSON.stringify(saved)))).toEqual(mark);
    }
  });
});

// ---------------------------------------------------------------- the line

describe("the line", () => {
  it("says the daemon's sentence word for word, quietly, with the list closed until pressed", () => {
    render(<CheckedLine mark={decodeChecked(GOOD)} />);
    expect(screen.getByTestId("checked-line-words").textContent).toBe(
      "Checked by Iron Jarvis: the spreadsheet opens, no error cells, 3 totals add up.",
    );
    expect(screen.getByTestId("checked-line").getAttribute("data-tone")).toBe("quiet");
    expect(screen.getByTestId("checked-line").className).not.toContain("text-tone-warn");
    expect(screen.queryByTestId("checked-list")).toBeNull();
    const press = screen.getByTestId("checked-line-press");
    expect(press.getAttribute("aria-expanded")).toBe("false");
    expect(press.getAttribute("title")).toBe(CHECKED_OPEN);
  });

  it("pressing it lists each check, its result and who checked; pressing again closes it", () => {
    render(<CheckedLine mark={decodeChecked(GOOD)} />);
    const press = screen.getByTestId("checked-line-press");
    fireEvent.click(press);
    expect(press.getAttribute("aria-expanded")).toBe("true");
    expect(press.getAttribute("title")).toBe(CHECKED_CLOSE);
    const list = screen.getByTestId("checked-list");
    expect(press.getAttribute("aria-controls")).toBe(list.id);
    const rows = within(list).getAllByTestId("checked-item");
    // The plan's do-nothing check: three checks of the file (and the reply's own).
    expect(rows.map((r) => r.textContent)).toEqual([
      "summary.xlsx: opens (Checked, by rule)",
      "summary.xlsx: no error cells (Checked, by rule)",
      "summary.xlsx: 3 totals add up (Checked, by rule)",
      "The reply: what it says matches what it did (Checked, by rule)",
    ]);
    expect(rows.filter((r) => r.textContent!.startsWith("summary.xlsx"))).toHaveLength(3);
    fireEvent.click(press);
    expect(screen.queryByTestId("checked-list")).toBeNull();
    expect(press.getAttribute("aria-expanded")).toBe("false");
  });

  it("a failed check reads in the warn tone, first in the list, and a model that checked is named", () => {
    render(<CheckedLine mark={decodeChecked(BAD)} />);
    const line = screen.getByTestId("checked-line");
    expect(line.getAttribute("data-tone")).toBe("warn");
    expect(line.className).toContain("text-tone-warn");
    expect(screen.getByTestId("checked-line-words").textContent).toContain("C7 on Summary holds #DIV/0!");
    fireEvent.click(screen.getByTestId("checked-line-press"));
    const rows = screen.getAllByTestId("checked-item");
    expect(rows[0].getAttribute("data-result")).toBe("failed");
    expect(rows[0].textContent).toBe("summary.xlsx: C7 on Summary holds #DIV/0! (Problem, by rule)");
    expect(rows[1].getAttribute("data-result")).toBe("not_checked");
    expect(rows.at(-1)!.textContent).toBe("Step 2: it passed its check (Checked, by Claude Sonnet 4.5)");
  });

  it("worth a look reads in the warning colour under its own word, after a problem and before not checked", () => {
    const { unmount } = render(<CheckedLine mark={decodeChecked(LOOK)} />);
    const line = screen.getByTestId("checked-line");
    expect(line.getAttribute("data-tone")).toBe("warn");
    expect(line.className).toContain("text-tone-warn");
    expect(screen.getByTestId("checked-line-words").textContent).toBe(
      "Checked by Iron Jarvis: the spreadsheet opens, no error cells. " +
        "Worth a look: B4 on Summary says 650 and the cells above it add up to 600.",
    );
    expect(screen.getByTestId("checked-line-words").textContent).not.toContain("Problem");
    fireEvent.click(screen.getByTestId("checked-line-press"));
    let rows = screen.getAllByTestId("checked-item");
    expect(rows[0].getAttribute("data-result")).toBe("look");
    expect(rows[0].textContent).toBe(
      "summary.xlsx: B4 on Summary says 650 and the cells above it add up to 600 (Worth a look, by rule)",
    );
    expect(rows[0].querySelector("span span")!.className).toContain("text-tone-warn");
    expect(rows[0].querySelector("svg")!.getAttribute("class")).toContain("text-tone-warn");
    expect(rows.map((r) => r.textContent).join(" ")).not.toContain("Problem");
    // What passed beside it stays quiet.
    expect(rows[1].getAttribute("data-result")).toBe("passed");
    expect(rows[1].querySelector("span span")!.className).not.toContain("text-tone-warn");
    unmount();
    render(<CheckedLine mark={decodeChecked(BOTH)} />);
    fireEvent.click(screen.getByTestId("checked-line-press"));
    rows = screen.getAllByTestId("checked-item");
    expect(rows.map((r) => r.getAttribute("data-result"))).toEqual(["failed", "look", "not_checked", "passed"]);
    expect(rows[0].textContent).toBe("summary.xlsx: C7 on Summary holds #DIV/0! (Problem, by rule)");
    expect(rows[1].textContent).toBe("summary.xlsx: D3 on Summary works out to #DIV/0! (Worth a look, by rule)");
    expect(rows[2].querySelector("span span")!.className).not.toContain("text-tone-warn");
  });

  it("what could not be checked is said quietly, and a long list says how many more", () => {
    const { unmount } = render(<CheckedLine mark={decodeChecked(NOT_CHECKED)} />);
    expect(screen.getByTestId("checked-line-words").textContent).toBe(
      "Not checked: the PDF (the check ran out of time).",
    );
    expect(screen.getByTestId("checked-line").getAttribute("data-tone")).toBe("quiet");
    unmount();
    render(<CheckedLine mark={decodeChecked(MANY)} />);
    fireEvent.click(screen.getByTestId("checked-line-press"));
    expect(screen.getAllByTestId("checked-item")).toHaveLength(MANY.checks.length);
    expect(screen.getByTestId("checked-more").textContent).toBe(MANY.more_words);
  });

  it("no mark, no line; no engine word on screen", () => {
    const { container } = render(<CheckedLine mark={null} />);
    expect(container.firstChild).toBeNull();
    cleanup();
    render(<CheckedLine mark={decodeChecked(BAD)} />);
    fireEvent.click(screen.getByTestId("checked-line-press"));
    const said = screen.getByTestId("checked-line").textContent!.toLowerCase();
    for (const word of ["verified", "lint", "validator", "judge", "unverified", "validated"]) {
      expect(said).not.toContain(word);
    }
  });
});

// ---------------------------------------------------------------- the page

async function send(text: string, reply: string) {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await screen.findByText(reply);
  return box;
}

const threadSaves = () => H.api.puts.filter((p) => p.path.startsWith("/chat/threads/"));
const lastSaved = () => threadSaves().at(-1)!.body.messages as { role: string; content: string; checked?: unknown }[];

describe("the chat page (both lanes)", () => {
  it("stream lane: the line is under the reply that made a file, and is saved on it", async () => {
    H.stream.checked = decodeChecked(BAD);
    render(<ChatPage />);
    await send("make me a summary sheet", "I saved summary.xlsx.");
    const words = await screen.findByTestId("checked-line-words");
    expect(words.textContent).toBe(BAD.line);
    expect(screen.getByTestId("checked-line").getAttribute("data-tone")).toBe("warn");
    await waitFor(() => expect(lastSaved().at(-1)!.checked).toEqual(BAD));
  });

  it("POST lane (no /chat/stream): the same line, from the response's checked", async () => {
    H.stream.absent = true;
    H.api.postResponses["/chat"] = {
      reply: "I saved summary.xlsx.",
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
      remembered: [],
      checked: GOOD,
    };
    render(<ChatPage />);
    await send("make me a summary sheet", "I saved summary.xlsx.");
    expect((await screen.findByTestId("checked-line-words")).textContent).toBe(GOOD.line);
    await waitFor(() => expect(lastSaved().at(-1)!.checked).toEqual(GOOD));
  });

  it("a reply that made no file shows no line and saves no key", async () => {
    render(<ChatPage />);
    await send("capital of France?", "I saved summary.xlsx.");
    await waitFor(() => expect(lastSaved().at(-1)!.content).toBe("I saved summary.xlsx."));
    expect(screen.queryByTestId("checked-line")).toBeNull();
    expect("checked" in lastSaved().at(-1)!).toBe(false);
  });

  it("the real stream reader carries the done frame's checked onto the reply, and only then", async () => {
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>("@/lib/useChatStream");
    const { renderHook, act } = await import("@testing-library/react");
    const settle = async (frame: Record<string, unknown>) => {
      const body = `event: done
data: ${JSON.stringify(frame)}

`;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(body));
          controller.close();
        },
      });
      vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
      try {
        const { result } = renderHook(() => useChatStream());
        let settled: { checked?: unknown } | null = null;
        await act(async () => {
          settled = (await result.current.run({ messages: [] })) as typeof settled;
        });
        return settled as unknown as { checked?: unknown };
      } finally {
        vi.unstubAllGlobals();
      }
    };
    const withFile = await settle({ reply: "I saved summary.xlsx.", checked: BAD });
    expect(withFile.checked).toEqual(decodeChecked(BAD));
    const plain = await settle({ reply: "Paris." });
    expect("checked" in plain).toBe(false);
    const junk = await settle({ reply: "x", checked: { line: "", checks: "no" } });
    expect("checked" in junk).toBe(false);
  });

  it("a reopened chat still shows the line of a saved reply, and pressing it lists the checks", async () => {
    H.api.getResponses = {
      ...CHAT_GETS(),
      "/chat/threads/t9": {
        id: "t9",
        title: "Summary",
        messages: [
          { role: "user", content: "make me a summary sheet", at: "2026-11-26T08:00:00" },
          { role: "assistant", content: "Saved it as summary.xlsx.", at: "2026-11-26T08:00:05", checked: GOOD },
          { role: "user", content: "thanks", at: "2026-11-26T08:01:00" },
          { role: "assistant", content: "You are welcome.", at: "2026-11-26T08:01:02" },
        ],
      },
    };
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    await screen.findByText("Saved it as summary.xlsx.");
    // One line: the reply that made the file. The later reply has none.
    const lines = await screen.findAllByTestId("checked-line-words");
    expect(lines.map((l) => l.textContent)).toEqual([GOOD.line]);
    fireEvent.click(screen.getByTestId("checked-line-press"));
    expect(screen.getAllByTestId("checked-item")).toHaveLength(GOOD.checks.length);
  });
});

// ---------------------------------------------------------- the task result

const RESULT: RunResult = {
  found: true,
  session_id: "session_r5",
  status: "completed",
  task: "make a summary sheet",
  summary: "I saved summary.xlsx.",
  steps: 3,
  tools_used: [{ tool: "write_document", count: 1 }],
  tools_failed: [],
  files_created: ["summary.xlsx"],
  files_created_total: 1,
  files_changed: [],
  files_changed_total: 0,
  errors: [],
  revertable: 1,
  reverted: 0,
  duration_s: 12,
  outcome: "completed",
} as unknown as RunResult;

describe("a task's result", () => {
  it("draws the same line from the result's checked, and none for a run that made no file", () => {
    const { unmount } = render(<RunResultCard result={{ ...RESULT, checked: BAD }} />);
    expect(screen.getByTestId("checked-line-words").textContent).toBe(BAD.line);
    fireEvent.click(screen.getByTestId("checked-line-press"));
    expect(screen.getAllByTestId("checked-item")[0].textContent).toBe(
      "summary.xlsx: C7 on Summary holds #DIV/0! (Problem, by rule)",
    );
    unmount();
    // The quiet form under a run's settled row draws it too.
    const quiet = render(<RunResultCard result={{ ...RESULT, checked: GOOD }} withRow />);
    expect(screen.getByTestId("checked-line-words").textContent).toBe(GOOD.line);
    quiet.unmount();
    // Worth a look, on a task's card: the daemon's line, the warning colour, its own word in the list.
    for (const withRow of [false, true]) {
      const card = render(<RunResultCard result={{ ...RESULT, checked: LOOK }} withRow={withRow} />);
      expect(screen.getByTestId("checked-line-words").textContent).toBe(LOOK.line);
      expect(screen.getByTestId("checked-line").getAttribute("data-tone")).toBe("warn");
      fireEvent.click(screen.getByTestId("checked-line-press"));
      expect(screen.getAllByTestId("checked-item")[0].textContent).toContain("(Worth a look, by rule)");
      card.unmount();
    }
    render(<RunResultCard result={RESULT} />);
    expect(screen.queryByTestId("checked-line")).toBeNull();
    cleanup();
    render(<RunResultCard result={RESULT} withRow />);
    expect(screen.queryByTestId("checked-line")).toBeNull();
  });
});
