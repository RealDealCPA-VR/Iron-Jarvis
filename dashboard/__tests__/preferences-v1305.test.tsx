/**
 * v1.305.0 — preferences you approve: a repeated correction becomes ONE quiet
 * question, and the Memory page shows every preference by status.
 *
 * Chat: the done frame / POST response carries `suggestion` (null or {id,
 * text, count, quotes, since}). The hook WHITELISTS it (like `remembered`),
 * both lanes store it on the message, and a quiet line under the receipt asks
 * "keep it as a standing preference?" with Keep · Edit · Not this. Keep turns
 * the line into "Remembered: …" (the v1.282.0 wording); the answer is saved
 * on the message so a reload renders the outcome; a turn that began before
 * the press can never put the question back on disk.
 *
 * Memory page: Kept / Suggested / Never ask again + the consent-per-press look
 * through Claude Code and Codex sessions. A 404 (older daemon) keeps exactly
 * the v1.279.0 list. Bell: only a SCAN-minted suggestion rings, quietly.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      patches: [] as { path: string; body: Record<string, unknown> }[],
      dels: [] as string[],
      gets: [] as string[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      suggestion: null as unknown,
      /** When set, the NEXT run waits for it before answering (mid-turn press). */
      gate: null as Promise<void> | null,
      /** Make the stream look absent (404) so the page falls back to POST /chat. */
      absent: false,
    },
    events: [] as { id: string; type: string; ts: string; payload?: Record<string, unknown> }[],
    notify: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.getResponses[path];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return typeof r === "function" ? (r as () => unknown)() : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async (path: string, body: Record<string, unknown>) => {
    H.api.patches.push({ path, body });
    const r = H.api.postResponses[`PATCH ${path}`];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  del: async (path: string) => {
    H.api.dels.push(path);
    return {};
  },
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      if (H.stream.absent) {
        const e = new H.FakeStreamError("no stream", 404);
        throw e;
      }
      const gate = H.stream.gate;
      H.stream.gate = null;
      if (gate) await gate;
      await new Promise<void>((r) => setTimeout(r, 0));
      const n = H.stream.bodies.length;
      const reply = `Reply ${n}.`;
      onDelta(reply, reply);
      const sug = H.stream.suggestion;
      H.stream.suggestion = null;
      return {
        reply,
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
        tools_used: [],
        ...(sug ? { suggestion: sug } : {}),
      };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events, connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: H.notify,
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
import { PreferenceSuggestion } from "@/components/chat/PreferenceSuggestion";
import { KnowsAboutYou, type MemoryOverview } from "@/components/memory/KnowsAboutYou";
import { NotificationBell, toActivity } from "@/components/NotificationBell";
import {
  applySettled,
  decodePreferences,
  decodeSuggestion,
  keptTextFrom,
  scanResultLine,
  suggestionAsk,
  type ChatSuggestion,
} from "@/lib/preferences";
import type { IJEvent } from "@/lib/types";

// ---------------------------------------------------------------- fixtures

const WIRE_SUGGESTION = {
  id: "lesson_ab12",
  text: "Make it shorter.",
  count: 2,
  quotes: [
    { quote: "too long, make it shorter", at: "2026-10-05T10:00:00", where: "chat", link: "/chat?thread=t7" },
    { quote: "shorter please", at: "2026-10-01T09:00:00", where: "phone" },
  ],
  since: "2026-10-01T09:00:00",
};

const pref = (over: Record<string, unknown>) => ({
  id: "lesson_x",
  text: "Prefers numbered steps.",
  status: "confirmed",
  origin: "said",
  source: "preference",
  weight: 5,
  created_at: "2026-09-18T10:00:00",
  decided_at: null,
  count: 0,
  evidence: [],
  ...over,
});

const PREFS = {
  kept: [
    pref({ id: "k1", text: "Prefers numbered steps.", origin: "said" }),
    pref({ id: "k2", text: "Use plain words.", origin: "noticed", count: 2 }),
    pref({ id: "k3", text: "Wants research in his own voice", origin: null, source: "feedback" }),
  ],
  suggested: [
    pref({
      id: "s1",
      text: "Make it shorter.",
      status: "proposed",
      origin: "noticed",
      count: 2,
      evidence: [
        { quote: "too long, make it shorter", at: "2026-10-05T10:00:00", where: "chat" },
        { quote: "shorter please", at: "2026-10-01T09:00:00", where: "claude-code" },
      ],
    }),
  ],
  never: [pref({ id: "n1", text: "No tables.", status: "declined", origin: "noticed" })],
  open_limit: 3,
  scan: {
    sources: [
      { id: "claude-code", label: "Claude Code", available: true },
      { id: "codex", label: "Codex", available: true },
    ],
  },
};

const OVERVIEW: MemoryOverview = {
  profile: { filled: true, enabled: true, about_line: "Goes by Dana Reyes.", tone: "neutral", writing_style: "narrative" },
  preferences: [
    { id: "l1", text: "Prefers short answers with numbered steps", source: "preference", weight: 5, created_at: "2026-09-18" },
    { id: "l2", text: "Wants research written in her own voice", source: "feedback", weight: 3, created_at: "2026-09-17" },
  ],
  lessons: { total: 2, reflections: 0, by_source: { preference: 1, feedback: 1 } },
  bases: [{ name: "brain", kind: "markdown", notes: 4 }],
  working: { session: 0, project: 0, user: 0, org: 0 },
  history: { docs: 0, available: true },
  empty: false,
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
});

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.api.patches.length = 0;
  H.api.dels.length = 0;
  H.api.gets.length = 0;
  H.stream.bodies.length = 0;
  H.stream.suggestion = null;
  H.stream.gate = null;
  H.stream.absent = false;
  H.events.length = 0;
  H.notify.mockReset();
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = CHAT_GETS();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------- decoders

