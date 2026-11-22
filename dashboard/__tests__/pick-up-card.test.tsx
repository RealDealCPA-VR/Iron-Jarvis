/**
 * Next leaps 04-a (build-out plan R2.1, idea 04 "Pick up where you left off"):
 * the card when you open a folder.
 *
 * Pinned here:
 *  - lib/pickUp: the answer decoded (nothing on record = no card), the day
 *    words, the cross kept in this browser until new work is on record,
 *    under a digest that holds no folder's path, and cleared for a folder
 *    the Memory page forgets;
 *  - the card: the full card (Last time, Done, Decided, Still open, one main
 *    press, Continue) only when the daemon says `full` (D-04.4), else one
 *    quiet "Last here: 12 Mar" line that opens it; read ONCE per folder;
 *    Continue sent once however often it is pressed; a failed read says so
 *    in words with Try again; on all three surfaces it steps aside while the
 *    box holds words or a file (one still going up included);
 *  - mounted on all three surfaces: a NEW chat (the real chat page: a folder
 *    pick shows it, Continue sends the fixed sentence as the chat's one
 *    message, a chat being opened neither reads nor shows it, even while its
 *    read is still out), a new Chat › Code session (HarnessStartComposer) and
 *    a new Build › Code session (StartComposer: Continue starts the session
 *    with the fixed sentence alone, and the card says on screen that the
 *    coding tool is told only that, not what the card lists);
 *  - what Continue starts is named after the work it continues
 *    ("<title> (continued)"), never after the fixed sentence.
 */

import React from "react";
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
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  flattenDetail: (d: unknown) => String(d),
  get: async (path: string) => {
    H.api.gets.push(path);
    if (path.startsWith("/fs/list")) return { path: "", entries: [] };
    if (path === "/fs/drives") return { drives: [{ path: "C:\\", label: "C:" }] };
    const r = H.api.getResponses[path];
    if (typeof r === "function") return (r as () => unknown)();
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
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
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  streamSSE: async function* () {},
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("done", "done");
      return { reply: "done" };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
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
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));

import ChatPage from "@/app/chat/page";
import { HarnessStartComposer, startCreateBody, type StartRequest } from "@/components/harness/HarnessStartComposer";
import { StartComposer } from "@/components/buildCode/StartComposer";
import { PickUpCard } from "@/components/work/PickUpCard";
import { decodeCodingTools } from "@/lib/buildApp";
import {
  CONTINUE_SENTENCE,
  PICK_UP_TOOL_NOTE,
  continuedTitle,
  dayWords,
  decodePickUp,
  forgetPickUpNote,
  hideFor,
  isHidden,
  pickUpPath,
  type PickUp,
} from "@/lib/pickUp";
import { WorkByFolder } from "@/components/memory/WorkByFolder";
import { FORGET_CONFIRM, WORK_FOLDERS_PATH, WORK_FORGET_PATH, workEntriesPath } from "@/lib/workRecord";

const BOOKS = "C:\\Work\\Books";
const LEDGERS = "C:\\Work\\Ledgers";

const NOW = Date.parse("2026-11-20T15:00:00Z");

function answer(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    folder: BOOKS,
    name: "Books",
    client: null,
    last: {
      id: "chat:t1",
      kind: "chat",
      title: "Reconcile March bank statement",
      tool: "",
      at: "2026-11-18T10:00:00+00:00",
      link: "/chat?thread=t1",
      outcome: "",
    },
    done: ["Wrote recon_march.xlsx"],
    decided: [
      { text: "Use the bank's dates", entry_id: "chat:t1", title: "Reconcile March", link: "/chat?thread=t1", at: "2026-11-18T10:00:00+00:00" },
    ],
    open: [
      { text: "Two cheques are still unmatched", entry_id: "chat:t1", title: "Reconcile March", link: "/chat?thread=t1", at: "2026-11-18T10:00:00+00:00" },
    ],
    pieces: 1,
    recap_pending: false,
    full: true,
    words: "Last time (Nov 18, 2026): Reconcile March bank statement. 1 still open.",
    ...over,
  };
}

/** The name the chat or session Continue starts is given (the card's last
 *  piece of work, then "(continued)"). */
