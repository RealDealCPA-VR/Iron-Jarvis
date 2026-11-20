/**
 * Leap 06-a: rehearse a rule or a schedule, and see the list.
 *
 * - The report card says the daemon's sentence and lists every item it was
 *   given (from the ledger), marks a step that would ask first, lists what
 *   would be refused, and offers ONE main press: Keep it.
 * - While a rehearsal runs, the card reads it again until it ends.
 * - Rules: Rehearse replaces Test (one word); Run now moves to the row's
 *   menu and still fires the rule for real.
 * - Schedules: Rehearse on a task schedule; Run now in the row's menu.
 * - "rehearse it" in chat: the rehearsal card rides the reply's cards.
 * - The bell says nothing for a rehearsal's run; its run reads "Rehearsed".
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
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

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    return Promise.resolve(r === undefined ? {} : r);
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

import ReflexPage from "@/app/reflex/page";
import SchedulesPage from "@/app/schedules/page";
import { RehearsalReport, REHEARSAL_POLL_MS } from "@/components/RehearsalReport";
import { RowMenu, rowMenuPlace } from "@/components/RowMenu";
import { ConfigCards } from "@/components/chat/ConfigCards";
import { decodeConfigCards } from "@/lib/configCards";
import { toActivity, isRehearsalRun } from "@/components/NotificationBell";
import { statusWords } from "@/lib/statusWords";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  api.gets = [];
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
});

const READY = {
  id: "reh_1",
  subject: { kind: "schedule", id: "sched_1", name: "Sort the W2s" },
  status: "ready",
  sentence: "This would rename 2 files and send 1 message. Nothing was changed.",
  counts: { move: 2, send: 1 },
  would: [
    { kind: "move", words: "rename 2025_W2_smith.pdf to Smith - W2 2025.pdf", would: "run" },
    { kind: "move", words: "rename 2025_W2_jones.pdf to Jones - W2 2025.pdf", would: "run" },
    { kind: "send", words: "send a message to Anna", would: "ask" },
  ],
  refused: [{ kind: "write", words: "write notes.txt", would: "refuse", why: "denied by policy" }],
  reads: 4,
  guessed_after: false,
  error: "",
  sample: "",
  kept_at: null,
};

describe("the report card", () => {
  it("says the daemon's sentence, lists every item, and offers one main press", async () => {
    api.postResponses["/rehearsals/reh_1/keep"] = { ...READY, kept_at: "2026-11-20T10:00:00Z" };
    render(<RehearsalReport id="reh_1" initial={READY} />);
    const card = screen.getByTestId("rehearsal-report-reh_1");
    expect(within(card).getByTestId("rehearsal-sentence-reh_1").textContent).toBe(READY.sentence);
    const items = within(card).getByTestId("rehearsal-items-reh_1");
    expect(items.textContent).toContain("Would rename 2025_W2_smith.pdf to Smith - W2 2025.pdf");
    expect(items.textContent).toContain("Would rename 2025_W2_jones.pdf to Jones - W2 2025.pdf");
    expect(items.textContent).toContain("asks you first");
    expect(within(card).getByTestId("rehearsal-refused-reh_1").textContent).toContain("write notes.txt");
    // One main press (G4); everything else is a quiet text button.
    const main = within(card).getAllByRole("button").filter((b) => /btn-accent/.test(b.className));
    expect(main.map((b) => b.textContent)).toEqual(["Keep it"]);
    fireEvent.click(within(card).getByTestId("rehearsal-keep-reh_1"));
    await waitFor(() => expect(screen.getByTestId("rehearsal-kept-reh_1")).toBeInTheDocument());
    expect(api.posts.map((p) => p.path)).toEqual(["/rehearsals/reh_1/keep"]);
    expect(screen.queryByTestId("rehearsal-keep-reh_1")).toBeNull();
  });

  it("reads a running rehearsal again until it ends, then stops reading", async () => {
    vi.useFakeTimers();
    api.responses["/rehearsals/reh_1"] = READY;
    render(
      <RehearsalReport
        id="reh_1"
        initial={{ ...READY, status: "running", sentence: "Rehearsing. Nothing is being changed.", would: [] }}
      />,
    );
    expect(screen.getByText("Rehearsing. Nothing is being changed.")).toBeInTheDocument();
    expect(screen.getByTestId("rehearsal-stop-reh_1")).toBeInTheDocument();
    expect(api.gets.length).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REHEARSAL_POLL_MS);
    });
    expect(api.gets).toEqual(["/rehearsals/reh_1"]);
    expect(screen.getByTestId("rehearsal-sentence-reh_1").textContent).toBe(READY.sentence);
    // It ended: the clock runs on for many more turns and nothing is read.
    // (MUTATION: drop the `if (!running) return;` guard -> red.)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REHEARSAL_POLL_MS * 5);
    });
    expect(api.gets.length).toBe(1);
  });

  it("a rehearsal that could not run says why, and that nothing was changed", () => {
    render(
      <RehearsalReport
        id="reh_2"
        initial={{ ...READY, id: "reh_2", status: "failed", would: [], refused: [],
                   error: "This schedule runs a workflow, which cannot be rehearsed yet, so nothing ran." }}
      />,
    );
    const err = screen.getByTestId("rehearsal-error-reh_2");
    expect(err.textContent).toContain("cannot be rehearsed yet");
    expect(err.textContent).toContain("Nothing was changed.");
    expect(screen.queryByText("Keep it")).toBeNull();
  });
});

describe("Rules: Rehearse replaces Test, Run now moves to the row's menu", () => {
  function seed() {
    api.responses["/reflex/rules"] = {
      rules: [{
        id: "reflex_1", name: "on-mail", source: "webhook", match: "gh", action: "session",
        target: "", task_template: "", project_id: null, enabled: true,
        created_at: "2026-11-05T10:00:00Z", last_fired_at: null, fire_count: 0,
        last_error: null, last_result: null, rehearsed_at: "2026-11-19T09:00:00Z",
      }],
    };
    api.responses["/workflows"] = { workflows: [] };
    api.responses["/agents/remote"] = { agents: [] };
    api.responses["/webhooks"] = { webhooks: [] };
    api.responses["/projects"] = { projects: [] };
    api.responses["/triggers"] = {};
  }

  it("the row's press is Rehearse; it starts a rehearsal and shows its report under the row", async () => {
    seed();
    api.postResponses["/rehearsals"] = { ...READY, subject: { kind: "rule", id: "reflex_1", name: "on-mail" } };
    render(<ReflexPage />);
    const press = await screen.findByTestId("reflex-rehearse-reflex_1");
    expect(press.textContent).toContain("Rehearse");
    expect(screen.queryByRole("button", { name: /^\s*Test\s*$/ })).toBeNull();
    expect(screen.getByTestId("reflex-rehearsed-reflex_1").textContent).toMatch(/^Rehearsed /);
    fireEvent.click(press);
    await screen.findByTestId("rehearsal-report-reh_1");
    expect(api.posts).toEqual([{ path: "/rehearsals", body: { kind: "rule", id: "reflex_1" } }]);
    expect(api.posts.some((p) => p.path.endsWith("/test"))).toBe(false);
  });

  it("a rule that starts a workflow offers no Rehearse (it would only refuse)", async () => {
    seed();
    const rules = (api.responses["/reflex/rules"] as { rules: Record<string, unknown>[] }).rules;
    rules.push({ ...rules[0], id: "reflex_2", name: "triage", action: "workflow", target: "Triage" });
    render(<ReflexPage />);
    await screen.findByTestId("reflex-more-reflex_2");
    expect(screen.getByTestId("reflex-rehearse-reflex_1")).toBeInTheDocument();
    expect(screen.queryByTestId("reflex-rehearse-reflex_2")).toBeNull();
  });

  it("Run now, in the row's menu, still fires the rule for real", async () => {
    seed();
    render(<ReflexPage />);
    fireEvent.click(await screen.findByTestId("reflex-more-reflex_1"));
    fireEvent.click(await screen.findByTitle("Run this rule now, for real, with a test signal"));
    await waitFor(() => expect(api.posts.map((p) => p.path)).toEqual(["/reflex/rules/reflex_1/test"]));
  });
});

describe("the row's … menu stays on a 390 px screen", () => {
  it("opens on the side that has room: at the left of a wrapped phone row, never off the screen", () => {
    // Found in the browser at 390 px: a wrapped Rules row puts "…" at the
    // left, and the right-aligned menu started 70 px off the screen.
    expect(rowMenuPlace({ left: 46, right: 74, bottom: 450, width: 28 }, 390)).toEqual({
      top: 454, left: 46, maxWidth: 336,
    });
    // At the right of a desktop row it lines up with the button's right edge.
    expect(rowMenuPlace({ left: 1193, right: 1221, bottom: 300, width: 28 }, 1280)).toEqual({
      top: 304, right: 59, maxWidth: 1213,
    });
  });

  it("the drawn menu is placed by that rule", () => {
    const width = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      render(<RowMenu label="More" testId="row-more" items={[{ label: "Run now", title: "Run now", onSelect: () => {} }]} />);
      const press = screen.getByTestId("row-more");
      press.getBoundingClientRect = () =>
        ({ left: 46, right: 74, top: 420, bottom: 450, width: 28, height: 30, x: 46, y: 420, toJSON: () => ({}) }) as DOMRect;
      fireEvent.click(press);
      const menu = screen.getByRole("menu");
      expect(menu.style.left).toBe("46px");
      expect(menu.style.right).toBe("");
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    }
  });
});

describe("Schedules: Rehearse on a task schedule, Run now in the row's menu", () => {
  function mount(kind = "task") {
    api.responses["/schedules"] = {
      schedules: [{
        name: "nightly", cron: "0 2 * * *", kind, enabled: true, next_run: null, last_run: null,
        trigger_type: "cron", payload_json: JSON.stringify({ task: "Tidy." }), last_status: "",
        last_detail: "", last_session_id: "",
      }],
    };
    api.responses["/workflows"] = { workflows: [] };
    api.responses["/projects"] = { projects: [] };
    api.responses["/comm/channels"] = { channels: [] };
    api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
    return render(<SchedulesPage />);
  }

  it("Rehearse starts one and its report shows above the list", async () => {
    api.postResponses["/rehearsals"] = READY;
    mount();
    fireEvent.click(await screen.findByTestId("schedule-rehearse-nightly"));
    await screen.findByTestId("rehearsal-report-reh_1");
    expect(api.posts).toEqual([{ path: "/rehearsals", body: { kind: "schedule", id: "nightly" } }]);
    // Run now is in the row's menu, not on the row.
    expect(screen.queryByTitle("Run now")).toBeNull();
    fireEvent.click(screen.getByTestId("schedule-more-nightly"));
    expect(await screen.findByTitle("Run now")).toBeInTheDocument();
  });

  it("a schedule that is not a task offers no Rehearse (it would only refuse)", async () => {
    mount("workflow");
    await screen.findByTestId("schedule-more-nightly");
    expect(screen.queryByTestId("schedule-rehearse-nightly")).toBeNull();
  });
});

describe("chat, the bell and the words", () => {
  it("the reply's rehearsal card is kept by the decoder and drawn as the report", async () => {
    const cards = decodeConfigCards([
      { kind: "rehearsal", rehearsal_id: "reh_1", subject_kind: "schedule", name: "Sort the W2s" },
      { kind: "rehearsal", rehearsal_id: "../../etc", name: "bad" },
    ]);
    expect(cards).toEqual([{ kind: "rehearsal", rehearsal_id: "reh_1", subject_kind: "schedule", name: "Sort the W2s" }]);
    api.responses["/rehearsals/reh_1"] = READY;
    render(<ConfigCards cards={cards} onSettle={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("rehearsal-sentence-reh_1").textContent).toBe(READY.sentence),
    );
  });

  it("the bell says nothing for a rehearsal's run, finished, failed or stopped", () => {
    for (const payload of [
      { status: "completed", outcome: "rehearsed", origin: "schedule:nightly", rehearsal: true },
      { status: "failed", outcome: null, origin: "reflex:on-mail", rehearsal: true },
    ]) {
      expect(isRehearsalRun(payload)).toBe(true);
      expect(
        toActivity({ id: "e1", type: "session.completed", ts: "2026-11-20T10:00:00Z", session_id: "s1", payload }),
      ).toBeNull();
    }
    // Control: the same failed run, not a rehearsal, rings.
    expect(
      toActivity({
        id: "e2", type: "session.completed", ts: "2026-11-20T10:00:00Z", session_id: "s1",
        payload: { status: "failed", outcome: null, origin: "reflex:on-mail" },
      }),
    ).not.toBeNull();
  });

  it("a rehearsal's run reads Rehearsed, never Done", () => {
    expect(statusWords("completed", "rehearsed").word).toBe("Rehearsed");
  });
});
