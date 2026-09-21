/**
 * v1.284.0 — a job posted from a thread carries the thread, and shows what it
 * made right under the receipt.
 *
 * The user's report: asked a seat for a PDF on the Agents page and "was unable
 * to see the actual PDF the agent created" — the round has no tools, and the
 * one tooled door ("Give it to builder") started a session BLIND ("only the
 * text you typed went with it") whose files lived on another page.
 *
 * Pins:
 *  1. the dispatched body's `task` is the user's words FIRST, then the thread's
 *     recent conversation under a rule — attributed, newest last, clipped;
 *  2. the receipt states how many messages rode along (read off the body), or
 *     says the thread had nothing to carry;
 *  3. a finished job's files render inline (the session page's own
 *     SessionFiles, so preview/open/download behave identically), and a
 *     running one says "Working…" and claims no files.
 *
 * Harness: the v1.180.0 dispatch mocks plus `/sessions/s-new` and its result.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    thread: null as unknown,
    projects: null as unknown,
    sessions: null as unknown,
    /** GET /sessions/s-new — null = the daemon cannot show it (404). */
    sessionDetail: null as unknown,
    /** GET /sessions/s-new/result */
    result: null as unknown,
    posts: [] as { path: string; body: Record<string, unknown> }[],
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => {
    if (path === "/agents/threads/t1") return Promise.resolve(api.thread);
    if (path === "/projects")
      return api.projects === null
        ? Promise.reject(new api.FakeApiError("Not Found", 404))
        : Promise.resolve(api.projects);
    if (path === "/sessions")
      return api.sessions === null
        ? Promise.reject(new api.FakeApiError("", 0))
        : Promise.resolve(api.sessions);
    if (path === "/sessions/s-new")
      return api.sessionDetail === null
        ? Promise.reject(new api.FakeApiError("Not Found", 404))
        : Promise.resolve(api.sessionDetail);
    if (path === "/sessions/s-new/result")
      return api.result === null
        ? Promise.reject(new api.FakeApiError("Not Found", 404))
        : Promise.resolve(api.result);
    return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body: (body ?? {}) as Record<string, unknown> });
    return Promise.resolve({ id: "s-new", status: "active" });
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import {
  JOB_CONTEXT_CLIP,
  JOB_CONTEXT_ENTRIES,
  RoundTable,
  jobContextLines,
  jobTask,
} from "@/components/agents/RoundTable";
import type { Participant, ThreadDetail } from "@/components/agents/identity";

window.HTMLElement.prototype.scrollIntoView = () => {};

const BUILDER: Participant = { key: "builtin:builder", source: "builtin", name: "builder", role: "lead" };

function threadWith(messages: ThreadDetail["messages"]): ThreadDetail {
  return {
    id: "t1",
    title: "Pricing",
    participants: [BUILDER],
    message_count: messages.length,
    updated_at: "2026-08-16T10:00:00Z",
    messages,
  };
}
const TALKED = threadWith([
  { who: "user", content: "flat rate or per-seat?", at: "2026-08-16T10:00:00Z" },
  { who: "builtin:builder", content: "hello there", at: "2026-08-16T10:00:05Z" },
]);