const CONTINUED = "Reconcile March bank statement (continued)";

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
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
  H.api.postResponses["/documents/folder-check"] = { problem: null, checked: true };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  vi.clearAllMocks();
});

const pickUpReads = () => H.api.gets.filter((p) => p.startsWith("/work-record/pick-up"));

/* ------------------------------------------------------------ lib/pickUp */

describe("the answer, the day and the cross", () => {
  it("decodes the card and answers null when the folder has no earlier work", () => {
    const p = decodePickUp(answer()) as PickUp;
    expect(p.last.title).toBe("Reconcile March bank statement");
    expect(p.last.link).toBe("/chat?thread=t1");
    expect(p.done).toEqual(["Wrote recon_march.xlsx"]);
    expect(p.decided.map((d) => d.text)).toEqual(["Use the bank's dates"]);
    expect(p.open[0].entryId).toBe("chat:t1");
    expect(p.full).toBe(true);
    expect(decodePickUp(answer({ last: null, pieces: 0, full: false }))).toBeNull();
    expect(decodePickUp(null)).toBeNull();
    // A link that leaves the app is dropped, never followed.
    expect((decodePickUp(answer({ last: { id: "x", title: "t", link: "//evil.example" } })) as PickUp).last.link).toBe("");
  });

  it("asks for exactly the folder, and says when the new chat is Local only or a Chat › Code session", () => {
    expect(pickUpPath(BOOKS)).toBe("/work-record/pick-up?folder=C%3A%5CWork%5CBooks");
    expect(pickUpPath(BOOKS, true)).toBe("/work-record/pick-up?folder=C%3A%5CWork%5CBooks&local=true");
    expect(pickUpPath(BOOKS, false, true)).toBe("/work-record/pick-up?folder=C%3A%5CWork%5CBooks&chat_code=true");
  });

  it("says the day as the app does, with the year only when it is not this year", () => {
    expect(dayWords("2026-03-12T12:00:00+00:00", NOW)).toBe("12 Mar");
    expect(dayWords("2025-03-12T12:00:00+00:00", NOW)).toBe("12 Mar 2025");
    expect(dayWords(null, NOW)).toBe("");
  });

  it("the cross hides the card for that folder until new work is on record", () => {
    const p = decodePickUp(answer()) as PickUp;
    expect(isHidden(BOOKS, p)).toBe(false);
    hideFor(BOOKS, p);
    expect(isHidden(BOOKS, p)).toBe(true);
    expect(isHidden("c:/work/books/", p)).toBe(true); // the same folder, spelled another way
    expect(isHidden(LEDGERS, p)).toBe(false);
    const later = decodePickUp(answer({ last: { ...(answer().last as object), at: "2026-11-19T10:00:00+00:00" } })) as PickUp;
    expect(isHidden(BOOKS, later)).toBe(false);
  });

  it("the cross's note keeps no folder's path or name, and forgetting the folder clears its note only", () => {
    const p = decodePickUp(answer()) as PickUp;
    hideFor(BOOKS, p);
    hideFor(LEDGERS, p);
    const raw = window.localStorage.getItem("ij_pick_up_hidden") ?? "";
    expect(Object.keys(JSON.parse(raw))).toHaveLength(2);
    for (const bit of ["work", "books", "ledgers", "\\", "/"]) expect(raw.toLowerCase()).not.toContain(bit);
    forgetPickUpNote("c:/work/books/"); // the same folder, spelled another way
    expect(isHidden(BOOKS, p)).toBe(false);
    expect(isHidden(LEDGERS, p)).toBe(true);
    expect(Object.keys(JSON.parse(window.localStorage.getItem("ij_pick_up_hidden") ?? "{}"))).toHaveLength(1);
  });

  it("names what Continue starts after the work it continues, never after the fixed sentence", () => {
    const named = (title: string, name = "Books") =>
      continuedTitle(decodePickUp(answer({ name, last: { ...(answer().last as object), title } })) as PickUp);
    expect(named("Reconcile March bank statement")).toBe(CONTINUED);
    // A chain of Continues keeps one mark.
    expect(named(CONTINUED)).toBe(CONTINUED);
    expect(named(`${CONTINUED} (continued)`)).toBe(CONTINUED);
    // A last piece named by the sentence itself (typed by the owner): the folder's name.
    expect(named(CONTINUE_SENTENCE)).toBe("Books (continued)");
    expect(named(CONTINUE_SENTENCE.toLowerCase(), "")).toBe("Earlier work (continued)");
    // As long as a chat's own name from its first message, at most.
    const long = named("x".repeat(120));
    expect(long).toBe(`${"x".repeat(47)}… (continued)`);
    expect(long.includes(CONTINUE_SENTENCE)).toBe(false);
  });
});