describe("the wire shapes (v1.305.0)", () => {
  it("decodeSuggestion keeps a valid suggestion and nothing else", () => {
    const s = decodeSuggestion(WIRE_SUGGESTION)!;
    expect(s).toEqual({
      id: "lesson_ab12",
      text: "Make it shorter.",
      count: 2,
      quotes: [
        { quote: "too long, make it shorter", at: "2026-10-05T10:00:00", where: "chat", link: "/chat?thread=t7" },
        { quote: "shorter please", at: "2026-10-01T09:00:00", where: "phone" },
      ],
      since: "2026-10-01T09:00:00",
    });
    for (const junk of [null, undefined, "x", 7, [], {}, { id: "a" }, { text: "t" }, { id: "a", text: "   " }]) {
      expect(decodeSuggestion(junk)).toBeNull();
    }
    // At most two quotes; an off-dashboard link is dropped; junk quotes skipped.
    const many = decodeSuggestion({
      id: "x",
      text: "t",
      quotes: ["", { quote: "a", link: "https://evil.example" }, "b", "c"],
    })!;
    expect(many.quotes).toEqual([
      { quote: "a", at: "", where: "" },
      { quote: "b", at: "", where: "" },
    ]);
    // The user's answer rides a persisted message back through the decoder.
    expect(decodeSuggestion({ ...WIRE_SUGGESTION, state: "kept" })!.state).toBe("kept");
    expect(decodeSuggestion({ ...WIRE_SUGGESTION, state: "bogus" })!.state).toBeUndefined();
  });

  it("decodePreferences reads B's grouped answer and refuses anything else", () => {
    const v = decodePreferences(PREFS)!;
    expect(v.kept.map((r) => r.id)).toEqual(["k1", "k2", "k3"]);
    expect(v.suggested[0].evidence).toHaveLength(2);
    expect(v.never[0].status).toBe("declined");
    expect(v.scanSources).toEqual(["claude-code", "codex"]);
    const none = decodePreferences({
      ...PREFS,
      scan: { sources: [{ id: "claude-code", available: false }, { id: "codex", available: false }] },
    })!;
    expect(none.scanSources).toEqual([]);
    // The overview object (what a wholesale mock hands every path) is not it.
    expect(decodePreferences(OVERVIEW)).toBeNull();
    expect(decodePreferences(null)).toBeNull();
    expect(decodePreferences({ kept: [], suggested: [] })).toBeNull();
  });

  it("the small words", () => {
    expect(suggestionAsk({ text: "Make it shorter.", count: 2 })).toBe(
      "You've said this twice: “Make it shorter” — keep it as a standing preference?",
    );
    expect(keptTextFrom({ preference: { text: "Shorter." } }, "x")).toBe("Shorter.");
    expect(keptTextFrom({}, "fallback")).toBe("fallback");
    expect(scanResultLine({ sessions_read: 12, suggestions: 1, suggested: [] })).toBe("Read 12 sessions — 1 suggestion");
    expect(scanResultLine({ sessions_read: 1, suggestions: 0, skipped_full: true })).toBe(
      "Read 1 session — 0 suggestions (three are already waiting — answer those first)",
    );
    expect(scanResultLine({})).toBeNull();
  });

  it("applySettled replaces an open ask with the answer, and nothing else", () => {
    const open = decodeSuggestion(WIRE_SUGGESTION)!;
    const kept: ChatSuggestion = { ...open, state: "kept", text: "Shorter." };
    const msgs = [{ role: "user" }, { role: "assistant", suggestion: open }, { role: "assistant" }];
    const out = applySettled(msgs, new Map([[open.id, kept]]));
    expect(out[1].suggestion).toEqual(kept);
    expect(out[0]).toBe(msgs[0]);
    expect(applySettled(msgs, new Map())).toBe(msgs);
  });
});

