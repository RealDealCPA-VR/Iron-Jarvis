/**
 * v1.354.0 (product-readiness G20): THE RUN PAGE READS AS A RECEIPT and can
 * continue an interrupted run.
 *
 * ACCEPTANCE (as a new user): opening a run from chat shows the task as the
 * title, "Took 2 min 5 s", plain step words and a collapsed "Full record"; a
 * run cut by a restart says "Stopped when Iron Jarvis restarted" with a
 * Continue that resumes its conversation.
 *
 * Harness: the REAL session page (app/sessions/[id]/page.tsx) with only the
 * transport mocked (approval-session-page-v13520's). TimeTravelFeed is mocked
 * to its component ALONE on purpose: ~11 suites mock it that way, and the
 * page's live words must not need any other export from it (they come from
 * lib/eventWords).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    FakeApiError,
  };
});
const H = vi.hoisted(() => ({
  run: {
    text: "",
    tools: [] as Record<string, unknown>[],
    phase: null as null | { phase: string; detail: string },
    active: false,
  },
  events: [] as Record<string, unknown>[],
  push: vi.fn(),
  back: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve(api.postResponses[path] ?? {});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ ...H.run, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events }) }));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({ enabled: false, supported: false, toggle: () => {}, speak: () => {}, stop: () => {} }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: H.push, back: H.back }) }));
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({
  TimeTravelFeed: () => <div data-testid="time-travel-feed" />,
}));
vi.mock("@/components/chat/DocPreview", () => ({ DocPreview: () => null, appLabelFor: () => "app" }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // Mock contract (v1.250.0): every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import SessionDetailPage from "@/app/sessions/[id]/page";
import { CONTINUE_AFTER_RESTART } from "@/components/InterruptedJobs";
import type { SessionView } from "@/lib/types";

const ID = "session_7b32f746bc3a";
const WS = "C:/Users/me/Documents/Iron Jarvis/2026-10-18 lease";

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setRun(over: Partial<SessionView> & Record<string, unknown> = {}, transcript?: unknown) {
  const session = {
    id: ID,
    task: "Summarise the **engagement** letter\nThen email the summary to Dana",
    agent_type: "builder",
    provider: "openai",
    model: "gpt-x",
    status: "completed",
    workspace_path: WS,
    summary: "Wrote a one-page summary to summary.md",
    // The daemon's own stamps are UNZONED (SQLite) — read in one form.
    created_at: "2026-10-18T09:00:00.000000",
    finished_at: "2026-10-18T09:02:05.000000",
    ...over,
  } as unknown as SessionView;
  api.responses[`/sessions/${ID}`] = {
    session,
    transcript: transcript ?? {
      runs: [{ id: "run_1", agent_type: "builder", state: "completed", steps: 3, parent_id: null, result: "done" }],
      tools: [{ id: "t_1", tool: "write_file", verdict: "allow", ok: true, output: "wrote summary.md" }],
    },
  };
}

function open() {
  return render(<SessionDetailPage params={fakeParams(ID)} />);
}

beforeEach(() => {
  H.run = { text: "", tools: [], phase: null, active: false };
  H.events = [];
  H.push.mockReset();
  H.back.mockReset();
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});
afterEach(() => {
  cleanup();
  api.gets = [];
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
});

describe("the run's page reads as a receipt", () => {
  it("is titled by what it was asked — its first line, plain text — never 'Session' and an id", async () => {
    setRun();
    open();
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1.textContent).toContain("Summarise the engagement letter");
    expect(h1.textContent).not.toContain("**");
    expect(h1.textContent).not.toContain("Then email");
    expect(h1.textContent).not.toMatch(/^Session$/);
    expect(h1.textContent).not.toContain(ID);
    // The whole task is still on the page: the Summary card's own line, and
    // the title's hint (the header shows it on demand).
    expect(screen.getAllByText(/Then email the summary to Dana/).length).toBeGreaterThan(0);
    expect(screen.getByTestId("page-subtitle").textContent).toContain(
      "Summarise the engagement letter Then email the summary to Dana",
    );
  });

  it("the id sits behind a copy button", async () => {
    setRun();
    open();
    const copy = await screen.findByRole("button", { name: "Copy run id" });
    fireEvent.click(copy);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(ID);
  });

  it("says how long it took in the chat receipt's clock words: 'Took 2 min 5 s'", async () => {
    setRun();
    open();
    const took = await screen.findByTestId("run-took");
    expect(took.textContent).toBe("2 min 5 s");
    expect(screen.getByText("Took", { selector: "div" })).toBeInTheDocument();
  });

  it("CONTROL: a run still going has no 'Took' yet", async () => {
    setRun({ status: "active", finished_at: null });
    open();
    await screen.findByTestId("session-summary");
    expect(screen.queryByTestId("run-took")).toBeNull();
  });

  it("names a custom agent by its own name, not the base type it runs on", async () => {
    setRun({ agent_type: "builder", agent_name: "custom:tax-preparer" });
    open();
    await screen.findByTestId("session-summary");
    const value = screen.getByText("Agent", { selector: "div" }).nextElementSibling as HTMLElement;
    const span = value.querySelector("span") as HTMLElement;
    expect(span.textContent).toBe("tax-preparer");
    expect(span.getAttribute("title")).toBe("custom:tax-preparer");
    // Shown as typed: no CSS casing over a custom name.
    expect(span.className).not.toContain("capitalize");
  });

  it("a model server the user set up reads by the name the model menu gives it", async () => {
    setRun({ provider: "fleet-a1b2c3", model: "llama-3-70b" });
    api.responses["/models"] = {
      models: [{ provider: "fleet-a1b2c3", model: "llama-3-70b", name: "DGX Spark" }],
    };
    open();
    await screen.findByTestId("session-summary");
    const value = screen.getByText("Model", { selector: "div" }).nextElementSibling as HTMLElement;
    expect(value.textContent).toBe("DGX Spark · llama-3-70b");
    expect(value.textContent).not.toContain("fleet-a1b2c3");
    // The raw stamp is still the record, on hover.
    expect(value.querySelector("[title]")?.getAttribute("title")).toBe("fleet-a1b2c3 / llama-3-70b");
  });

  it("the full record is ONE collapsed chevron: Time-travel, Evaluation and the raw tables open on demand", async () => {
    setRun();
    api.responses[`/sessions/${ID}/evaluation`] = {
      completion: 1,
      tool_success_rate: 1,
      tool_calls: 1,
      step_count: 3,
      latency_s: 125,
    };
    open();
    await screen.findByTestId("session-summary");
    const toggle = screen.getByTestId("full-record-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle.textContent).toContain("Full record");
    for (const t of ["Time-travel", "Evaluation", "Agent runs · 1", "Tool invocations · 1"]) {
      expect(screen.queryByText(t)).toBeNull();
    }
    expect(screen.queryByTestId("time-travel-feed")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const region = document.getElementById(toggle.getAttribute("aria-controls") ?? "");
    expect(region).not.toBeNull();
    for (const t of ["Time-travel", "Evaluation", "Agent runs · 1", "Tool invocations · 1"]) {
      expect(within(region as HTMLElement).getByText(t)).toBeInTheDocument();
    }
    expect(within(region as HTMLElement).getByTestId("time-travel-feed")).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByText("Time-travel")).toBeNull();
  });

  it("the run controls stay in the receipt, above the fold", async () => {
    setRun();
    open();
    await screen.findByTestId("session-summary");
    const controls = screen.getByRole("heading", { level: 2, name: "Run controls" });
    const toggle = screen.getByTestId("full-record-toggle");
    expect(controls.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: /Rerun/ })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Send a follow-up/)).toBeInTheDocument();
  });

  it("Back goes back to where the user came from (a chat) when there is history", async () => {
    setRun();
    window.history.pushState({}, "", "/chat?thread=t1");
    window.history.pushState({}, "", `/sessions/${ID}`);
    open();
    const back = await screen.findByTestId("run-back");
    expect(back.textContent).toContain("Back");
    // With nowhere to go back to it is still a plain link to the list.
    expect(back.getAttribute("href")).toBe("/sessions");
    fireEvent.click(back);
    expect(H.back).toHaveBeenCalledTimes(1);
  });
});

describe("a live run speaks in the chat's step words", () => {
  it("the phase reads as words ('Planning the work…'), never the raw phase id", async () => {
    setRun({ status: "running", finished_at: null });
    H.run = { text: "", tools: [], phase: { phase: "planning", detail: "" }, active: true };
    open();
    const phase = await screen.findByTestId("live-run-phase");
    expect(phase.textContent).toBe("Planning the work…");
    expect(phase.className).not.toContain("capitalize");
  });

  it("the phase's own detail follows the words", async () => {
    setRun({ status: "running", finished_at: null });
    H.run = { text: "", tools: [], phase: { phase: "running", detail: "step 2 of 3: draft" }, active: true };
    open();
    const phase = await screen.findByTestId("live-run-phase");
    expect(phase.textContent).toBe("Working — step 2 of 3: draft");
  });

  it("the run's tool calls are the chat's quiet rows ('Read notes.txt'), not monospace id chips", async () => {
    setRun({ status: "running", finished_at: null });
    H.run = {
      text: "",
      tools: [{ id: "c1", name: "read_file", status: "done", ok: true, args: { path: `${WS}/notes.txt` } }],
      phase: { phase: "running", detail: "" },
      active: true,
    };
    open();
    const rows = await screen.findByTestId("live-tool-rows");
    expect(within(rows).getByText("notes.txt")).toBeInTheDocument();
    expect(within(rows).getByTestId("live-work-words").textContent).toContain("read 1 file");
    // No pill of the raw tool id anywhere in the card.
    const card = rows.closest("section, div[class*='rounded']") ?? document.body;
    expect(Array.from(card.querySelectorAll(".font-mono")).some((el) => el.textContent === "read_file")).toBe(false);
  });

  it("live activity rows are words — never the payload as JSON", async () => {
    setRun({ status: "running", finished_at: null });
    H.events = [
      { id: "e4", type: "weird.new_kind", ts: "2026-10-18T09:00:04Z", session_id: ID, payload: { token: "abc", n: 1 } },
      { id: "e3", type: "session.completed", ts: "2026-10-18T09:00:03Z", session_id: ID, payload: { status: "completed" } },
      { id: "e2", type: "provider.routed", ts: "2026-10-18T09:00:02Z", session_id: ID, payload: { provider: "openai", model: "gpt-x" } },
      { id: "e1", type: "tool.executed", ts: "2026-10-18T09:00:01Z", session_id: ID, payload: { tool: "write_file", ok: false } },
    ];
    open();
    await screen.findByTestId("session-summary");
    const rows = screen.getAllByTestId("live-activity-row");
    expect(rows).toHaveLength(4);
    const text = rows.map((r) => r.textContent ?? "").join("\n");
    expect(text).not.toMatch(/[{}]/);
    expect(text).not.toContain('"token"');
    expect(rows[3].textContent).toContain("Made · write_file");
    expect(rows[3].textContent).toContain("failed");
    expect(rows[2].textContent).toContain("Picked a model for this turn");
    expect(rows[2].textContent).toContain("OpenAI · gpt-x");
    expect(rows[1].textContent).toContain("Session finished");
    expect(rows[1].textContent).toContain("Done");
    // An event nobody has words for reads as its own name (raw, on purpose),
    // with nothing after it.
    expect(rows[0].textContent).toContain("weird.new_kind");
    // The raw type is always on hover.
    expect(rows[2].querySelector('[title="provider.routed"]')).not.toBeNull();
  });
});

describe("a run a restart cut off", () => {
  const INTERRUPTED = {
    status: "failed",
    interrupted_at: "2026-10-18T09:01:00.000000",
    summary: "interrupted by a daemon restart",
  };

  it("says 'Stopped when Iron Jarvis restarted', reads 'Interrupted' (never a red 'Failed'), and offers Continue", async () => {
    setRun(INTERRUPTED);
    open();
    const line = await screen.findByTestId("session-interrupted");
    expect(line.textContent).toContain("Stopped when Iron Jarvis restarted");
    expect(within(line).getByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(within(line).getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    const chip = screen.getByTestId("session-interrupted-chip");
    expect(chip.textContent).toBe("Interrupted");
    expect(screen.queryByText("Failed")).toBeNull();
  });

  it("Continue resumes the run's own conversation and the page follows the new run", async () => {
    setRun(INTERRUPTED);
    api.postResponses[`/sessions/${ID}/continue`] = { id: "session_next" };
    api.responses["/sessions?limit=10"] = {
      sessions: [
        // Another run in another folder, newer: not it.
        { id: "session_other", task: CONTINUE_AFTER_RESTART, workspace_path: "C:/elsewhere", created_at: new Date().toISOString() },
        { id: "session_next", task: `${CONTINUE_AFTER_RESTART}\n\n[Continuing an earlier session.]`, workspace_path: WS, created_at: new Date().toISOString() },
        { id: ID, task: "Summarise", workspace_path: WS, created_at: "2026-10-18T09:00:00" },
      ],
    };
    open();
    const line = await screen.findByTestId("session-interrupted");
    await act(async () => {
      fireEvent.click(within(line).getByRole("button", { name: "Continue" }));
    });
    const post = api.posts.find((p) => p.path === `/sessions/${ID}/continue`);
    expect(post?.body).toEqual({ message: CONTINUE_AFTER_RESTART, wait: false, resume: true });
    await waitFor(() => expect(H.push).toHaveBeenCalledWith("/sessions/session_next"));
  });

  it("Dismiss starts nothing and the page stays (nothing to follow)", async () => {
    setRun(INTERRUPTED);
    api.responses["/sessions?limit=10"] = { sessions: [{ id: ID, task: "Summarise", workspace_path: WS, created_at: "2026-10-18T09:00:00" }] };
    open();
    const line = await screen.findByTestId("session-interrupted");
    await act(async () => {
      fireEvent.click(within(line).getByRole("button", { name: "Dismiss" }));
    });
    expect(api.posts.some((p) => p.path === `/sessions/${ID}/interrupted/dismiss`)).toBe(true);
    await waitFor(() => expect(api.gets).toContain("/sessions?limit=10"));
    expect(H.push).not.toHaveBeenCalled();
  });

  it("a follow-up typed in the box resumes the conversation too", async () => {
    setRun(INTERRUPTED);
    api.postResponses[`/sessions/${ID}/continue`] = { id: "session_next" };
    open();
    const box = await screen.findByPlaceholderText(/Send a follow-up/);
    fireEvent.change(box, { target: { value: "and add the totals" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    });
    const post = api.posts.find((p) => p.path === `/sessions/${ID}/continue`);
    expect(post?.body).toEqual({ message: "and add the totals", wait: false, resume: true });
    await waitFor(() => expect(H.push).toHaveBeenCalledWith("/sessions/session_next"));
  });

  it("CONTROL: an ordinary failed run is 'Failed', offers no restart Continue, and a follow-up does not resume", async () => {
    setRun({ status: "failed", summary: "it broke" });
    api.postResponses[`/sessions/${ID}/continue`] = { id: "session_next" };
    open();
    await screen.findByTestId("session-summary");
    expect(screen.queryByTestId("session-interrupted")).toBeNull();
    expect(screen.queryByTestId("session-interrupted-chip")).toBeNull();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    const box = screen.getByPlaceholderText(/Send a follow-up/);
    fireEvent.change(box, { target: { value: "try again" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    });
    const post = api.posts.find((p) => p.path === `/sessions/${ID}/continue`);
    expect(post?.body).toEqual({ message: "try again", wait: false });
  });
});