/* ------------------------------------------------------------ the card */

describe("the card", () => {
  it("the full card: Last time, Done, Decided, Still open and one Continue", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    const card = await screen.findByTestId("pick-up-card");
    expect(card.getAttribute("data-state")).toBe("full");
    expect(within(card).getByTestId("pick-up-last").textContent).toContain("Last time (18 Nov)");
    expect(within(card).getByRole("link", { name: "Reconcile March bank statement" }).getAttribute("href")).toBe(
      "/chat?thread=t1",
    );
    expect(within(card).getByTestId("pick-up-done").textContent).toContain("Wrote recon_march.xlsx");
    expect(within(card).getByTestId("pick-up-decided").textContent).toContain("Use the bank's dates");
    expect(within(card).getByTestId("pick-up-open").textContent).toContain("Still open");
    expect(within(card).getByTestId("pick-up-open").textContent).toContain("Two cheques are still unmatched");
    // One main press (G4).
    expect(within(card).getAllByRole("button").map((b) => b.textContent)).toEqual(["", "Continue"]);
    expect(within(card).getByTestId("pick-up-continue").getAttribute("title")).toContain(CONTINUE_SENTENCE);
  });

  it("D-04.4: otherwise one quiet line, Last here, that opens the card", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer({ full: false, open: [] });
    render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    const quiet = await screen.findByTestId("pick-up-quiet");
    expect(quiet.textContent).toBe("Last here: 18 Nov");
    expect(screen.queryByTestId("pick-up-continue")).toBeNull();
    fireEvent.click(quiet);
    expect(screen.getByTestId("pick-up-card").getAttribute("data-state")).toBe("full");
    expect(screen.getByTestId("pick-up-continue")).toBeTruthy();
  });

  it("a folder with no earlier work shows nothing", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer({ last: null, pieces: 0, done: [], decided: [], open: [], full: false });
    render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    await waitFor(() => expect(pickUpReads()).toHaveLength(1));
    await act(async () => {});
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
  });

  it("is read once per folder, never again for the same folder", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    H.api.getResponses[pickUpPath(LEDGERS)] = answer({ folder: LEDGERS });
    const { rerender } = render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    await screen.findByTestId("pick-up-card");
    rerender(<PickUpCard folder={BOOKS} onContinue={() => {}} hidden />);
    rerender(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    await act(async () => {});
    expect(pickUpReads()).toEqual([pickUpPath(BOOKS)]);
    rerender(<PickUpCard folder={LEDGERS} onContinue={() => {}} />);
    await waitFor(() => expect(pickUpReads()).toEqual([pickUpPath(BOOKS), pickUpPath(LEDGERS)]));
  });

  it("Continue is sent once, however often it is pressed while it goes", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    let release: () => void = () => {};
    const onContinue = vi.fn(() => new Promise<void>((r) => (release = r)));
    render(<PickUpCard folder={BOOKS} onContinue={onContinue} />);
    const press = await screen.findByTestId("pick-up-continue");
    fireEvent.click(press);
    fireEvent.click(press);
    fireEvent.click(press);
    expect(onContinue).toHaveBeenCalledTimes(1);
    await act(async () => release());
  });

  it("a failed read says so in words, and Try again reads again", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = new H.FakeApiError(
      "Your work record could not be read right now. Try again in a moment.",
      503,
    );
    render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    const failed = await screen.findByTestId("pick-up-failed");
    expect(failed.textContent).toBe("Earlier work in this folder couldn't load. Try again.");
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    fireEvent.click(within(failed).getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("pick-up-continue")).toBeTruthy();
    expect(pickUpReads()).toHaveLength(2);
  });

  it("an Iron Jarvis that has no such read (404) shows nothing", async () => {
    render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    await waitFor(() => expect(pickUpReads()).toHaveLength(1));
    await act(async () => {});
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
  });

  it("the cross hides it for this folder", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    const { unmount } = render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    fireEvent.click(await screen.findByTestId("pick-up-hide"));
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    unmount();
    render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    await waitFor(() => expect(pickUpReads()).toHaveLength(2));
    await act(async () => {});
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
  });

  it("a recap waiting to be written again is said", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer({ done: [], decided: [], open: [], recap_pending: true });
    render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    expect((await screen.findByTestId("pick-up-pending")).textContent).toContain("written before the latest messages");
  });
});