function renderTable() {
  return render(
    <RoundTable threadId="t1" reloadNonce={0} onEditPanel={() => {}} onRoundDone={() => {}} />,
  );
}
async function openAndType(text = "Turn this into a one-page PDF brief") {
  renderTable();
  expect(await screen.findByText("hello there")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Message the panel"), { target: { value: text } });
}
function give() {
  fireEvent.click(screen.getByRole("button", { name: /give it to/i }));
}
function dispatchCall() {
  return api.posts.find((p) => p.path === "/sessions" || p.path.endsWith("/spawn"));
}

beforeEach(() => {
  api.thread = TALKED;
  api.projects = { projects: [] };
  api.sessions = { sessions: [] };
  api.sessionDetail = null;
  api.result = null;
  api.posts = [];
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the thread rides with the job (v1.284.0)", () => {
  it("the body's task is the words first, then the conversation, attributed", async () => {
    await openAndType();
    give();
    await waitFor(() => expect(dispatchCall()).toBeTruthy());
    const task = String(dispatchCall()!.body.task);
    expect(task.startsWith("Turn this into a one-page PDF brief")).toBe(true);
    expect(task).toContain('Context — the panel conversation so far in "Pricing" (2 of 2 messages, newest last):');
    expect(task).toContain("You: flat rate or per-seat?");
    expect(task).toContain("builder (lead): hello there");
    expect(task.indexOf("You: flat rate")).toBeLessThan(task.indexOf("builder (lead): hello"));
    // the receipt states the count read off the body
    await waitFor(() => expect(screen.getByText(/Session started/i)).toBeInTheDocument());
    const receipt = screen
      .getAllByRole("status")
      .find((el) => /builder is doing the work/i.test(el.textContent ?? ""))!;
    expect(receipt).toHaveTextContent(/the last 2 messages of this thread went with it as context/i);
  });

  it("an empty thread carries nothing and the receipt says so", async () => {
    api.thread = threadWith([]);
    renderTable();
    await waitFor(() => expect(screen.getByLabelText("Message the panel")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Message the panel"), { target: { value: "Rename the files" } });
    give();
    await waitFor(() => expect(dispatchCall()).toBeTruthy());
    expect(dispatchCall()!.body.task).toBe("Rename the files");
    await waitFor(() => expect(screen.getByText(/Session started/i)).toBeInTheDocument());
    expect(screen.getByText(/this thread had no conversation to carry/i)).toBeInTheDocument();
  });

  it("jobContextLines keeps the newest entries, clips each, skips error-only seats", () => {
    const many = threadWith(
      Array.from({ length: JOB_CONTEXT_ENTRIES + 5 }, (_, i) => ({
        who: i % 2 === 0 ? "user" : "builtin:builder",
        content: `m${i} ` + "x".repeat(JOB_CONTEXT_CLIP + 50),
        at: "2026-08-16T10:00:00Z",
      })),
    );
    const lines = jobContextLines(many);
    expect(lines).toHaveLength(JOB_CONTEXT_ENTRIES);
    expect(lines[0]).toMatch(/^(You|builder \(lead\)): m5 /); // the oldest kept is entry 5
    expect(lines.at(-1)).toMatch(new RegExp(`m${JOB_CONTEXT_ENTRIES + 4} `));
    for (const ln of lines) expect(ln.length).toBeLessThanOrEqual(JOB_CONTEXT_CLIP + 40);
    expect(lines.every((ln) => ln.endsWith("…"))).toBe(true);
    // an honest error entry has no words worth carrying
    const withError = threadWith([
      { who: "user", content: "hi", at: "t" },
      { who: "builtin:builder", content: "", error: "builder couldn't answer: boom", at: "t" },
    ]);
    expect(jobContextLines(withError)).toEqual(["You: hi"]);
    // an agent not on the panel is named by its name part, never the plumbing
    const stranger = threadWith([{ who: "dynamic:remy", content: "yo", at: "t" }]);
    expect(jobContextLines(stranger)).toEqual(["remy: yo"]);
    expect(jobTask("do it", null)).toBe("do it");
    expect(jobTask("do it", threadWith([]))).toBe("do it");
  });
});

describe("the job's files are under the receipt (v1.284.0)", () => {
  it("a finished job shows its files inline, through the session page's own rail", async () => {
    api.sessionDetail = {
      session: {
        id: "s-new",
        task: "Turn this into a one-page PDF brief",
        agent_type: "builder",
        provider: "mock",
        model: "m",
        status: "completed",
        workspace_path: "C:\\w",
        summary: "Wrote brief.pdf with three sections.",
      },
      transcript: [],
    };
    api.result = {
      found: true,
      session_id: "s-new",
      files_created: ["brief.pdf"],
      files_changed: [],
      files_created_total: 1,
      files_changed_total: 0,
      documents: ["C:\\w\\brief.pdf"],
      revertable: 1,
    };
    await openAndType();
    give();
    const outcome = await screen.findByTestId("job-outcome");
    await waitFor(() => expect(outcome).toHaveTextContent(/Finished\./));
    expect(outcome).toHaveTextContent(/Wrote brief\.pdf with three sections\./);
    const files = await within(outcome).findByTestId("session-files");
    expect(files).toHaveTextContent(/brief\.pdf/);
    // download is the session page's own href shape
    const download = within(files).getAllByRole("link").find((a) => /download=1/.test(a.getAttribute("href") ?? ""));
    expect(download).toBeTruthy();
  });

  it("a running job says Working… and claims no files", async () => {
    api.sessionDetail = {
      session: { id: "s-new", status: "active", summary: "", workspace_path: "C:\\w" },
      transcript: [],
    };
    await openAndType();
    give();
    const outcome = await screen.findByTestId("job-outcome");
    await waitFor(() => expect(outcome).toHaveTextContent(/Working…/));
    expect(within(outcome).queryByTestId("session-files")).toBeNull();
  });

  it("a session the daemon cannot show stays Working… rather than inventing an outcome", async () => {
    await openAndType();
    give();
    const outcome = await screen.findByTestId("job-outcome");
    expect(outcome).toHaveTextContent(/Working…/);
    expect(within(outcome).queryByTestId("session-files")).toBeNull();
  });
});