describe("the done frame carries the suggestion (whitelist, like remembered)", () => {
  it("decodes a valid one and drops null", async () => {
    const { decodeSSE } = await vi.importActual<typeof import("@/lib/useChatStream")>("@/lib/useChatStream");
    const ev = decodeSSE("done", JSON.stringify({ reply: "r", suggestion: WIRE_SUGGESTION })) as {
      suggestion?: ChatSuggestion;
    };
    expect(ev.suggestion?.id).toBe("lesson_ab12");
    expect(ev.suggestion?.quotes).toHaveLength(2);
    const none = decodeSSE("done", JSON.stringify({ reply: "r", suggestion: null })) as { suggestion?: unknown };
    expect("suggestion" in none).toBe(false);
  });

  it("the real hook puts it on the resolved result", async () => {
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>("@/lib/useChatStream");
    const { renderHook } = await import("@testing-library/react");
    const frames = `event: done\ndata: ${JSON.stringify({ reply: "ok", suggestion: WIRE_SUGGESTION })}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { suggestion?: ChatSuggestion } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      expect(settled!.suggestion?.text).toBe("Make it shorter.");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

// ---------------------------------------------------------------- the line

describe("the quiet line under the reply", () => {
  const S = () => decodeSuggestion(WIRE_SUGGESTION)!;

  it("asks in one sentence with Keep · Edit · Not this, and takes no focus", () => {
    const box = document.createElement("textarea");
    document.body.appendChild(box);
    box.focus();
    try {
      render(<PreferenceSuggestion suggestion={S()} />);
      const line = document.getElementById("pref-suggestion-lesson_ab12")!;
      expect(line).not.toBeNull();
      expect(line.getAttribute("data-state")).toBe("open");
      expect(line.textContent).toContain("You've said this twice: “Make it shorter” — keep it as a");
      expect(screen.getByTestId("pref-suggestion-keep").textContent).toBe("Keep");
      expect(screen.getByTestId("pref-suggestion-edit").textContent).toBe("Edit");
      expect(screen.getByTestId("pref-suggestion-decline").textContent).toBe("Not this");
      // The composer keeps the caret: the line never grabs focus on arrival.
      expect(document.activeElement).toBe(box);
      // Quiet: no dialog, no alert, no status toast.
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
    } finally {
      box.remove();
    }
  });

  it("hover names the two quotes, and the times word expands them with where", () => {
    render(<PreferenceSuggestion suggestion={S()} />);
    const why = screen.getByTestId("pref-suggestion-why");
    expect(why.getAttribute("title")).toContain("“too long, make it shorter” — chat");
    expect(why.getAttribute("title")).toContain("“shorter please” — phone");
    expect(screen.queryByTestId("pref-suggestion-quotes")).toBeNull();
    fireEvent.click(why);
    const panel = screen.getByTestId("pref-suggestion-quotes");
    expect(panel.textContent).toContain("“too long, make it shorter”");
    expect(panel.textContent).toContain("phone");
  });

  it("Keep posts the keep route and the line becomes Remembered: <the daemon's text>", async () => {
    H.api.postResponses["/memory/preferences/lesson_ab12/keep"] = {
      preference: { id: "lesson_ab12", text: "Keep replies short.", status: "confirmed" },
    };
    const onSettle = vi.fn();
    render(<PreferenceSuggestion suggestion={S()} onSettle={onSettle} />);
    fireEvent.click(screen.getByTestId("pref-suggestion-keep"));
    const kept = await screen.findByTestId("pref-suggestion-kept");
    expect(kept.textContent).toBe("Remembered: Keep replies short.");
    expect(kept.className).toContain("text-accent-soft");
    expect(H.api.posts).toEqual([{ path: "/memory/preferences/lesson_ab12/keep", body: {} }]);
    expect(onSettle).toHaveBeenCalledWith(expect.objectContaining({ id: "lesson_ab12", state: "kept", text: "Keep replies short." }));
    expect(screen.queryByTestId("pref-suggestion-keep")).toBeNull();
  });

  it("Edit opens a prefilled field; Enter keeps the edited words and never reaches the page", async () => {
    const parentKeys = vi.fn();
    render(
      <div onKeyDown={(e) => parentKeys(e.key)}>
        <PreferenceSuggestion suggestion={S()} />
      </div>,
    );
    fireEvent.click(screen.getByTestId("pref-suggestion-edit"));
    const input = screen.getByTestId("pref-suggestion-input") as HTMLInputElement;
    expect(input.value).toBe("Make it shorter.");
    fireEvent.change(input, { target: { value: "Keep answers under five lines." } });
    fireEvent.keyDown(input, { key: "Enter" });
    const kept = await screen.findByTestId("pref-suggestion-kept");
    expect(kept.textContent).toBe("Remembered: Keep answers under five lines.");
    expect(H.api.posts).toEqual([
      { path: "/memory/preferences/lesson_ab12/keep", body: { text: "Keep answers under five lines." } },
    ]);
    expect(parentKeys).not.toHaveBeenCalled();
  });

  it("Escape in the field puts the line back and never reaches a running turn's Stop", () => {
    const parentKeys = vi.fn();
    render(
      <div onKeyDown={(e) => parentKeys(e.key)}>
        <PreferenceSuggestion suggestion={S()} />
      </div>,
    );
    fireEvent.click(screen.getByTestId("pref-suggestion-edit"));
    fireEvent.keyDown(screen.getByTestId("pref-suggestion-input"), { key: "Escape" });
    expect(screen.queryByTestId("pref-suggestion-input")).toBeNull();
    expect(screen.getByTestId("pref-suggestion-keep")).toBeTruthy();
    expect(parentKeys).not.toHaveBeenCalled();
    expect(H.api.posts).toEqual([]);
  });

  it("Not this declines — final, and said quietly", async () => {
    const onSettle = vi.fn();
    render(<PreferenceSuggestion suggestion={S()} onSettle={onSettle} />);
    fireEvent.click(screen.getByTestId("pref-suggestion-decline"));
    await waitFor(() =>
      expect(document.getElementById("pref-suggestion-lesson_ab12")!.getAttribute("data-state")).toBe("declined"),
    );
    expect(document.getElementById("pref-suggestion-lesson_ab12")!.textContent).toBe(
      "Won't suggest “Make it shorter” again.",
    );
    expect(H.api.posts).toEqual([{ path: "/memory/preferences/lesson_ab12/decline", body: {} }]);
    expect(onSettle).toHaveBeenCalledWith(expect.objectContaining({ state: "declined" }));
  });

  it("a refusal is said on the line and the question stays", async () => {
    H.api.postResponses["/memory/preferences/lesson_ab12/keep"] = new H.FakeApiError(
      "That sentence can't be kept: it reads like an instruction to the model.",
      400,
    );
    const onSettle = vi.fn();
    render(<PreferenceSuggestion suggestion={S()} onSettle={onSettle} />);
    fireEvent.click(screen.getByTestId("pref-suggestion-keep"));
    const err = await screen.findByTestId("pref-suggestion-error");
    expect(err.textContent).toContain("That sentence can't be kept");
    expect(screen.getByTestId("pref-suggestion-keep")).toBeTruthy();
    expect(onSettle).not.toHaveBeenCalled();
    // A genuine refusal never re-reads the list.
    expect(H.api.gets).not.toContain("/memory/preferences");
  });

  // Answered ELSEWHERE (the Memory page, another window): the old line's
  // press gets 409 / 404 and settles to the real outcome instead of an error.
  const answeredElsewhere = (over: Partial<Record<"kept" | "suggested" | "never", unknown[]>>) => ({
    ...PREFS,
    kept: [] as unknown[],
    suggested: [] as unknown[],
    never: [] as unknown[],
    ...over,
  });

  it("409 on Keep, kept elsewhere: the line becomes Remembered: <the words actually kept>", async () => {
    H.api.postResponses["/memory/preferences/lesson_ab12/keep"] = new H.FakeApiError(
      "That one isn't a suggestion any more.",
      409,
    );
    H.api.getResponses["/memory/preferences"] = answeredElsewhere({
      kept: [pref({ id: "lesson_ab12", text: "Keep replies under five lines.", origin: "noticed" })],
    });
    const onSettle = vi.fn();
    render(<PreferenceSuggestion suggestion={S()} onSettle={onSettle} />);
    fireEvent.click(screen.getByTestId("pref-suggestion-keep"));
    const kept = await screen.findByTestId("pref-suggestion-kept");
    expect(kept.textContent).toBe("Remembered: Keep replies under five lines.");
    expect(H.api.gets).toContain("/memory/preferences");
    expect(screen.queryByTestId("pref-suggestion-error")).toBeNull();
    expect(onSettle).toHaveBeenCalledWith(
      expect.objectContaining({ id: "lesson_ab12", state: "kept", text: "Keep replies under five lines." }),
    );
  });

  it("409 on Keep, declined elsewhere: the line says it won't suggest it again", async () => {
    H.api.postResponses["/memory/preferences/lesson_ab12/keep"] = new H.FakeApiError(
      "That one isn't a suggestion any more.",
      409,
    );
    H.api.getResponses["/memory/preferences"] = answeredElsewhere({
      never: [pref({ id: "lesson_ab12", text: "Make it shorter.", status: "declined", origin: "noticed" })],
    });
    const onSettle = vi.fn();
    render(<PreferenceSuggestion suggestion={S()} onSettle={onSettle} />);
    fireEvent.click(screen.getByTestId("pref-suggestion-keep"));
    await waitFor(() =>
      expect(document.getElementById("pref-suggestion-lesson_ab12")!.getAttribute("data-state")).toBe("declined"),
    );
    expect(screen.queryByTestId("pref-suggestion-error")).toBeNull();
    expect(onSettle).toHaveBeenCalledWith(expect.objectContaining({ id: "lesson_ab12", state: "declined" }));
  });

  it("404 on Not this, gone elsewhere: the line disappears quietly", async () => {
    H.api.postResponses["/memory/preferences/lesson_ab12/decline"] = new H.FakeApiError(
      "No such preference.",
      404,
    );
    H.api.getResponses["/memory/preferences"] = answeredElsewhere({
      kept: [pref({ id: "k1", text: "Prefers numbered steps." })],
    });
    const onSettle = vi.fn();
    render(<PreferenceSuggestion suggestion={S()} onSettle={onSettle} />);
    fireEvent.click(screen.getByTestId("pref-suggestion-decline"));
    await waitFor(() => expect(document.getElementById("pref-suggestion-lesson_ab12")).toBeNull());
    expect(screen.queryByTestId("pref-suggestion-error")).toBeNull();
    expect(onSettle).toHaveBeenCalledWith(expect.objectContaining({ id: "lesson_ab12", state: "gone" }));
  });

  it("409 whose re-read fails, or still shows it as a suggestion: the sentence is said", async () => {
    H.api.postResponses["/memory/preferences/lesson_ab12/keep"] = new H.FakeApiError(
      "That one isn't a suggestion any more.",
      409,
    );
    H.api.getResponses["/memory/preferences"] = new H.FakeApiError("Failed to fetch", 0);
    const onSettle = vi.fn();
    render(<PreferenceSuggestion suggestion={S()} onSettle={onSettle} />);
    fireEvent.click(screen.getByTestId("pref-suggestion-keep"));
    const err = await screen.findByTestId("pref-suggestion-error");
    expect(err.textContent).toContain("isn't a suggestion any more");
    expect(onSettle).not.toHaveBeenCalled();
    cleanup();
    H.api.getResponses["/memory/preferences"] = answeredElsewhere({
      suggested: [pref({ id: "lesson_ab12", text: "Make it shorter.", status: "proposed" })],
    });
    render(<PreferenceSuggestion suggestion={S()} onSettle={onSettle} />);
    fireEvent.click(screen.getByTestId("pref-suggestion-keep"));
    expect((await screen.findByTestId("pref-suggestion-error")).textContent).toContain("isn't a suggestion");
    expect(screen.getByTestId("pref-suggestion-keep")).toBeTruthy();
    expect(onSettle).not.toHaveBeenCalled();
  });

  it("a decided suggestion renders its outcome (a reload never asks again)", () => {
    render(<PreferenceSuggestion suggestion={{ ...S(), state: "kept", text: "Shorter." }} />);
    expect(screen.getByTestId("pref-suggestion-kept").textContent).toBe("Remembered: Shorter.");
    expect(screen.queryByTestId("pref-suggestion-keep")).toBeNull();
  });
});

// ---------------------------------------------------------------- the page

async function send(text: string, reply: string) {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await screen.findByText(reply);
}

const threadSaves = () => H.api.puts.filter((p) => p.path.startsWith("/chat/threads/"));
const lastSaved = () =>
  threadSaves().at(-1)!.body.messages as { role: string; content: string; suggestion?: ChatSuggestion }[];

describe("the chat page — both lanes (v1.305.0)", () => {
  it("stream lane: the line renders under the reply; Keep saves the answer on the message", async () => {
    H.stream.suggestion = WIRE_SUGGESTION;
    H.api.postResponses["/memory/preferences/lesson_ab12/keep"] = {
      preference: { id: "lesson_ab12", text: "Make it shorter.", status: "confirmed" },
    };
    render(<ChatPage />);
    await send("too long, make it shorter", "Reply 1.");
    const line = await waitFor(() => {
      const el = document.getElementById("pref-suggestion-lesson_ab12");
      if (!el) throw new Error("no line yet");
      return el;
    });
    expect(line.getAttribute("data-state")).toBe("open");
    // Saved with the message, still open, so a reload asks the same question.
    await waitFor(() => expect(lastSaved().at(-1)!.suggestion?.id).toBe("lesson_ab12"));
    expect(lastSaved().at(-1)!.suggestion?.state).toBeUndefined();
    const saves = threadSaves().length;
    fireEvent.click(within(line).getByTestId("pref-suggestion-keep"));
    await waitFor(() => expect(threadSaves().length).toBeGreaterThan(saves));
    expect(lastSaved().at(-1)!.suggestion).toEqual(
      expect.objectContaining({ id: "lesson_ab12", state: "kept", text: "Make it shorter." }),
    );
    expect(screen.getByTestId("pref-suggestion-kept").textContent).toBe("Remembered: Make it shorter.");
  });

  it("POST lane (no /chat/stream): the same line, from the response's suggestion", async () => {
    H.stream.absent = true;
    H.api.postResponses["/chat"] = {
      reply: "Posted reply.",
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
      remembered: [],
      suggestion: WIRE_SUGGESTION,
    };
    render(<ChatPage />);
    await send("shorter please", "Posted reply.");
    await waitFor(() => expect(document.getElementById("pref-suggestion-lesson_ab12")).not.toBeNull());
    await waitFor(() => expect(lastSaved().at(-1)!.suggestion?.id).toBe("lesson_ab12"));
  });

  it("a turn with no suggestion shows no line", async () => {
    render(<ChatPage />);
    await send("hello", "Reply 1.");
    expect(screen.queryByTestId("pref-suggestion")).toBeNull();
  });

  it("a Keep pressed MID-TURN survives that turn's own save", async () => {
    H.stream.suggestion = WIRE_SUGGESTION;
    render(<ChatPage />);
    await send("too long, make it shorter", "Reply 1.");
    await waitFor(() => expect(document.getElementById("pref-suggestion-lesson_ab12")).not.toBeNull());
    let release: () => void = () => {};
    H.stream.gate = new Promise<void>((r) => {
      release = r;
    });
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "and the next thing" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies.length).toBe(2));
    // The turn is running; the user answers the line under the first reply.
    fireEvent.click(screen.getByTestId("pref-suggestion-keep"));
    await screen.findByTestId("pref-suggestion-kept");
    await act(async () => {
      release();
    });
    await screen.findByText("Reply 2.");
    await waitFor(() => expect(lastSaved().length).toBe(4));
    const first = lastSaved()[1];
    expect(first.suggestion?.state).toBe("kept");
    expect(screen.getByTestId("pref-suggestion-kept").textContent).toBe("Remembered: Make it shorter.");
  });

  it("a reopened thread renders the saved answer, not the question", async () => {
    window.history.replaceState({}, "", "/chat?thread=t9");
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Shorter",
      messages: [
        { role: "user", content: "too long, make it shorter" },
        {
          role: "assistant",
          content: "Earlier reply.",
          route: { requested: "", provider: "mock", model: "mock", reason: "default" },
          suggestion: { ...WIRE_SUGGESTION, state: "kept", text: "Keep it short." },
        },
      ],
    };
    render(<ChatPage />);
    await screen.findByText("Earlier reply.");
    expect((await screen.findByTestId("pref-suggestion-kept")).textContent).toBe("Remembered: Keep it short.");
    expect(screen.queryByTestId("pref-suggestion-keep")).toBeNull();
  });

  it("answered on the Memory page: the old line settles to the real outcome and saves it", async () => {
    H.stream.suggestion = WIRE_SUGGESTION;
    H.api.postResponses["/memory/preferences/lesson_ab12/keep"] = new H.FakeApiError(
      "That one isn't a suggestion any more.",
      409,
    );
    H.api.getResponses["/memory/preferences"] = {
      ...PREFS,
      kept: [pref({ id: "lesson_ab12", text: "Keep replies short.", origin: "noticed" })],
      suggested: [],
      never: [],
    };
    render(<ChatPage />);
    await send("too long, make it shorter", "Reply 1.");
    await waitFor(() => expect(lastSaved().at(-1)!.suggestion?.id).toBe("lesson_ab12"));
    const saves = threadSaves().length;
    fireEvent.click(screen.getByTestId("pref-suggestion-keep"));
    await waitFor(() => expect(threadSaves().length).toBeGreaterThan(saves));
    expect(lastSaved().at(-1)!.suggestion).toEqual(
      expect.objectContaining({ id: "lesson_ab12", state: "kept", text: "Keep replies short." }),
    );
    expect(screen.getByTestId("pref-suggestion-kept").textContent).toBe("Remembered: Keep replies short.");
    expect(screen.queryByTestId("pref-suggestion-error")).toBeNull();
  });

  it("asked again / forgotten elsewhere: the line goes, and a reload keeps it gone", async () => {
    H.stream.suggestion = WIRE_SUGGESTION;
    H.api.postResponses["/memory/preferences/lesson_ab12/decline"] = new H.FakeApiError("No such preference.", 404);
    H.api.getResponses["/memory/preferences"] = { ...PREFS, kept: [], suggested: [], never: [] };
    render(<ChatPage />);
    await send("too long, make it shorter", "Reply 1.");
    await waitFor(() => expect(lastSaved().at(-1)!.suggestion?.id).toBe("lesson_ab12"));
    const saves = threadSaves().length;
    fireEvent.click(screen.getByTestId("pref-suggestion-decline"));
    await waitFor(() => expect(threadSaves().length).toBeGreaterThan(saves));
    expect(lastSaved().at(-1)!.suggestion).toEqual(expect.objectContaining({ id: "lesson_ab12", state: "gone" }));
    expect(screen.queryByTestId("pref-suggestion")).toBeNull();
    expect(screen.queryByTestId("pref-suggestion-error")).toBeNull();
    // The saved answer survives a reload: nothing renders, nothing asks.
    cleanup();
    window.history.replaceState({}, "", "/chat?thread=t9");
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Shorter",
      messages: [
        { role: "user", content: "too long, make it shorter" },
        {
          role: "assistant",
          content: "Earlier reply.",
          route: { requested: "", provider: "mock", model: "mock", reason: "default" },
          suggestion: { ...WIRE_SUGGESTION, state: "gone" },
        },
      ],
    };
    render(<ChatPage />);
    await screen.findByText("Earlier reply.");
    expect(screen.queryByTestId("pref-suggestion")).toBeNull();
    expect(screen.queryByTestId("pref-suggestion-keep")).toBeNull();
  });

  it("source: the page never subscribes to the composer store for this", async () => {
    const { readFileSync } = await import("node:fs");
    const read = (p: string) => readFileSync(p, "utf8").replace(/\r\n/g, "\n");
    const src = read("components/chat/PreferenceSuggestion.tsx");
    expect(src).not.toMatch(/composerStore|useComposer/);
  });
});

// ---------------------------------------------------------------- Memory page

describe("What Jarvis knows about you — by status (v1.305.0)", () => {
  beforeEach(() => {
    H.api.getResponses = { "/memory/overview": OVERVIEW, "/memory/preferences": PREFS };
  });

  it("an older daemon (404) keeps exactly today's list", async () => {
    delete H.api.getResponses["/memory/preferences"];
    render(<KnowsAboutYou />);
    await screen.findByTestId("knows-about-you");
    await waitFor(() => expect(H.api.gets).toContain("/memory/preferences"));
    const items = screen.getByTestId("knows-preferences").querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("you said so");
    expect(document.getElementById("prefs-kept")).toBeNull();
    expect(document.getElementById("prefs-scan")).toBeNull();
  });

  it("shows Kept, Suggested (with the user's words) and Never ask again, under stable ids", async () => {
    render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("prefs-kept")).not.toBeNull());
    const kept = document.getElementById("prefs-kept")!;
    expect(kept.textContent).toContain("Prefers numbered steps.");
    expect(kept.textContent).toContain("you said so");
    expect(kept.textContent).toContain("you kept a suggestion");
    expect(kept.textContent).toContain("from your feedback");
    const sug = document.getElementById("prefs-suggested")!;
    expect(sug.textContent).toContain("Make it shorter.");
    expect(sug.textContent).toContain("“shorter please” — Claude Code");
    expect(within(sug).getAllByTestId("prefs-quote")).toHaveLength(2);
    const never = document.getElementById("prefs-never")!;
    expect(never.textContent).toContain("“No tables”");
    expect(within(never).getByTestId("prefs-ask-again")).toBeTruthy();
  });

  it("Keep moves a suggestion to Kept; Not this moves it to Never; Ask again clears a declined one", async () => {
    H.api.postResponses["/memory/preferences/s1/keep"] = { preference: pref({ id: "s1", text: "Make it shorter." }) };
    render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("prefs-suggested")).not.toBeNull());
    // The re-read after the press answers with the daemon's new grouping.
    H.api.getResponses["/memory/preferences"] = { ...PREFS, suggested: [], kept: [...PREFS.kept, pref({ id: "s1", text: "Make it shorter." })] };
    fireEvent.click(within(document.getElementById("prefs-suggested")!).getByTestId("prefs-keep"));
    await waitFor(() => expect(document.getElementById("prefs-suggested")).toBeNull());
    expect(document.getElementById("prefs-kept")!.textContent).toContain("Make it shorter.");
    expect(H.api.posts).toEqual([{ path: "/memory/preferences/s1/keep", body: {} }]);

    fireEvent.click(within(document.getElementById("prefs-never")!).getByTestId("prefs-ask-again"));
    await waitFor(() => expect(H.api.posts.at(-1)!.path).toBe("/memory/preferences/n1/ask-again"));
  });

  it("Not this declines from the page", async () => {
    render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("prefs-suggested")).not.toBeNull());
    H.api.getResponses["/memory/preferences"] = {
      ...PREFS,
      suggested: [],
      never: [pref({ id: "s1", text: "Make it shorter.", status: "declined" }), ...PREFS.never],
    };
    fireEvent.click(within(document.getElementById("prefs-suggested")!).getByTestId("prefs-decline"));
    await waitFor(() => expect(H.api.posts).toEqual([{ path: "/memory/preferences/s1/decline", body: {} }]));
    await waitFor(() => expect(document.getElementById("prefs-never")!.textContent).toContain("Make it shorter"));
  });

  it("Edit on a suggestion keeps the edited words; a refusal stays on the row", async () => {
    H.api.postResponses["/memory/preferences/s1/keep"] = new H.FakeApiError("That sentence can't be kept: too long.", 400);
    render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("prefs-suggested")).not.toBeNull());
    const sug = document.getElementById("prefs-suggested")!;
    fireEvent.click(within(sug).getByTestId("prefs-edit"));
    const input = within(sug).getByTestId("prefs-edit-input") as HTMLInputElement;
    expect(input.value).toBe("Make it shorter.");
    fireEvent.change(input, { target: { value: "Two paragraphs at most." } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(H.api.posts).toEqual([{ path: "/memory/preferences/s1/keep", body: { text: "Two paragraphs at most." } }]),
    );
    expect((await within(sug).findByTestId("prefs-error")).textContent).toContain("can't be kept");
    expect(document.getElementById("prefs-suggested")).not.toBeNull();
  });

  it("a kept preference can be edited (PATCH) and forgotten (DELETE)", async () => {
    render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("prefs-kept")).not.toBeNull());
    const kept = document.getElementById("prefs-kept")!;
    fireEvent.click(within(kept).getAllByTestId("prefs-edit")[0]);
    const input = within(kept).getByTestId("prefs-edit-input");
    fireEvent.change(input, { target: { value: "Prefers numbered steps, always." } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(H.api.patches).toEqual([{ path: "/memory/preferences/k1", body: { text: "Prefers numbered steps, always." } }]),
    );
    // Wait for the EDIT TO FINISH, not for its request: the section ignores
    // any press while one action is in flight (`act`'s busy guard clears in a
    // `finally` after the PATCH resolves), so a Forget pressed in that window
    // is dropped — the v1.311.0 Release gate (a smaller runner) found the
    // window. Finished = the editor is gone and no row is busy (every Forget
    // is enabled again — the precondition the press needs).
    await waitFor(() => {
      const kept = document.getElementById("prefs-kept")!;
      expect(within(kept).queryByTestId("prefs-edit-input")).toBeNull();
      const forgets = within(kept).getAllByTestId("prefs-forget") as HTMLButtonElement[];
      expect(forgets.length).toBe(3);
      expect(forgets.every((b) => !b.disabled)).toBe(true);
    });
    const k2row = within(document.getElementById("prefs-kept")!)
      .getByText("Use plain words.")
      .closest("li") as HTMLElement;
    fireEvent.click(within(k2row).getByTestId("prefs-forget"));
    await waitFor(() => expect(H.api.dels).toEqual(["/memory/preferences/k2"]));
  });

  it("the deeper look: consent said first, one press reads, the result is one line", async () => {
    H.api.postResponses["/memory/preferences/scan"] = {
      sessions_read: 12,
      by_source: { "claude-code": 8, codex: 4 },
      suggestions: 1,
      suggested: [],
      skipped_full: false,
    };
    render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("prefs-scan")).not.toBeNull());
    const scan = document.getElementById("prefs-scan")!;
    // Before any press: what is read, and that it stays here.
    expect(scan.textContent).toContain("Reads only the messages you typed");
    expect(scan.textContent).toContain("never the replies or tool output");
    expect(scan.textContent).toContain("stays on this PC");
    const button = within(scan).getByTestId("prefs-scan-button");
    expect(button.textContent).toBe("Look through my Claude Code and Codex sessions");
    // Nothing was read on arrival: consent is the press.
    expect(H.api.posts).toEqual([]);
    fireEvent.click(button);
    expect((await within(scan).findByTestId("prefs-scan-result")).textContent).toBe("Read 12 sessions — 1 suggestion");
    expect(H.api.posts).toEqual([{ path: "/memory/preferences/scan", body: { sources: ["claude-code", "codex"] } }]);
  });

  it("names only the apps found, and offers nothing when neither is here", async () => {
    H.api.getResponses["/memory/preferences"] = {
      ...PREFS,
      scan: { sources: [{ id: "claude-code", label: "Claude Code", available: true }, { id: "codex", label: "Codex", available: false }] },
    };
    const first = render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("prefs-scan")).not.toBeNull());
    expect(screen.getByTestId("prefs-scan-button").textContent).toBe("Look through my Claude Code sessions");
    first.unmount();
    H.api.getResponses["/memory/preferences"] = {
      ...PREFS,
      scan: { sources: [{ id: "claude-code", available: false }, { id: "codex", available: false }] },
    };
    render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("prefs-kept")).not.toBeNull());
    expect(document.getElementById("prefs-scan")).toBeNull();
  });
});

// ---------------------------------------------------------------- the bell

const ev = (type: string, payload: Record<string, unknown>, id = "e1"): IJEvent =>
  ({ id, type, ts: "2026-10-06T10:00:00", payload }) as unknown as IJEvent;

describe("the bell rings for a scan's suggestion only, quietly", () => {
  it("maps via=scan to a Memory-page row and ignores the in-chat one", () => {
    const row = toActivity(ev("preference.suggested", { id: "s1", text: "Make it shorter.", count: 2, via: "scan" }))!;
    expect(row.href).toBe("/memory#prefs-suggested");
    expect(row.body).toBe("Make it shorter.");
    expect(row.quiet).toBe(true);
    expect(toActivity(ev("preference.suggested", { id: "s1", text: "x", via: "chat" }))).toBeNull();
    expect(toActivity(ev("preference.suggested", { id: "s1", text: "x" }))).toBeNull();
  });

  it("shows the row in the dropdown and pings no desktop notification for it", async () => {
    H.api.getResponses = {
      "/computeruse": { pending_approvals: 0 },
      "/diagnostics": { pending_reviews: 0 },
      "/chat/approvals/pending": { approvals: [] },
      "/workflows/runs?status=waiting&slim=true&limit=200": { runs: [] },
    };
    H.events.push(ev("preference.suggested", { id: "s1", text: "Make it shorter.", count: 2, via: "scan" }, "e-pref"));
    const view = render(<NotificationBell />);
    fireEvent.click(screen.getByRole("button", { name: /notifications/i }));
    const link = await screen.findByText("A suggested preference is waiting on the Memory page");
    expect(link.closest("a")!.getAttribute("href")).toBe("/memory#prefs-suggested");
    expect(H.notify).not.toHaveBeenCalled();
    view.unmount();
    // Anti-vacuity: an ordinary activity row in the same harness DOES ping.
    H.events.length = 0;
    H.events.push(ev("schedule.fired", { name: "nightly" }, "e-sched"));
    render(<NotificationBell />);
    await waitFor(() => expect(H.notify).toHaveBeenCalled());
  });
});

// ------------------------------------------------- v1.320.0: rate a reply
//
// The user: "Teach it your style" said "rate a finished session", and Chat —
// where it sent them — had no rating at all. Every settled reply now carries
// 👍 / 👎; a 👎 asks what to change, and the answer is the lesson.

describe("rate a reply in Chat (v1.320.0)", () => {
  const feedbackPosts = () => H.api.posts.filter((p) => p.path === "/chat/feedback");

  it("the newest reply asks 'Was this helpful?'; 👍 records it and saves it on the message", async () => {
    render(<ChatPage />);
    await send("hello", "Reply 1.");
    const row = await screen.findByTestId("reply-rating");
    expect(row.textContent).toMatch(/Was this helpful\?/);
    const saves = threadSaves().length;
    fireEvent.click(within(row).getByRole("button", { name: "Good reply" }));
    expect(await screen.findByTestId("reply-rated")).toHaveTextContent("Thanks — noted.");
    expect(feedbackPosts()).toEqual([
      { path: "/chat/feedback", body: expect.objectContaining({ rating: "up", comment: "" }) },
    ]);
    await waitFor(() => expect(threadSaves().length).toBeGreaterThan(saves));
    expect((lastSaved().at(-1) as { rating?: unknown }).rating).toEqual({ value: "up" });
  });

  it("👎 asks what should be different; the answer is sent and said back", async () => {
    render(<ChatPage />);
    await send("hello", "Reply 1.");
    fireEvent.click(within(await screen.findByTestId("reply-rating")).getByRole("button", { name: "Not quite right" }));
    const ask = screen.getByTestId("reply-rating-ask");
    const box = within(ask).getByLabelText("What should be different next time?");
    expect(document.activeElement).toBe(box);
    fireEvent.change(box, { target: { value: "shorter, with bullets" } });
    fireEvent.click(within(ask).getByRole("button", { name: "Save" }));
    expect(await screen.findByTestId("reply-rated")).toHaveTextContent(
      "Noted — Jarvis will remember: “shorter, with bullets”",
    );
    expect(feedbackPosts().at(-1)!.body).toEqual(
      expect.objectContaining({ rating: "down", comment: "shorter, with bullets" }),
    );
    await waitFor(() =>
      expect((lastSaved().at(-1) as { rating?: unknown }).rating).toEqual({
        value: "down",
        note: "shorter, with bullets",
      }),
    );
  });

  it("Enter in the 👎 box saves the rating and never sends a chat message", async () => {
    render(<ChatPage />);
    await send("hello", "Reply 1.");
    fireEvent.click(within(await screen.findByTestId("reply-rating")).getByRole("button", { name: "Not quite right" }));
    const box = screen.getByLabelText("What should be different next time?");
    fireEvent.change(box, { target: { value: "use plain words" } });
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.submit(box.closest("form")!);
    await screen.findByTestId("reply-rated");
    expect(H.stream.bodies.length).toBe(1); // only the first turn
  });

  it("a reopened chat shows the saved rating, not the buttons", async () => {
    window.history.replaceState({}, "", "/chat?thread=t9");
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Rated",
      messages: [
        { role: "user", content: "hi" },
        {
          role: "assistant",
          content: "Earlier reply.",
          route: { requested: "", provider: "mock", model: "mock", reason: "default" },
          rating: { value: "down", note: "less formal" },
        },
      ],
    };
    render(<ChatPage />);
    await screen.findByText("Earlier reply.");
    expect(await screen.findByTestId("reply-rated")).toHaveTextContent("less formal");
    expect(screen.queryByTestId("reply-rating")).toBeNull();
  });

  it("a failed save says so and leaves the buttons", async () => {
    H.api.postResponses["/chat/feedback"] = new H.FakeApiError("down", 0);
    render(<ChatPage />);
    await send("hello", "Reply 1.");
    fireEvent.click(within(await screen.findByTestId("reply-rating")).getByRole("button", { name: "Good reply" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Couldn't save that/);
    expect(screen.queryByTestId("reply-rated")).toBeNull();
  });
});