/* ------------------------------------------------------------ a new chat */

type Bridge = { pickFolder: ReturnType<typeof vi.fn> };
function desktop(...answers: Array<{ path?: string; cancelled?: boolean }>): Bridge {
  const queue = [...answers];
  const pickFolder = vi.fn(async () => queue.shift() ?? { cancelled: true });
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = { dialog: { pickFolder } };
  return { pickFolder };
}

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

describe("a new chat", () => {
  it("a folder pick shows the card, and Continue sends the fixed sentence as the chat's one message", async () => {
    desktop({ path: BOOKS });
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    render(<ChatPage />);
    fireEvent.click(await screen.findByTestId("chat-hero-folder-choose"));
    const card = await screen.findByTestId("pick-up-card");
    expect(within(card).getByTestId("pick-up-open").textContent).toContain("Two cheques are still unmatched");
    const press = within(card).getByTestId("pick-up-continue");
    fireEvent.click(press);
    fireEvent.click(press);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    const body = H.stream.bodies[0];
    expect(body.workspace_dir).toBe(BOOKS);
    const messages = body.messages as { role: string; content: string }[];
    const users = messages.filter((m) => m.role === "user");
    expect(users.map((m) => m.content)).toEqual([CONTINUE_SENTENCE]);
    // The conversation has started: the card is gone.
    await waitFor(() => expect(screen.queryByTestId("pick-up-card")).toBeNull());
    await act(async () => {});
    expect(H.stream.bodies).toHaveLength(1);
    // The chat is named after the work it continues, by its FIRST save only
    // (a later save never undoes a rename), never after the fixed sentence.
    await waitFor(() => expect(H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).length).toBeGreaterThan(1));
    const saves = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/"));
    expect(saves[0].path).toBe("/chat/threads/new");
    expect(saves[0].body.title).toBe(CONTINUED);
    expect(saves.slice(1).every((p) => !("title" in p.body))).toBe(true);
  });

  it("a message typed in a new chat is named by the daemon as before (no title is sent)", async () => {
    desktop({ path: BOOKS });
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    render(<ChatPage />);
    fireEvent.click(await screen.findByTestId("chat-hero-folder-choose"));
    await screen.findByTestId("pick-up-card");
    const input = await box();
    fireEvent.change(input, { target: { value: "check the March figures" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(H.api.puts.some((p) => p.path === "/chat/threads/new")).toBe(true));
    expect(H.api.puts.every((p) => !("title" in p.body))).toBe(true);
  });

  it("steps aside while the box holds words", async () => {
    desktop({ path: BOOKS });
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    render(<ChatPage />);
    fireEvent.click(await screen.findByTestId("chat-hero-folder-choose"));
    await screen.findByTestId("pick-up-card");
    fireEvent.change(await box(), { target: { value: "something else" } });
    await waitFor(() => expect(screen.queryByTestId("pick-up-card")).toBeNull());
    fireEvent.change(await box(), { target: { value: "" } });
    expect(await screen.findByTestId("pick-up-card")).toBeTruthy();
  });

  it("steps aside while a file goes up (a send pressed then is queued, and would lose Continue's sentence)", async () => {
    desktop({ path: BOOKS });
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    let uploaded: (v: unknown) => void = () => {};
    H.api.postResponses["/documents/upload"] = () => new Promise((r) => (uploaded = r));
    H.api.postResponses["/documents/workfolder"] = { path: BOOKS, created: false, files: [], note: "" };
    render(<ChatPage />);
    fireEvent.click(await screen.findByTestId("chat-hero-folder-choose"));
    await screen.findByTestId("pick-up-continue");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["abc"], "notes.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(H.api.posts.some((x) => x.path === "/documents/upload")).toBe(true));
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    await act(async () => uploaded({ name: "notes.txt", path: "C:\\Work\\Books\\notes.txt" }));
    // The file is in the box (its chip shows), and the card is still aside.
    expect(await screen.findByText("notes.txt")).toBeTruthy();
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    expect(H.stream.bodies).toHaveLength(0);
  });

  it("a new chat in a project neither reads nor shows it (its turn is told the project's activity)", async () => {
    H.api.getResponses["/projects"] = {
      projects: [{ id: "p1", name: "Books project", root: BOOKS, root_exists: true }],
    };
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    window.history.replaceState({}, "", "/chat?project=p1");
    render(<ChatPage />);
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").getAttribute("data-folder")).toBe(BOOKS));
    await act(async () => {});
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    expect(pickUpReads()).toEqual([]);
  });

  // The reviewer's probe: the folder this browser remembers is already the
  // new-chat folder while a chat is being OPENED (no thread id, no messages
  // until its read answers). The card must neither read nor flash a live
  // Continue over the chat being opened: on a ?thread= link (the card's own
  // title link among them) and on the restore of the last open chat at start.
  it.each([
    ["a ?thread= link", () => window.history.replaceState({}, "", "/chat?thread=t9")],
    [
      "the restore of the last open chat",
      () => window.sessionStorage.setItem("ij_chat_open_thread", JSON.stringify({ id: "t9", project: null })),
    ],
  ])("a chat being opened (%s) neither reads nor shows it, before or after its read answers", async (_how, land) => {
    window.localStorage.setItem("ij_chat_workspace", BOOKS);
    let answerThread: () => void = () => {};
    const thread = new Promise<unknown>(
      (r) =>
        (answerThread = () =>
          r({
            id: "t9",
            title: "books",
            updated_at: "S1",
            messages: [
              { role: "user", content: "hi there" },
              { role: "assistant", content: "hello back" },
            ],
            setup: { workspace_dir: BOOKS },
          })),
    );
    H.api.getResponses["/chat/threads/t9"] = () => thread;
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    land();
    render(<ChatPage />);
    // The pane a new chat in Books would show: the hero, its folder chip on
    // Books, and the chat's read asked for (and not answered).
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").getAttribute("data-folder")).toBe(BOOKS));
    await waitFor(() => expect(H.api.gets).toContain("/chat/threads/t9"));
    await act(async () => {});
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    expect(pickUpReads()).toEqual([]);
    // The read answers: the chat is open, and still nothing was read for it.
    await act(async () => answerThread());
    expect(await screen.findByText("hello back")).toBeTruthy();
    await act(async () => {});
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    expect(pickUpReads()).toEqual([]);
  });

  it("a chat that could not be opened is a new chat again: its card shows", async () => {
    window.localStorage.setItem("ij_chat_workspace", BOOKS);
    window.sessionStorage.setItem("ij_chat_open_thread", JSON.stringify({ id: "gone", project: null }));
    H.api.getResponses["/chat/threads/gone"] = new H.FakeApiError("not found", 404);
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    render(<ChatPage />);
    expect(await screen.findByTestId("pick-up-continue")).toBeTruthy();
    expect(H.api.gets).toContain("/chat/threads/gone");
  });
});

/* ------------------------------------------------------------ the two start composers */

describe("a new Chat › Code session", () => {
  it("shows the card for its folder, and Continue starts the session with the fixed sentence once", async () => {
    // Asked as a Chat › Code session (the daemon answers no card for a folder
    // inside a project: that session's turns are told the project's activity).
    H.api.getResponses[pickUpPath(BOOKS, false, true)] = answer();
    const starts: StartRequest[] = [];
    let release: (v: string | null) => void = () => {};
    const onStart = vi.fn((req: StartRequest) => {
      starts.push(req);
      return new Promise<string | null>((r) => (release = r));
    });
    render(<HarnessStartComposer initialFolder={BOOKS} onStart={onStart} />);
    const press = await screen.findByTestId("pick-up-continue");
    fireEvent.click(press);
    fireEvent.click(press);
    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1));
    expect(starts[0].text).toBe(CONTINUE_SENTENCE);
    expect(starts[0].folder).toBe(BOOKS);
    // Named after the work it continues, at its create (a session with a
    // name is not named by its first message).
    expect(starts[0].title).toBe(CONTINUED);
    expect(startCreateBody(starts[0]).title).toBe(CONTINUED);
    await act(async () => release(null));
  });

  it("a session started from the box sends no name (its first message names it, as before)", async () => {
    H.api.getResponses[pickUpPath(BOOKS, false, true)] = answer();
    const onStart = vi.fn(async (_req: StartRequest) => null);
    render(<HarnessStartComposer initialFolder={BOOKS} onStart={onStart} />);
    await screen.findByTestId("pick-up-card");
    fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: "fix the import" } });
    fireEvent.keyDown(screen.getByTestId("harness-new-box"), { key: "Enter" });
    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1));
    expect(onStart.mock.calls[0][0].title).toBeUndefined();
    expect("title" in startCreateBody(onStart.mock.calls[0][0])).toBe(false);
  });

  it("asks as Local only when the session is, and steps aside while the box holds words", async () => {
    H.api.getResponses[pickUpPath(BOOKS, false, true)] = answer();
    H.api.getResponses[pickUpPath(BOOKS, true, true)] = answer();
    render(<HarnessStartComposer initialFolder={BOOKS} onStart={async () => null} />);
    await screen.findByTestId("pick-up-card");
    fireEvent.click(screen.getByTestId("harness-new-local-only"));
    await waitFor(() => expect(pickUpReads()).toContain(pickUpPath(BOOKS, true, true)));
    fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: "fix the import" } });
    await waitFor(() => expect(screen.queryByTestId("pick-up-card")).toBeNull());
  });

  it("steps aside while a file goes up and once it is in the box", async () => {
    H.api.getResponses[pickUpPath(BOOKS, false, true)] = answer();
    let uploaded: (v: unknown) => void = () => {};
    H.api.postResponses["/documents/upload"] = () => new Promise((r) => (uploaded = r));
    const onStart = vi.fn(async (_req: StartRequest) => null);
    render(<HarnessStartComposer initialFolder={BOOKS} onStart={onStart} />);
    await screen.findByTestId("pick-up-continue");
    fireEvent.change(screen.getByTestId("harness-attach-input"), {
      target: { files: [new File(["abc"], "notes.txt", { type: "text/plain" })] },
    });
    await waitFor(() => expect(H.api.posts.some((x) => x.path === "/documents/upload")).toBe(true));
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    await act(async () => uploaded({ name: "notes.txt", path: "C:\\Work\\Books\\notes.txt" }));
    expect(await screen.findByTestId("harness-attachment")).toBeTruthy();
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    expect(onStart).not.toHaveBeenCalled();
  });

  it("no folder: no card and no read", async () => {
    render(<HarnessStartComposer onStart={async () => null} />);
    await screen.findByTestId("harness-new-box");
    await act(async () => {});
    expect(pickUpReads()).toEqual([]);
  });
});

