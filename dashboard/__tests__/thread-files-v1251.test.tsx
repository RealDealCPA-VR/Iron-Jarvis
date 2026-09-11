/**
 * v1.251.0 (C-01) — a follow-up message remembers the conversation's files.
 *
 * The user's report: attach a return, ask for a summary, then "now turn that
 * into a memo" — and the chat asks for the file again. Chat history crosses as
 * `{role, content}` text, so only THIS message's `attachments` ever rode the
 * request; every earlier file was invisible to the turn.
 *
 * The page already knows the answer: the Files rail (`threadDocs`) is exactly
 * "what this conversation was given or made", it persists in the thread setup,
 * and the user curates it (a dismissed file leaves). So the send carries that
 * list as `thread_files`, MINUS the files this message is attaching, newest
 * few only.
 *
 * Pinned here on the real page with only the transport mocked:
 *  - the first turn carries NO `thread_files` (its file is an attachment);
 *  - the next turn, attaching nothing, carries it;
 *  - a file attached THIS turn is never also carried;
 *  - the list is bounded, newest kept, so a long conversation cannot crowd out
 *    the turn's own prompt;
 *  - a dismissed file stops riding, because the rail is the user's own list.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  get: async (path: string) => {
    const r = H.api.getResponses[path];
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
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
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
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

const UPLOADS = "C:\\home\\uploads";
const FOLDER = "C:\\Users\\me\\Documents\\Iron Jarvis\\2026-09-11 jan";

/** The page's own cap on carried files — kept in step with `MAX_CARRIED_FILES`
 *  in `app/chat/page.tsx`. The daemon bounds the list again on its side. */
const MAX_CARRIED_FILES = 8;

function uploadFor(body: Record<string, unknown>) {
  const name = String(body.filename);
  return { path: `${UPLOADS}\\${name}`, name, bytes: 3 };
}

/** A no-project chat gives the conversation a folder (v1.244.0) and the
 *  attachment paths become the copies inside it — so the paths this test
 *  asserts on are the ones the user's install really sends. */
function workfolderFor(body: Record<string, unknown>) {
  const into = String(body.into || "");
  const files = (body.files as string[]).map((src) => {
    const name = src.split("\\").pop() as string;
    return { name, path: `${into || FOLDER}\\${name}`, bytes: 3, source: src };
  });
  return { path: into || FOLDER, created: !into, files, skipped: [], note: "" };
}

beforeEach(() => {
  H.api.posts.length = 0;
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
  H.api.postResponses["/documents/upload"] = uploadFor;
  H.api.postResponses["/documents/workfolder"] = workfolderFor;
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function attach(...names: string[]) {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const files = names.map((n) => new File(["abc"], n, { type: "application/pdf" }));
  fireEvent.change(input, { target: { files } });
}

async function send(text: string) {
  const before = H.stream.bodies.length;
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(H.stream.bodies.length).toBeGreaterThan(before));
  return H.stream.bodies[H.stream.bodies.length - 1];
}

/** Wait for the attachment chip to carry its FOLDER path: the upload and the
 *  workfolder copy are two awaits, and sending in between would attach the
 *  uploads path instead of the folder one. Waits on the thing being asserted,
 *  never on a proxy signal that lands earlier (CLAUDE.md). */
async function attached(name: string) {
  await waitFor(() =>
    expect(
      H.api.posts.some(
        (p) => p.path === "/documents/workfolder" &&
          (p.body.files as string[]).some((f) => f.endsWith(name)),
      ),
    ).toBe(true),
  );
}

describe("a follow-up message remembers the conversation's files", () => {
  it("carries nothing on the turn that attaches the file, and carries it on the next", async () => {
    render(<ChatPage />);
    await attach("jan.pdf");
    await attached("jan.pdf");

    const first = await send("summarise this return");
    expect(first.attachments).toEqual([`${FOLDER}\\jan.pdf`]);
    // Its own attachment is not ALSO an earlier file.
    expect(first.thread_files).toBeUndefined();

    const second = await send("now turn that into a memo");
    // The follow-up attaches nothing — the key is omitted, as it always was.
    expect(second.attachments).toBeUndefined();
    expect(second.thread_files).toEqual([`${FOLDER}\\jan.pdf`]);
  });

  it("never carries the file this turn is attaching", async () => {
    render(<ChatPage />);
    await attach("jan.pdf");
    await attached("jan.pdf");
    await send("summarise this");

    await attach("feb.pdf");
    await attached("feb.pdf");
    const body = await send("compare them");

    expect(body.attachments).toEqual([`${FOLDER}\\feb.pdf`]);
    expect(body.thread_files).toEqual([`${FOLDER}\\jan.pdf`]);
    expect(body.thread_files).not.toContain(`${FOLDER}\\feb.pdf`);
  });

  it("is bounded to the newest few, so a long conversation cannot crowd the prompt", async () => {
    // The composer takes at most MAX_ATTACHMENTS (4) files per MESSAGE, so a
    // conversation can only pass the carry cap across several turns — which is
    // exactly how the user's conversations actually accumulate files.
    render(<ChatPage />);
    const names: string[] = [];
    for (let turn = 0; turn < 3; turn++) {
      const batch = [0, 1, 2, 3].map((i) => `f${turn}${i}.pdf`);
      await attach(...batch);
      for (const n of batch) await attached(n);
      await send(`read batch ${turn}`);
      names.push(...batch);
    }
    expect(names).toHaveLength(12);

    const body = await send("and now the summary");
    const carried = body.thread_files as string[];
    expect(carried).toHaveLength(MAX_CARRIED_FILES);
    // The NEWEST are what a follow-up means; the oldest roll off.
    expect(carried.at(-1)).toBe(`${FOLDER}\\${names.at(-1)}`);
    expect(carried).not.toContain(`${FOLDER}\\${names[0]}`);
  });

  it("a dismissed file stops riding along", async () => {
    render(<ChatPage />);
    await attach("jan.pdf");
    await attached("jan.pdf");
    await send("summarise this");

    // The rail lives in the project panel, which an ATTACHMENT does not open
    // (only a document a turn MADE does). So this walks the user's own route:
    // open the panel, then dismiss the file from the Files list.
    fireEvent.click(await screen.findByLabelText("Show project panel"));
    // The rail is the user's own list: dismissing a file is an instruction,
    // and a file the user removed must stop riding along with every later turn.
    fireEvent.click(
      await screen.findByRole("button", { name: "Remove jan.pdf from this chat" }),
    );

    const body = await send("now the memo");
    expect(body.thread_files).toBeUndefined();
  });
});
