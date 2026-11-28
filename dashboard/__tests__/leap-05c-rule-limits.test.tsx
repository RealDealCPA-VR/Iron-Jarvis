/**
 * Leap 05-c: a rule works hands-off inside the Limits approved once.
 *
 * - The rehearsal's card says what Keep it sets BEFORE it is pressed ("Keep
 *   it sets its limits: …", the daemon's words), and nothing once kept.
 * - The rule's row lists every OK kept on it under "Allowed without asking
 *   on this rule:" with Remove, and a run that stopped at its limits says
 *   "Needs you:" and why, linking the run.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
import { RehearsalReport, decodeRehearsal } from "@/components/RehearsalReport";

afterEach(() => {
  cleanup();
  api.gets = [];
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
});

const LIMITS_WORDS = "Writes only in Intake; anything outside stops it and it needs you. It may write files without asking.";

const READY = {
  id: "reh_9",
  subject: { kind: "rule", id: "reflex_9", name: "File it" },
  status: "ready",
  sentence: "This would write 1 file. Nothing was changed.",
  counts: { write: 1 },
  would: [{ kind: "write", words: "write a.txt", would: "ask" }],
  refused: [],
  reads: 0,
  guessed_after: false,
  error: "",
  sample: "a made-up signal",
  kept_at: null,
  limits: { folders: ["C:\\Intake"], words: LIMITS_WORDS },
};

describe("the rehearsal's card says what Keep it approves", () => {
  it("before Keep it: the limits it sets, in the daemon's words; after: nothing more", async () => {
    api.postResponses["/rehearsals/reh_9/keep"] = { ...READY, kept_at: "2026-11-27T10:00:00Z" };
    render(<RehearsalReport id="reh_9" initial={READY} />);
    const line = screen.getByTestId("rehearsal-limits-reh_9");
    expect(line.textContent).toBe(`Keep it sets its limits: ${LIMITS_WORDS}`);
    fireEvent.click(screen.getByTestId("rehearsal-keep-reh_9"));
    await waitFor(() => expect(screen.getByTestId("rehearsal-kept-reh_9")).toBeInTheDocument());
    expect(screen.queryByTestId("rehearsal-limits-reh_9")).toBeNull();
  });

  it("a schedule's report (no limits) and an offer that cannot be kept say none", () => {
    render(<RehearsalReport id="reh_9" initial={{ ...READY, limits: null }} />);
    expect(screen.queryByTestId("rehearsal-limits-reh_9")).toBeNull();
    cleanup();
    render(<RehearsalReport id="reh_9" initial={READY} keepable={false} />);
    expect(screen.queryByTestId("rehearsal-limits-reh_9")).toBeNull();
  });

  it("decodes only well-formed limits", () => {
    expect(decodeRehearsal(READY)?.limits).toEqual({ folders: ["C:\\Intake"], words: LIMITS_WORDS });
    expect(decodeRehearsal({ ...READY, limits: { words: "" } })?.limits).toBeNull();
    expect(decodeRehearsal({ ...READY, limits: "junk" })?.limits).toBeNull();
  });
});

describe("the rule's row: its OKs with Remove, and a run that stopped at its limits", () => {
  function seed(extra: Record<string, unknown> = {}) {
    api.responses["/reflex/rules"] = {
      rules: [{
        id: "reflex_9", name: "File it", source: "webhook", match: "file-it", action: "session",
        target: "", task_template: "File {text}", project_id: null, enabled: true,
        created_at: "2026-11-05T10:00:00Z", last_fired_at: null, fire_count: 3,
        last_error: null, last_result: null, rehearsed_at: "2026-11-27T09:00:00Z",
        when_words: "When the webhook “file-it” is called.", does_words: "Jarvis does: File {text}.",
        where_words: "In C:\\Intake.",
        limits_words: `${LIMITS_WORDS} Anything else asks you first. Starts at most 12 times an hour, then pauses and tells you.`,
        limits: { folders: ["C:\\Intake"], kept_at: "2026-11-27T09:00:00Z" },
        limits_stop: {
          words: "Stopped at its limits: it would have written C:\\Elsewhere\\x.txt, outside the folders it may write in (C:\\Intake). Nothing was written there.",
          href: "/sessions/session_3",
        },
        standing_grants: [{
          id: "grant_1", scope_kind: "rule", scope_id: "reflex_9", tool: "write_file", args_hash: "",
          exact: false, label: "Write files (kept after its rehearsal)", created_by: "rehearsal",
          created_at: "2026-11-27T09:00:00Z", expires_at: "2026-12-27T09:00:00Z", revoked_at: null,
          uses: 2, last_used_at: null, live: true,
        }],
        ...extra,
      }],
    };
    api.responses["/workflows"] = { workflows: [] };
    api.responses["/agents/remote"] = { agents: [] };
    api.responses["/webhooks"] = { webhooks: [] };
    api.responses["/projects"] = { projects: [] };
    api.responses["/triggers"] = {};
  }

  it("lists each kept OK with Remove, and Remove revokes it", async () => {
    seed();
    render(<ReflexPage />);
    const held = await screen.findByTestId("held-oks-File it");
    expect(held.textContent).toContain("Allowed without asking on this rule:");
    expect(held.textContent).toContain("Write files (kept after its rehearsal)");
    expect(screen.getByTestId("reflex-lines-reflex_9").textContent).toContain(LIMITS_WORDS);
    fireEvent.click(within(held).getByTestId("held-ok-remove-grant_1"));
    fireEvent.click(within(held).getByTestId("held-ok-remove-grant_1"));
    await waitFor(() => expect(api.posts.map((p) => p.path)).toContain("/grants/grant_1/revoke"));
  });

  it("a run that stopped at its limits says Needs you and why, linking the run", async () => {
    seed();
    render(<ReflexPage />);
    const stop = await screen.findByTestId("reflex-limits-stop-reflex_9");
    expect(stop.textContent).toBe(
      "Needs you: It would have written C:\\Elsewhere\\x.txt, outside the folders it may write in (C:\\Intake). Nothing was written there.",
    );
    expect(within(stop).getByRole("link").getAttribute("href")).toBe("/sessions/session_3");
  });

  it("a rule with no limits kept shows neither", async () => {
    seed({ limits: null, limits_stop: null, standing_grants: [] });
    render(<ReflexPage />);
    await screen.findByTestId("reflex-row-reflex_9");
    expect(screen.queryByTestId("reflex-limits-stop-reflex_9")).toBeNull();
    expect(screen.queryByTestId("held-oks-File it")).toBeNull();
  });
});