const TOOLS = decodeCodingTools({
  harnesses: [
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Codex's default" }],
      efforts: [],
      accounts: [],
    },
  ],
});

const MADE = {
  id: "s1",
  harness: "codex",
  folder: BOOKS,
  no_folder: false,
  work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis",
  title: "",
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
};

describe("a new Build › Code session", () => {
  it("shows the card for its folder, and Continue starts the session with the fixed sentence once", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    H.api.postResponses["/build/app/sessions"] = MADE;
    const onStarted = vi.fn();
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={BOOKS} onStarted={onStarted} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Codex"));
    const press = await screen.findByTestId("pick-up-continue");
    // The user's own coding tool is told only the sentence, never what the
    // card lists: the card SAYS so in those words, on screen beside Continue
    // (a hover is never seen on a phone), and the press's hover says it too.
    const card = screen.getByTestId("pick-up-card");
    expect(within(card).getByTestId("pick-up-tool-note").textContent).toBe(PICK_UP_TOOL_NOTE);
    expect(PICK_UP_TOOL_NOTE).toBe(
      `Your coding tool is not told what this card lists. Continue sends it only "${CONTINUE_SENTENCE}" and nothing else.`,
    );
    expect(press.getAttribute("title")).toBe(PICK_UP_TOOL_NOTE);
    fireEvent.click(press);
    fireEvent.click(press);
    await waitFor(() => expect(onStarted).toHaveBeenCalledTimes(1));
    // What the note says is what happens: the session starts with the
    // sentence alone (no card line, no file).
    expect(onStarted.mock.calls[0][1]).toBe(CONTINUE_SENTENCE);
    expect(onStarted.mock.calls[0][2]).toEqual([]);
    const creates = H.api.posts.filter((p) => p.path === "/build/app/sessions");
    expect(creates).toHaveLength(1);
    expect(creates[0].body.folder).toBe(BOOKS);
    // Named after the work it continues, never after the fixed sentence.
    expect(creates[0].body.title).toBe(CONTINUED);
  });

  it("steps aside while the box holds words or a file, one still going up included", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    let uploaded: (v: unknown) => void = () => {};
    H.api.postResponses["/documents/upload"] = () => new Promise((r) => (uploaded = r));
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={BOOKS} onStarted={() => {}} />);
    await screen.findByTestId("pick-up-continue");
    const box = screen.getByTestId("code-start-box");
    fireEvent.change(box, { target: { value: "fix the cart" } });
    await waitFor(() => expect(screen.queryByTestId("pick-up-card")).toBeNull());
    fireEvent.change(box, { target: { value: "" } });
    expect(await screen.findByTestId("pick-up-continue")).toBeTruthy();
    // A file going up: aside at once (a Continue held for the upload would
    // start the session WITH that file)...
    fireEvent.change(screen.getByTestId("harness-attach-input"), {
      target: { files: [new File(["abc"], "notes.txt", { type: "text/plain" })] },
    });
    await waitFor(() => expect(H.api.posts.some((x) => x.path === "/documents/upload")).toBe(true));
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    // ...and still aside once it is in the box.
    await act(async () => uploaded({ name: "notes.txt", path: "C:\\Work\\Books\\notes.txt" }));
    expect(await screen.findByTestId("harness-attachment")).toBeTruthy();
    expect(screen.queryByTestId("pick-up-card")).toBeNull();
    expect(H.api.posts.filter((x) => x.path === "/build/app/sessions")).toEqual([]);
  });

  it("a new chat and a Chat › Code session say nothing about a coding tool", async () => {
    H.api.getResponses[pickUpPath(BOOKS)] = answer();
    render(<PickUpCard folder={BOOKS} onContinue={() => {}} />);
    await screen.findByTestId("pick-up-continue");
    expect(screen.queryByTestId("pick-up-tool-note")).toBeNull();
  });
});

