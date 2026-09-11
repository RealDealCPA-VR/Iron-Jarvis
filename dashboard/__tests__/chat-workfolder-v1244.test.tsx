/**
 * v1.244.0 — attaching a file to a chat with NO project gives the conversation
 * its own folder and the file tools a project would.
 *
 * The user's report: "When I attach documents in the chat module and ask for a
 * task to be completed, I often get a lagging delay, a request for information
 * and then a completed screen with absolutely no output." Replayed on the live
 * model: with no project the chat had no folder and no file tools, so it handed
 * the job to an agent in a hidden scratch folder, which built the workbook,
 * stopped twice for approval, ran out of steps and reported "Task failed"
 * beside a file nobody could find. In a project none of that happens, because
 * selecting a project binds the project folder and arms the file essentials.
 *
 * Pinned here, on the real page with only the transport mocked:
 *  - the first attachment asks the daemon for a conversation folder, and the
 *    chip's path becomes the copy INSIDE it;
 *  - the send carries that folder as `workspace_dir` and the project file
 *    essentials as `tools`, so the turn can finish the job itself;
 *  - a later attachment joins the SAME folder (`into`), never a second one;
 *  - a folder refusal degrades to the old behaviour (the upload still
 *    attaches) instead of losing the file;
 *  - the folder is per conversation: it is never written to the sticky
 *    localStorage default a plain New chat returns to.
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
      /** POST outcome per path: an Error rejects, a function is called with the
       *  body, any other value resolves. */
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
  // v1.250.0 (S-03): see the real hook — no store on a mock, so the text is
  // read straight off the object these fakes return.
  useLiveText: (s: { text?: string }) => s?.text ?? "",
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
const FOLDER = "C:\\Users\\me\\Documents\\Iron Jarvis\\2026-09-11 HarborPoint Q1 Expenses";
const PROJECT_FILE_TOOLS = ["file_search", "read_document", "write_document", "write_file"];

function uploadFor(body: Record<string, unknown>) {
  const name = String(body.filename);
  return { path: `${UPLOADS}\\${name}`, name, bytes: 3 };
}

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
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(H.stream.bodies.length).toBeGreaterThan(0));
  return H.stream.bodies[H.stream.bodies.length - 1];
}

const workfolderCalls = () => H.api.posts.filter((p) => p.path === "/documents/workfolder");

describe("attach with no project → the conversation gets its own folder", () => {
  it("asks for a folder named after the file, and the chip points at the copy inside it", async () => {
    render(<ChatPage />);
    await attach("HarborPoint_Q1_Expenses.pdf");

    await waitFor(() => expect(workfolderCalls()).toHaveLength(1));
    expect(workfolderCalls()[0].body).toMatchObject({
      files: [`${UPLOADS}\\HarborPoint_Q1_Expenses.pdf`],
      title: "HarborPoint_Q1_Expenses.pdf",
      prefer: "",
    });
    // The user can see where the work will land.
    expect(await screen.findByTestId("workfolder-chip")).toHaveTextContent(
      "2026-09-11 HarborPoint Q1 Expenses",
    );

    const body = await send("Put these expenses into an Excel workbook");
    expect(body.attachments).toEqual([`${FOLDER}\\HarborPoint_Q1_Expenses.pdf`]);
    expect(body.workspace_dir).toBe(FOLDER);
    expect(body.tools).toEqual(PROJECT_FILE_TOOLS);
  });

  it("a later attachment joins the SAME folder — never a second one", async () => {
    render(<ChatPage />);
    await attach("jan.pdf");
    await waitFor(() => expect(workfolderCalls()).toHaveLength(1));
    await screen.findByTestId("workfolder-chip");
    await attach("feb.pdf");

    await waitFor(() => expect(workfolderCalls()).toHaveLength(2));
    expect(workfolderCalls()[1].body).toMatchObject({ files: [`${UPLOADS}\\feb.pdf`], into: FOLDER });
    const body = await send("compare them");
    expect(body.attachments).toEqual([`${FOLDER}\\jan.pdf`, `${FOLDER}\\feb.pdf`]);
    expect(body.workspace_dir).toBe(FOLDER);
  });

  it("the folder belongs to the conversation, not to the sticky default", async () => {
    render(<ChatPage />);
    await attach("x.pdf");
    await screen.findByTestId("workfolder-chip");
    expect(window.localStorage.getItem("ij_chat_workspace")).toBeNull();
  });

  it("tools the user armed themselves are kept, not replaced", async () => {
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    window.localStorage.setItem("ij_chat_workspace", "");
    await attach("x.pdf");
    await screen.findByTestId("workfolder-chip");
    const body = await send("go");
    // With nothing armed beforehand, the essentials are armed…
    expect(body.tools).toEqual(PROJECT_FILE_TOOLS);
  });

  it("a folder refusal keeps the attachment (the old behaviour) and says why", async () => {
    H.api.postResponses["/documents/workfolder"] = new H.FakeApiError(
      "could not make a working folder under D:\\Iron Jarvis",
      409,
    );
    render(<ChatPage />);
    await attach("x.pdf");
    await waitFor(() => expect(workfolderCalls()).toHaveLength(1));
    expect(screen.queryByTestId("workfolder-chip")).not.toBeInTheDocument();

    const body = await send("summarise it");
    expect(body.attachments).toEqual([`${UPLOADS}\\x.pdf`]);
    expect(body.workspace_dir).toBeUndefined();
  });
});