/* ------------------------------------------------------------ Forget this folder */

describe("Forget this folder on the Memory page", () => {
  it("clears that folder's cross note in this browser, and keeps every other folder's", async () => {
    const p = decodePickUp(answer()) as PickUp;
    hideFor(BOOKS, p);
    hideFor(LEDGERS, p);
    H.api.getResponses[WORK_FOLDERS_PATH] = {
      folders: [{ folder: BOOKS, name: "Books", count: 1, last_at: null, kinds: { chat: 1 } }],
      total: 1,
      building: false,
      forgotten: [],
    };
    H.api.getResponses[workEntriesPath(BOOKS)] = {
      folder: BOOKS,
      name: "Books",
      total: 1,
      entries: [
        {
          id: "chat:t1", kind: "chat", ref: "t1", title: "Reconcile March bank statement", tool: "", outcome: "",
          local_only: false, files: [], files_total: 0, note: "", updated_at: null, link: "/chat?thread=t1", recap: null,
        },
      ],
    };
    H.api.postResponses[WORK_FORGET_PATH] = { ok: true, removed: 1, folder: BOOKS };
    render(<WorkByFolder />);
    fireEvent.click(await screen.findByRole("button", { name: /Books/ }));
    await screen.findByText("Reconcile March bank statement");
    fireEvent.click(screen.getByTestId("work-forget"));
    expect(screen.getByTestId("work-forget").textContent).toContain(FORGET_CONFIRM);
    expect(isHidden(BOOKS, p)).toBe(true);
    await act(async () => {
      fireEvent.click(screen.getByTestId("work-forget"));
    });
    await waitFor(() => expect(H.api.posts.filter((x) => x.path === WORK_FORGET_PATH)).toHaveLength(1));
    await waitFor(() => expect(isHidden(BOOKS, p)).toBe(false));
    expect(isHidden(LEDGERS, p)).toBe(true);
  });
});
