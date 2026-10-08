/**
 * UX wave 2, track T3 — automation pages: words & empty states (v1.314.0).
 *
 * WHAT THE USER SAW (scratch screenshots fresh__/desk__/tall__, verified):
 *  - Reflexes, Sentinels, Webhooks and Schedules opened EMPTY on a fresh
 *    install to one grey line pointing at a header button ("No reflexes yet —
 *    add one…", "No schedules yet."). The page explanation lives in the (i)
 *    popover since v1.214.1 (the user's call), so the empty state is the ONLY
 *    place a new user learns what a reflex/sentinel/webhook IS. The shared
 *    <Empty title action examples secondary> contract (components/ui.tsx) now
 *    takes an onClick action; each page must use it to open ITS OWN form.
 *  - Sentinels' banner spoke config keys ("flips `sentinels_enabled`"),
 *    "no-op" and "arms", with two bright primary buttons competing.
 *  - Webhooks led with `POST http://…/webhooks/{slug}` and nothing else.
 *  - Machine names: raw agent ids in the Schedules/Templates pickers, next-run
 *    timestamps to the second, and a notification destination named "mock"
 *    labelled "Built-in — always available" that delivers NOTHING.
 *  - Autonomy: "Run a tick", "the daemon can key a grant", "P3", "Dial", and
 *    the morning briefing in a monospace <pre> like raw program output.
 *  - The Browser (computer-use) page's safety settings in engineer words
 *    (DOM/a11y, screenshot_click, "in sync with the daemon",
 *    "human-in-the-loop").
 *  - Retired vocabulary: "Channels" links/labels (the page is Notifications,
 *    a row is a destination), "plug-in" (now extension).
 *  - Four page grids that can widen a phone (the wave-1 PageGrid carry-over).
 *
 * WHAT MUST NOT CHANGE (the anti-vacuity controls below): every header Add
 * button; the empty-state action opens the SAME form state the header button
 * sets (never a second copy, never a toggle that closes an open form); the
 * Enable-sentinels write still goes through `sentinels_enabled`; Check now
 * still POSTs /sentinels/poll and /autonomy/tick; option VALUES and API
 * payloads (agent ids, priority numbers, "mock" destination name, action
 * kinds) are unchanged; the raw value that is a RECORD stays reachable in a
 * title; the "Test only" disclosure on the demo destination is still there;
 * suggest-only stays disclosed; the approval copy keeps its qualifier
 * (read-only browser actions DO run without approval).
 *
 * Honesty pins (never weaken): the sentinel interval is 300 s by default
 * (config.py sentinels_tick_seconds), not 30 s; sentinels SUGGEST, execution
 * still flows through the autonomy dial, so no "never acts on its own";
 * priority 5 = highest (motivation/models.py:48, engine orders desc); the
 * loopback note shows only when the address IS loopback (API_BASE can be a
 * deployed daemon).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/* -------------------------------------------------------------------------- */
/*  Harness                                                                    */
/* -------------------------------------------------------------------------- */

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
    FakeApiError,
    /** API_BASE, switchable per test (read through a getter). */
    base: "http://127.0.0.1:8797",
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get API_BASE() {
    return api.base;
  },
  ijToken: () => "",
  wsUrl: (p: string) => p,
  sseUrl: (p: string) => p,
  flattenDetail: (d: unknown) => String(d),
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  api: async () => ({}),
  get: async (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r instanceof api.FakeApiError) throw r;
    return r ?? {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    return r ?? { ok: true };
  },
  put: async (path: string, body?: unknown) => {
    api.puts.push({ path, body });
    return {};
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
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
// The subtitle lives in the (i) popover — the mock renders title + actions,
// so "visible text" below means what the page body shows.
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1 data-testid="page-title">{title}</h1>
      {actions}
    </div>
  ),
}));

import ReflexPage from "@/app/reflex/page";
import SentinelsPage from "@/app/sentinels/page";
import WebhooksPage from "@/app/webhooks/page";
import SchedulesPage from "@/app/schedules/page";
import TemplatesPage from "@/app/templates/page";
import AutonomyPage from "@/app/autonomy/page";
import ChannelsPage from "@/app/channels/page";
import BrowserPage from "@/app/computeruse/page";
import { StandingGrants } from "@/components/StandingGrants";

const writeText = vi.fn(async (_t: string) => {});

beforeAll(() => {
  // jsdom has no scrollIntoView; a focus helper that scrolls must not crash
  // the test (the page should still guard with `?.`).
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function () {} as typeof Element.prototype.scrollIntoView;
  }
});

beforeEach(() => {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});

afterEach(() => {
  cleanup();
  api.base = "http://127.0.0.1:8797";
  api.gets = [];
  api.posts = [];
  api.puts = [];
  api.responses = {};
  api.postResponses = {};
  writeText.mockClear();
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

/** Source text, CRLF-normalised (this checkout is autocrlf). */
const src = (rel: string): string =>
  readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");
/** Source with block, JSX and line comments removed: what can reach a user. */
const code = (rel: string): string =>
  src(rel)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");

/** The one empty state on screen (the shared <Empty> carries this test id). */
async function findEmpty(): Promise<HTMLElement> {
  const all = await screen.findAllByTestId("empty-state");
  return all[0];
}

/** An <Empty> that TEACHES: a title (what this is) + examples. */
function expectTeaching(empty: HTMLElement) {
  // The shared Empty renders `title` as the first .font-medium child.
  const title = empty.querySelector(":scope > .font-medium");
  expect(title, "empty state needs a title saying what this place is").not.toBeNull();
  expect((title?.textContent ?? "").trim().length).toBeGreaterThan(3);
  const examples = within(empty).queryByTestId("empty-examples");
  expect(examples, "empty state needs examples of what goes here").not.toBeNull();
  expect(examples!.querySelectorAll("li").length).toBeGreaterThanOrEqual(1);
}

/** The empty state's own action: a <button> (onClick) inside the empty card. */
function emptyButton(empty: HTMLElement): HTMLButtonElement {
  const buttons = within(empty).getAllByRole("button");
  expect(buttons.length).toBe(1);
  return buttons[0] as HTMLButtonElement;
}

/* ========================================================================== */
/*  A. Empty states teach and open the page's OWN form                         */
/* ========================================================================== */

function seedReflex(rules: unknown[] = []) {
  api.responses["/reflex/rules"] = { rules };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/agents/remote"] = { agents: [] };
  api.responses["/webhooks"] = { webhooks: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/triggers"] = {};
}

function seedSentinels(sentinels: unknown[] = [], enabled = true) {
  api.responses["/sentinels"] = { enabled, sentinels };
  api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
}

const SENTINEL = {
  id: "sentinel_1",
  name: "intake",
  kind: "file",
  config: { path: "E:\\intake" },
  task: "triage new intake scans",
  agent_type: "builder",
  risk: "low",
  enabled: true,
  last_checked_at: null,
  last_error: null,
  created_at: "2026-09-01T10:00:00Z",
};

function seedSchedules(schedules: unknown[] = [], dynamic: { name: string }[] = []) {
  api.responses["/schedules"] = { schedules };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/comm/channels"] = { channels: [] };
  api.responses["/agents"] = { builtin: ["builder", "researcher"], dynamic };
}

describe("A1 Reflexes: the empty state teaches and opens the add form", () => {
  it("has a title + examples, drops the old one-liner, and claims nothing about acting alone", async () => {
    seedReflex();
    render(<ReflexPage />);
    const empty = await findEmpty();
    expectTeaching(empty);
    expect(empty.textContent).not.toContain("No reflexes yet — add one to make Iron Jarvis act on its own.");
    expect(empty.textContent).not.toMatch(/act on its own/i);
  });

  it("its button opens the SAME add form the header button opens", async () => {
    seedReflex();
    render(<ReflexPage />);
    const empty = await findEmpty();
    expect(screen.queryByLabelText("Signal source")).toBeNull();
    fireEvent.click(emptyButton(empty));
    expect(await screen.findByLabelText("Signal source")).toBeInTheDocument();
    // Anti-vacuity: the header door is still there.
    expect(screen.getAllByRole("button", { name: /add reflex/i }).length).toBeGreaterThanOrEqual(1);
  });

  it("pressing it while the form is already open keeps ONE form open (opens, never toggles)", async () => {
    seedReflex();
    render(<ReflexPage />);
    const empty = await findEmpty();
    // Open through the header first (the first /add reflex/ button is the header's).
    fireEvent.click(screen.getAllByRole("button", { name: /add reflex/i })[0]);
    await screen.findByLabelText("Signal source");
    fireEvent.click(emptyButton(empty));
    expect(screen.getAllByLabelText("Signal source")).toHaveLength(1);
  });
});

describe("A2 Sentinels: the empty state teaches (suggests, for approval) and opens the add form", () => {
  it("has a title + examples, says it SUGGESTS for your approval, never 'never acts'", async () => {
    seedSentinels([], true);
    render(<SentinelsPage />);
    const empty = await findEmpty();
    expectTeaching(empty);
    expect(empty.textContent).toMatch(/suggest/i);
    expect(empty.textContent).toMatch(/approv/i);
    expect(empty.textContent).not.toMatch(/never acts/i);
    expect(empty.textContent).not.toContain("No sentinels yet — use “Add sentinel”");
  });

  it("its button opens the existing add form, and only one", async () => {
    seedSentinels([], true);
    render(<SentinelsPage />);
    const empty = await findEmpty();
    expect(screen.queryByPlaceholderText("downloads-watch")).toBeNull();
    fireEvent.click(emptyButton(empty));
    expect(await screen.findByPlaceholderText("downloads-watch")).toBeInTheDocument();
    fireEvent.click(emptyButton(empty));
    expect(screen.getAllByPlaceholderText("downloads-watch")).toHaveLength(1);
  });

  it("no sentinel copy claims an absolute 'never acts on its own' (execution flows through the dial)", () => {
    expect(code("app/sentinels/page.tsx")).not.toMatch(/never acts on its own/i);
  });
});

describe("A3 Webhooks: the empty state teaches, opens the form, and points most people to Reflexes", () => {
  it("has a title + examples and a quieter link to /reflex", async () => {
    api.responses["/webhooks"] = { webhooks: [] };
    render(<WebhooksPage />);
    const empty = await findEmpty();
    expectTeaching(empty);
    const toReflex = within(empty)
      .getAllByRole("link")
      .filter((a) => a.getAttribute("href") === "/reflex");
    expect(toReflex.length).toBe(1);
    expect(empty.textContent).not.toContain("No webhooks registered");
  });

  it("its button opens the existing add form (once), and the header button is still the only /add webhook/ press", async () => {
    api.responses["/webhooks"] = { webhooks: [] };
    render(<WebhooksPage />);
    const empty = await findEmpty();
    // Compatibility with webhooks-manage-v1292 (getByRole /add webhook/i on an
    // EMPTY list): the empty action must not ALSO be named "Add webhook".
    expect(screen.getAllByRole("button", { name: /add webhook/i })).toHaveLength(1);
    expect(screen.queryByPlaceholderText("github-push")).toBeNull();
    fireEvent.click(emptyButton(empty));
    expect(await screen.findByPlaceholderText("github-push")).toBeInTheDocument();
    fireEvent.click(emptyButton(empty));
    expect(screen.getAllByPlaceholderText("github-push")).toHaveLength(1);
  });
});

describe("A4 Schedules: the empty state teaches and takes you INTO the add form", () => {
  it("has a title + examples instead of 'No schedules yet.'", async () => {
    seedSchedules([]);
    render(<SchedulesPage />);
    const empty = await findEmpty();
    expectTeaching(empty);
    expect(empty.textContent?.trim()).not.toBe("No schedules yet.");
  });

  it("its button moves focus into the existing add form; a preset named on it is really applied", async () => {
    seedSchedules([]);
    render(<SchedulesPage />);
    const empty = await findEmpty();
    const btn = emptyButton(empty);
    const label = btn.textContent ?? "";
    const task = (await screen.findByLabelText("Task text")) as HTMLTextAreaElement;
    const form = task.closest("form") as HTMLFormElement;
    fireEvent.click(btn);
    await waitFor(() => expect(form.contains(document.activeElement)).toBe(true));
    // Copy stays TRUE: if the action names a starter, that starter is applied.
    const PRESETS: [RegExp, RegExp][] = [
      [/morning briefing/i, /morning briefing/i],
      [/friday digest/i, /Friday digest/],
      [/tidy downloads/i, /Downloads folder/],
    ];
    for (const [named, taskWords] of PRESETS) {
      if (named.test(label)) expect(task.value).toMatch(taskWords);
    }
    // Anti-vacuity: still ONE add form on the page.
    expect(screen.getAllByLabelText("Task text")).toHaveLength(1);
  });
});

/* ========================================================================== */
/*  B. Sentinels banner in plain words, one primary button                     */
/* ========================================================================== */

describe("B Sentinels: the off-banner speaks plainly and has ONE primary", () => {
  it("renders no config key, no 'no-op', no 'arms', and no false 30-second cadence", async () => {
    seedSentinels([SENTINEL], false);
    render(<SentinelsPage />);
    const enable = await screen.findByRole("button", { name: /enable sentinels/i });
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/no-op/i);
    expect(text).not.toContain("sentinels_enabled");
    expect(text).not.toMatch(/\barms\b/i);
    expect(text).not.toMatch(/every 30 seconds|about every 30|every ~?30 ?s/i);
    // Anti-vacuity: the enable flow and the proposals link are still here.
    expect(enable).toBeInTheDocument();
    expect(
      screen.getAllByRole("link").some((a) => a.getAttribute("href") === "/autonomy"),
    ).toBe(true);
  });

  it("while off, exactly one btn-accent sits above the Watchers card (Enable is THE primary)", async () => {
    seedSentinels([SENTINEL], false);
    render(<SentinelsPage />);
    await screen.findByRole("button", { name: /enable sentinels/i });
    const watchers = screen.getByText(/^Watchers/);
    const above = Array.from(document.querySelectorAll(".btn-accent")).filter(
      (b) => b.compareDocumentPosition(watchers) & Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(above).toHaveLength(1);
    expect(above[0].textContent).toMatch(/enable sentinels/i);
    // Anti-vacuity: Add sentinel is still a button in the header.
    expect(screen.getByRole("button", { name: /add sentinel/i })).toBeInTheDocument();
  });

  it("Enable still writes sentinels_enabled=true through /settings (the wire is unchanged)", async () => {
    seedSentinels([SENTINEL], false);
    render(<SentinelsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /enable sentinels/i }));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual({ path: "/settings", body: { values: { sentinels_enabled: true } } });
  });

  it("'Poll now' is 'Check now': hinted while off, still clickable, still POSTs, no 'no-op' reply", async () => {
    seedSentinels([SENTINEL], false);
    api.postResponses["/sentinels/poll"] = { ran: false, reason: "sentinels_disabled", proposals: [] };
    render(<SentinelsPage />);
    await screen.findByRole("button", { name: /enable sentinels/i });
    expect(screen.queryByRole("button", { name: /poll now/i })).toBeNull();
    const check = screen.getByRole("button", { name: /check now/i });
    expect(check.getAttribute("title") ?? "").toMatch(/turn sentinels on first/i);
    expect((check as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(check);
    await waitFor(() => expect(api.posts.map((p) => p.path)).toContain("/sentinels/poll"));
    // Wait for the REPLY itself (a status note that is not the busy spinner),
    // never for a word the banner already shows.
    const reply = await waitFor(() => {
      const notes = screen
        .queryAllByRole("status")
        .filter((n) => n.tagName !== "SPAN" && (n.textContent ?? "").trim().length > 0);
      expect(notes.length).toBeGreaterThan(0);
      return notes[notes.length - 1];
    });
    expect(reply.textContent).not.toMatch(/no-op/i);
    expect(document.body.textContent).not.toMatch(/no-op/i);
  });

  it("source: no rendered 'no-op' or false 30-second cadence anywhere on the page", () => {
    const s = code("app/sentinels/page.tsx");
    expect(s).not.toMatch(/no-op/);
    expect(s).not.toMatch(/every 30 seconds|about every 30/i);
    expect(s).not.toContain("Poll now");
    // The wire key itself is still written (the enable flow).
    expect(s).toContain("sentinels_enabled: true");
  });
});

/* ========================================================================== */
/*  C. Webhooks: what it's for, a copyable address, plain labels               */
/* ========================================================================== */

const INBOUND = { slug: "gh", direction: "inbound", target_url: null, event_types_json: "[]", enabled: true };

describe("C Webhooks: leads with what it is for", () => {
  it("leads with other apps, not an HTTP verb; the address has a Copy button that copies it", async () => {
    api.responses["/webhooks"] = { webhooks: [INBOUND] };
    render(<WebhooksPage />);
    await screen.findByText("gh");
    const body = document.body.textContent ?? "";
    expect(body).not.toContain("Inbound webhooks accept events at");
    expect(body).toMatch(/other apps?/i);
    const copy = screen.getAllByRole("button", { name: /copy/i });
    expect(copy.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(copy[0]);
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(String(writeText.mock.calls[0][0])).toMatch(/^http:\/\/127\.0\.0\.1:8797\/webhooks\//);
  });

  it("says 'only this computer' when the address is loopback…", async () => {
    api.base = "http://127.0.0.1:8797";
    api.responses["/webhooks"] = { webhooks: [INBOUND] };
    render(<WebhooksPage />);
    await screen.findByText("gh");
    expect(document.body.textContent).toMatch(/Only programs on this computer can reach this address/);
  });

  it("…and NOT when the daemon is deployed elsewhere (copy stays true)", async () => {
    api.base = "https://jarvis.example.com";
    api.responses["/webhooks"] = { webhooks: [INBOUND] };
    render(<WebhooksPage />);
    await screen.findByText("gh");
    expect(document.body.textContent).not.toMatch(/Only programs on this computer/);
    // The real address is still shown.
    expect(document.body.textContent).toContain("https://jarvis.example.com/webhooks/");
  });

  it("the form speaks plainly; the test hooks (placeholder, Direction label, values) survive", async () => {
    api.responses["/webhooks"] = { webhooks: [] };
    render(<WebhooksPage />);
    fireEvent.click(screen.getAllByRole("button", { name: /add webhook/i })[0]);
    expect(await screen.findByPlaceholderText("github-push")).toBeInTheDocument();
    const labels = Array.from(document.querySelectorAll("label")).map((l) => (l.textContent ?? "").trim());
    expect(labels).not.toContain("Slug");
    const dir = screen.getByLabelText("Direction") as HTMLSelectElement;
    const opts = Array.from(dir.options);
    expect(opts.map((o) => o.value)).toEqual(["inbound", "outbound"]);
    for (const o of opts) expect(o.textContent).toMatch(/another app/i);
    // The free-text event box stays VISIBLE for outbound (webhooks-manage-v1292).
    fireEvent.change(dir, { target: { value: "outbound" } });
    expect(screen.getByPlaceholderText("session.completed, workflow.completed")).toBeInTheDocument();
  });

  it("the list is 'yours', not 'Registrations', and a populated page still points to Reflexes", async () => {
    api.responses["/webhooks"] = { webhooks: [INBOUND] };
    render(<WebhooksPage />);
    await screen.findByText("gh");
    expect(document.body.textContent).not.toMatch(/Registrations/);
    expect(
      screen.getAllByRole("link").some((a) => a.getAttribute("href") === "/reflex"),
    ).toBe(true);
    // Anti-vacuity: Remove is still the two-press control on the row.
    expect(screen.getByTitle("Remove webhook gh")).toBeInTheDocument();
  });
});

/* ========================================================================== */
/*  D. Machine names: friendly first, raw kept where it is a record            */
/* ========================================================================== */

describe("D1 Schedules: 'Who runs it' reads friendly names, values stay ids", () => {
  it("built-in options read 'Builder…'/'Researcher…' with value builder/researcher; a custom agent keeps its name", async () => {
    seedSchedules([], [{ name: "remy" }]);
    render(<SchedulesPage />);
    const picker = (await screen.findByLabelText("Agent")) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(picker.options).map((o) => o.value)).toContain("remy"),
    );
    const byValue = (v: string) => Array.from(picker.options).find((o) => o.value === v)!;
    expect(byValue("builder").textContent).toMatch(/^Builder/);
    expect(byValue("researcher").textContent).toMatch(/^Researcher/);
    expect(byValue("remy").textContent).toMatch(/remy/);
    expect(picker.value).toBe("builder");
  });

  it("the custom-agent helper shows only when a custom agent is picked", async () => {
    seedSchedules([], [{ name: "remy" }]);
    render(<SchedulesPage />);
    const picker = (await screen.findByLabelText("Agent")) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(picker.options).map((o) => o.value)).toContain("remy"),
    );
    expect(document.body.textContent).not.toMatch(/Custom agents fire with their own prompt and tools/);
    fireEvent.change(picker, { target: { value: "remy" } });
    expect(document.body.textContent).toMatch(/own prompt and tools/);
  });
});

describe("D2 Schedules: next run is short and human, the full value is in a title", () => {
  it("renders no seconds-precision timestamp; the exact time is reachable in a title", async () => {
    seedSchedules([
      {
        name: "nightly",
        cron: "0 2 * * *",
        kind: "task",
        enabled: true,
        next_run: "2026-10-08T15:00:00Z",
        last_run: null,
        trigger_type: "cron",
        payload_json: JSON.stringify({ task: "Nightly rounds." }),
      },
    ]);
    render(<SchedulesPage />);
    const row = (await screen.findByText("nightly")).closest("tr") as HTMLElement;
    expect(row.textContent).not.toMatch(/\d{1,2}:\d{2}:\d{2}/);
    const titled = Array.from(row.querySelectorAll("[title]")).map((e) => e.getAttribute("title") ?? "");
    expect(titled.some((t) => /2026/.test(t))).toBe(true);
  });

  it("source: next_run is no longer printed with toLocaleString()", () => {
    expect(code("app/schedules/page.tsx")).not.toMatch(/next_run\)\.toLocaleString\(\)/);
  });
});

describe("D3 Templates: the agent picker reads friendly names, values stay ids", () => {
  it("option text 'Builder…' for value builder", async () => {
    api.responses["/templates"] = { templates: [] };
    api.responses["/agents"] = { builtin: ["builder", "planner"], dynamic: [] };
    api.responses["/models"] = { models: [] };
    render(<TemplatesPage />);
    const picker = (await screen.findByLabelText("Agent type")) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(picker.options).map((o) => o.value)).toEqual(["builder", "planner"]));
    const builder = Array.from(picker.options).find((o) => o.value === "builder")!;
    expect(builder.textContent).toMatch(/^Builder/);
    expect(Array.from(picker.options).find((o) => o.value === "planner")!.textContent).toMatch(/^Planner/);
  });
});

const MOCK_DEST = {
  name: "mock",
  type: "mock",
  builtin: true,
  last_test_ok: null,
  last_test_at: null,
  events: [],
  inbound_enabled: false,
  chat_enabled: false,
  allowed_senders_count: 0,
  last_poll_error: null,
  last_poll_error_at: null,
};
const THIS_PC = { ...MOCK_DEST, name: "this-pc", type: "desktop" };
const TG = {
  ...MOCK_DEST,
  name: "tg",
  type: "telegram",
  builtin: false,
  last_test_ok: true,
  last_test_at: "2026-09-02T10:00:00Z",
};

function seedChannels(channels: unknown[]) {
  api.responses["/comm/channels"] = { channels };
  api.responses["/comm/channel-types"] = { types: [] };
}

/** The destination rows: the <li>s of the first list under the Destinations title. */
function destinationRows(): HTMLElement[] {
  let el: HTMLElement | null = screen.getByText(/^Destinations/);
  while (el && !el.querySelector("ul")) el = el.parentElement;
  const ul = el?.querySelector("ul");
  expect(ul, "the Destinations card lists its rows").toBeTruthy();
  return Array.from(ul!.children) as HTMLElement[];
}

describe("D4 Notifications: the demo destination is an honest 'Test log', listed last", () => {
  it("reads 'Test log (in-app only)' with a 'Test only' badge and 'nothing is sent'; raw name in a title", async () => {
    seedChannels([MOCK_DEST, THIS_PC, TG]);
    render(<ChannelsPage />);
    await screen.findAllByText("tg");
    const rows = destinationRows();
    const mockRow = rows.find((r) => /Test log/.test(r.textContent ?? ""));
    expect(mockRow, "the mock destination shows as 'Test log'").toBeTruthy();
    expect(mockRow!.textContent).toContain("Test log (in-app only)");
    expect(mockRow!.textContent).toMatch(/Test only/);
    expect(mockRow!.textContent).toMatch(/nothing is (sent|delivered)/i);
    expect(mockRow!.textContent).not.toMatch(/Built-in — always available/);
    // The raw id is not prose any more, but it is still reachable.
    expect(mockRow!.textContent).not.toMatch(/\bmock\b/i);
    const titles = Array.from(mockRow!.querySelectorAll("[title]")).map((e) => e.getAttribute("title"));
    expect(titles.some((t) => /\bmock\b/.test(t ?? ""))).toBe(true);
  });

  it("lists the test log AFTER the real destinations", async () => {
    seedChannels([MOCK_DEST, THIS_PC, TG]);
    render(<ChannelsPage />);
    await screen.findAllByText("tg");
    const rows = destinationRows();
    expect(rows.length).toBe(3);
    expect(rows[rows.length - 1].textContent).toMatch(/Test log/);
    expect(rows[0].textContent).not.toMatch(/Test log/);
  });
});

describe("D5 Notifications: the test-send picker says destination, and the value stays 'mock'", () => {
  it("label Destination, 'All destinations', mock option reads Test log; sending to it still posts 'mock'", async () => {
    seedChannels([MOCK_DEST, TG]);
    api.postResponses["/comm/notify"] = { mock: { ok: true, detail: "recorded" } };
    render(<ChannelsPage />);
    await screen.findAllByText("tg");
    const pick = screen.getByLabelText("Destination") as HTMLSelectElement;
    const opts = Array.from(pick.options);
    expect(opts[0].value).toBe("");
    expect(opts[0].textContent).toBe("All destinations");
    const mockOpt = opts.find((o) => o.value === "mock")!;
    expect(mockOpt.textContent).toBe("Test log (in-app only)");
    fireEvent.change(screen.getByPlaceholderText("Hello from Iron Jarvis…"), { target: { value: "hi" } });
    fireEvent.change(pick, { target: { value: "mock" } });
    const send = screen.getAllByRole("button", { name: /^send$/i })[0];
    fireEvent.click(send);
    await waitFor(() => expect(api.posts.map((p) => p.path)).toContain("/comm/notify"));
    expect(api.posts.find((p) => p.path === "/comm/notify")!.body).toEqual({ message: "hi", channels: ["mock"] });
    // The result row names it the same way (raw only in a title).
    const result = (await screen.findByText(/^Result$/)).parentElement as HTMLElement;
    expect(result.textContent).toMatch(/Test log/);
    expect(result.textContent).not.toMatch(/\bmock\b/);
  });
});

/* ========================================================================== */
/*  E. Autonomy in plain words                                                 */
/* ========================================================================== */

function primeAutonomy(briefingText = "Iron Jarvis — morning briefing\n- Active goals: 0\n- Pending proposals: 2") {
  api.responses["/autonomy"] = {
    enabled: false,
    level: "suggest",
    dry_run: false,
    kill_switch: false,
    tick_seconds: 900,
    max_actions_per_day: 20,
    max_tokens_per_day: 200000,
    used_actions_24h: 0,
    used_tokens_24h: 0,
    active_goals: 0,
    pending_proposals: 0,
  };
  api.responses["/autonomy/goals"] = { goals: [] };
  api.responses["/proposals?status=pending"] = { proposals: [] };
  api.responses["/autonomy/briefing"] = {
    text: briefingText,
    active_goals: 0,
    recent_actions: 0,
    pending_proposals: 2,
    pushed: null,
  };
  api.responses["/goals"] = { goals: [] };
  api.responses["/goals/digest?hours=24"] = { digest: { goals: [], since: "2026-10-06T00:00:00Z" } };
  api.responses["/grants?live=1"] = { grants: [] };
  api.postResponses["/autonomy/tick"] = { proposal_id: null };
  api.postResponses["/autonomy/briefing"] = { text: briefingText, pushed: null };
}

describe("E1 Autonomy: 'Run a tick' is 'Check now' and still deliberates", () => {
  it("no 'Run a tick'; 'Check now' POSTs /autonomy/tick", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    const check = await screen.findByRole("button", { name: /check now/i });
    expect(screen.queryByRole("button", { name: /run a tick/i })).toBeNull();
    await waitFor(() => expect((check as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(check);
    await waitFor(() => expect(api.posts.map((p) => p.path)).toContain("/autonomy/tick"));
  });
});

describe("E2 Autonomy: the morning briefing reads like a note, not program output", () => {
  it("renders through Markdown: list items, no <pre>", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    const item = await screen.findByText("Active goals: 0");
    expect(item.closest("li")).not.toBeNull();
    expect(item.closest("pre")).toBeNull();
    expect(screen.getByText("Pending proposals: 2").closest("li")).not.toBeNull();
  });

  it("source: the briefing text is not wrapped in a <pre>", () => {
    expect(code("app/autonomy/page.tsx")).not.toMatch(/<pre[^>]*>\s*\{briefing\.data\.text\}/);
  });
});

describe("E3 Autonomy: 'Send briefing' speaks destinations and still pushes", () => {
  it("button text and tooltip never say 'channels'; it POSTs /autonomy/briefing", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    const btn = await screen.findByRole("button", { name: /send briefing/i });
    expect(btn.textContent).not.toMatch(/channels/i);
    expect(btn.getAttribute("title") ?? "").not.toMatch(/channels/i);
    fireEvent.click(btn);
    await waitFor(() => expect(api.posts.map((p) => p.path)).toContain("/autonomy/briefing"));
  });
});

describe("E4 Autonomy: starter goals in plain words, priority mapped the RIGHT way round", () => {
  it("the intro drops tick/pulse/PROPOSE; meta reads 'normal'/'low priority', never 'P3'; suggest-only stays said", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    // Scope: the smallest ancestor of the card title that holds the three
    // starters' Add buttons (the page around it has other words).
    let card: HTMLElement | null = await screen.findByText("Starter goals");
    while (card && within(card).queryAllByRole("button", { name: "Add" }).length < 3) card = card.parentElement;
    expect(card, "the Starter goals card with its three Add buttons").not.toBeNull();
    expect(card).not.toBe(document.body);
    const all = card!.textContent ?? "";
    expect(all).not.toMatch(/\btick\b/);
    expect(all).not.toMatch(/\bpulse\b/);
    expect(all).not.toContain("PROPOSE");
    expect(all).not.toMatch(/\bP\d\b/);
    // priority 3 (two starters) = normal, priority 2 (usage recap) = LOW.
    // (motivation/models.py:48: 1 = low … 5 = high — an inverted map is a lie.)
    expect(all).toMatch(/normal priority/i);
    expect(all).toMatch(/low priority/i);
    expect(all).not.toMatch(/high priority/i);
    expect(all).toMatch(/suggest/i);
  });

  it("adding a starter still sends the numeric priority and suggest level", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    await screen.findByText("Starter goals");
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
    await waitFor(() => expect(api.posts.map((p) => p.path)).toContain("/autonomy/goals"));
    const body = api.posts.find((p) => p.path === "/autonomy/goals")!.body as Record<string, unknown>;
    expect(body.priority).toBe(3);
    expect(body.autonomy_level).toBe("suggest");
  });
});

describe("E5 Autonomy: the New goal form — 'How much freedom', and P5 is the HIGHEST", () => {
  it("no bare 'Dial' label; priority options name highest (5) and lowest (1); values stay 1..5", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    const pri = (await screen.findByLabelText("Priority")) as HTMLSelectElement;
    const labels = Array.from(document.querySelectorAll("label")).map((l) => (l.textContent ?? "").trim());
    expect(labels).not.toContain("Dial");
    const opts = Array.from(pri.options);
    expect(opts.map((o) => o.value).sort()).toEqual(["1", "2", "3", "4", "5"]);
    expect(opts.find((o) => o.value === "5")!.textContent).toMatch(/highest/i);
    expect(opts.find((o) => o.value === "1")!.textContent).toMatch(/lowest/i);
    expect(opts.find((o) => o.value === "1")!.textContent).not.toMatch(/highest/i);
    // Anti-vacuity: the dial select is still there with its three levels.
    const dial = screen.getByLabelText("Autonomy dial") as HTMLSelectElement;
    expect(Array.from(dial.options).map((o) => o.value)).toEqual(["suggest", "act_low", "act_all"]);
  });
});

describe("E6 Standing grants: the empty line says where grants come from, in plain words", () => {
  it("names 'Always allow exactly this'; no daemon / 'key a grant' / 'None standing'", async () => {
    api.responses["/grants?live=1"] = { grants: [] };
    render(<StandingGrants />);
    const box = await screen.findByTestId("standing-grants");
    await waitFor(() => expect(api.gets).toContain("/grants?live=1"));
    await waitFor(() => expect(box.textContent).not.toMatch(/None standing/));
    expect(box.textContent).not.toMatch(/daemon/i);
    expect(box.textContent).not.toMatch(/key a grant/i);
    expect(box.textContent).toMatch(/Always allow exactly this/);
  });
});

/* ========================================================================== */
/*  F. Browser (computer-use) page in plain words                              */
/* ========================================================================== */

const KINDS = ["navigate", "read", "extract", "screenshot", "wait", "click", "type", "screenshot_click"];

function primeBrowser(over: Record<string, unknown> = {}) {
  api.responses["/browser/status"] = {
    connected: false,
    access: "off",
    host_permission: false,
    extension_id: "x",
    active_tab: null,
    pending_pairing: null,
    paired: false,
    last_error: null,
  };
  api.responses["/computeruse"] = {
    enabled: false,
    domain_allowlist: ["example.com"],
    action_allowlist: ["navigate", "read"],
    isolation: "isolated",
    max_steps: 20,
    max_retries: 2,
    pending_approvals: 0,
    ...over,
  };
  api.responses["/computeruse/approvals"] = { approvals: [] };
  api.responses["/computeruse/runs?limit=20"] = { runs: [] };
  api.responses["/health"] = { status: "ok", providers: [], default_provider: "mock" };
}

/** Page text with the Your-browser card cut out (its own words are pinned elsewhere). */
function pageTextWithoutBrowserCard(): string {
  const clone = document.body.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("[data-testid='your-browser-card']").forEach((n) => n.remove());
  return clone.textContent ?? "";
}

async function toggles(): Promise<HTMLElement[]> {
  await waitFor(() => expect(document.querySelectorAll("button[data-kind]").length).toBe(8));
  return Array.from(document.querySelectorAll("button[data-kind]")) as HTMLElement[];
}

describe("F Browser page: safety settings a non-engineer can judge", () => {
  it("visible text has no DOM / a11y / screenshot_click / daemon / human-in-the-loop", async () => {
    primeBrowser();
    render(<BrowserPage />);
    await toggles();
    const text = pageTextWithoutBrowserCard();
    expect(text).not.toMatch(/\bDOM\b/);
    expect(text).not.toMatch(/a11y/);
    expect(text).not.toContain("screenshot_click");
    expect(text).not.toMatch(/daemon/i);
    expect(text).not.toMatch(/human-in-the-loop/i);
  });

  it("each of the 8 toggles leads with a plain label and keeps its kind id in data-kind + title", async () => {
    primeBrowser();
    render(<BrowserPage />);
    const rows = await toggles();
    expect(rows.map((r) => r.getAttribute("data-kind"))).toEqual(KINDS);
    for (const r of rows) {
      const kind = r.getAttribute("data-kind")!;
      expect(r.getAttribute("title") ?? "", `${kind} keeps its id in a title`).toContain(kind);
      expect((r.textContent ?? "").trimStart().startsWith(kind), `${kind} leads with a plain label`).toBe(false);
    }
  });

  it("toggling still edits the allowlist by the ORIGINAL kind, and Apply sends it", async () => {
    primeBrowser();
    render(<BrowserPage />);
    const rows = await toggles();
    const sc = rows.find((r) => r.getAttribute("data-kind") === "screenshot_click")!;
    expect(document.body.textContent).not.toMatch(/unsaved/i);
    fireEvent.click(sc);
    await waitFor(() => expect(document.body.textContent).toMatch(/unsaved/i));
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));
    await waitFor(() => expect(api.posts.map((p) => p.path)).toContain("/computeruse/enable"));
    const body = api.posts.find((p) => p.path === "/computeruse/enable")!.body as { action_allowlist: string[] };
    expect([...body.action_allowlist].sort()).toEqual(["navigate", "read", "screenshot_click"]);
  });

  it("chips speak plainly; isolation is built from the REAL value (raw value still reachable)", async () => {
    primeBrowser({ isolation: "shared-profile" });
    render(<BrowserPage />);
    await toggles();
    // No raw config-key chip labels.
    const exact = (s: string) =>
      Array.from(document.querySelectorAll("*")).some(
        (e) => e.children.length === 0 && (e.textContent ?? "").trim().toLowerCase() === s,
      );
    expect(exact("isolation")).toBe(false);
    expect(exact("max retries")).toBe(false);
    // An unknown mode is shown as itself — never dressed up as a sandbox.
    const text = pageTextWithoutBrowserCard();
    const titles = Array.from(document.querySelectorAll("[title]")).map((e) => e.getAttribute("title") ?? "");
    expect(text.includes("shared-profile") || titles.some((t) => t.includes("shared-profile"))).toBe(true);
    expect(text).not.toMatch(/isolated sandbox/i);
  });

  it("the known 'isolated' value keeps its raw word reachable", async () => {
    primeBrowser({ isolation: "isolated" });
    render(<BrowserPage />);
    await toggles();
    const text = pageTextWithoutBrowserCard();
    const titles = Array.from(document.querySelectorAll("[title]")).map((e) => e.getAttribute("title") ?? "");
    expect(/\bisolated\b/.test(text) || titles.some((t) => /\bisolated\b/.test(t))).toBe(true);
  });

  it("the approval copy keeps its qualifier — read-only actions DO run without approval", async () => {
    primeBrowser();
    render(<BrowserPage />);
    await toggles();
    const queue = screen.getByText("Approval queue").closest("div")!.parentElement as HTMLElement;
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/sensitive or destructive/i);
    expect(text).not.toMatch(/Nothing runs until you approve/i);
    expect(queue).toBeTruthy();
  });
});

/* ========================================================================== */
/*  G. Retired vocabulary on these pages (source pins, comments stripped)      */
/* ========================================================================== */

describe("G Vocabulary: Notifications/destination, extension — on the automation pages too", () => {
  it("Reflexes links say Notifications (target unchanged), never 'Channels'", () => {
    const s = code("app/reflex/page.tsx");
    expect(s).not.toMatch(/^\s*Channels\b/m);
    expect(s).not.toMatch(/Channels page/);
    expect(s).not.toMatch(/\bEmail channel\b/i);
    expect(s).toContain('href="/channels"'); // the wire/route is untouched
    expect(s).toMatch(/Notifications/);
  });

  it("Autonomy never sends 'to channels'", () => {
    const s = code("app/autonomy/page.tsx");
    expect(s).not.toMatch(/to channels|connected channels/i);
  });

  it("the Notifications test picker says destination; Slack's own 'channel' is still allowed", () => {
    const s = code("app/channels/page.tsx");
    expect(s).not.toMatch(/All channels/);
    expect(s).not.toMatch(/^\s*Channel\s*$/m);
    expect(s).not.toContain('aria-label="Channel"');
    expect(s).not.toMatch(/No channel responses/);
    // Exemption (VOCABULARY.md "Allowed exceptions"): Slack's channel is Slack's word.
    expect(s).toContain("a bot token + a channel");
    expect(s).toContain("/comm/channels");
  });

  it("no automation page says plug-in", () => {
    for (const f of [
      "app/templates/page.tsx",
      "app/reflex/page.tsx",
      "app/autonomy/page.tsx",
      "app/channels/page.tsx",
    ]) {
      expect(code(f), f).not.toMatch(/Plug-ins?|plug-ins?/);
    }
  });

  it("vocabulary.test.ts now scans these pages (the canon's own ratchet)", () => {
    const v = src("__tests__/vocabulary.test.ts");
    for (const f of ["app/templates/page.tsx", "app/reflex/page.tsx", "app/autonomy/page.tsx", "app/channels/page.tsx"]) {
      expect(v, `vocabulary.test.ts should scan ${f}`).toContain(`"${f}"`);
    }
  });
});

/* ========================================================================== */
/*  H. PageGrid carry-over (a phone never widens)                              */
/* ========================================================================== */

const tokens = (cls: string): string[] => cls.split(/\s+/).filter(Boolean);
function classStrings(text: string): string[] {
  const out: string[] = [];
  const re = /className=(?:"([^"]*)"|\{`([^`]*)`\})/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) out.push(m[1] ?? m[2] ?? "");
  return out;
}
/** Same detector as ux-wave1-phone-v1313: a gap grid with lg cols and no base track / child guard. */
function isBlowoutGrid(cls: string): boolean {
  const t = tokens(cls);
  if (!t.includes("grid")) return false;
  if (!t.some((x) => /^gap-/.test(x))) return false;
  if (!t.some((x) => /^lg:grid-cols-\d+$/.test(x))) return false;
  const hasBase = t.some((x) => /^grid-cols-(\d+|\[minmax\(0,1fr\)\])$/.test(x));
  return !hasBase && !t.includes("[&>*]:min-w-0");
}

describe("H PageGrid carry-over on templates / autonomy / channels / computeruse", () => {
  // [file, desktop column count (unchanged), lg:col-span count as of v1.313.0]
  const GRIDS: [string, 2 | 3, number][] = [
    ["app/templates/page.tsx", 3, 2],
    ["app/autonomy/page.tsx", 2, 0],
    ["app/channels/page.tsx", 2, 0],
    ["app/computeruse/page.tsx", 2, 0],
  ];

  it("the detector is real (anti-vacuity)", () => {
    expect(isBlowoutGrid("grid gap-6 lg:grid-cols-3")).toBe(true);
    expect(isBlowoutGrid("grid gap-4 sm:grid-cols-2 lg:grid-cols-4")).toBe(true);
    expect(isBlowoutGrid("grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4")).toBe(false);
  });

  it.each(GRIDS)("%s lays out with <PageGrid cols={%i}>, no blowout grid, same desktop spans", (rel, cols, spans) => {
    const text = src(rel);
    expect(text).toMatch(/import\s*\{\s*PageGrid\s*\}\s*from\s*"@\/components\/PageGrid"/);
    expect((text.match(new RegExp(`<PageGrid\\s+cols=\\{${cols}\\}`, "g")) ?? []).length).toBe(1);
    expect(classStrings(text).filter(isBlowoutGrid)).toEqual([]);
    expect((text.match(/lg:col-span-\d/g) ?? []).length).toBe(spans);
  });

  it("the wave-1 ratchet (T4_FILES) now covers these pages plus updates and self-dev", () => {
    const t = src("__tests__/ux-wave1-phone-v1313.test.tsx");
    const list = /const T4_FILES = \[([\s\S]*?)\];/.exec(t)?.[1] ?? "";
    for (const f of [
      "app/templates/page.tsx",
      "app/autonomy/page.tsx",
      "app/channels/page.tsx",
      "app/computeruse/page.tsx",
      "app/updates/page.tsx",
      "app/self-dev/page.tsx",
    ]) {
      expect(list, `T4_FILES should list ${f}`).toContain(`"${f}"`);
    }
  });
});

/* ========================================================================== */
/*  Review-fix pins (v1.314.0)                                                 */
/* ========================================================================== */

describe("Review fix: webhook examples are TRUE for a loopback install", () => {
  it("a loopback daemon never lists GitHub or a hosted web form as a sender", async () => {
    api.base = "http://127.0.0.1:8797";
    api.responses["/webhooks"] = { webhooks: [] };
    render(<WebhooksPage />);
    const empty = await findEmpty();
    const t = empty.textContent ?? "";
    expect(t).not.toMatch(/GitHub/);
    expect(t).not.toMatch(/web form/i);
    // An inbound webhook never starts a task by itself — a reflex does.
    expect(t).not.toMatch(/starts a task/i);
    expect(t).toMatch(/this PC/);
    expect(t).toMatch(/Zapier/); // outbound sends to any URL: true everywhere
  });

  it("…a deployed daemon may name GitHub (anti-vacuity: the switch is real)", async () => {
    api.base = "https://jarvis.example.com";
    api.responses["/webhooks"] = { webhooks: [] };
    render(<WebhooksPage />);
    const empty = await findEmpty();
    expect(empty.textContent).toMatch(/GitHub/);
  });

  it("no form or header line claims an inbound webhook starts work (a reflex decides)", () => {
    // v1.314.0 (review fix): routes/comm.py only publishes webhook.received and
    // fires reflexes bound to that slug — a webhook with no reflex starts
    // nothing, so the Direction option, the events hint and the subtitle must
    // not say it does (the empty state above already says a reflex decides).
    const s = code("app/webhooks/page.tsx");
    expect(s).not.toMatch(/starts? work here|start work in Iron Jarvis/);
    expect(s).toMatch(/a reflex decides what to do/);
  });
});

describe("Review fix: the briefing note names destinations in plain words", () => {
  it("pushed={mock:{ok:true}} reads 'Test log (in-app only)', says nothing left the app, keeps 'mock' in a title", async () => {
    primeAutonomy();
    api.postResponses["/autonomy/briefing"] = { text: "x", pushed: { mock: { ok: true } } };
    render(<AutonomyPage />);
    fireEvent.click(await screen.findByRole("button", { name: /send briefing/i }));
    const note = await screen.findByText(/Test log \(in-app only\)/);
    expect(note.textContent).toMatch(/nothing left this app/i);
    expect(note.textContent).not.toMatch(/\bmock\b/);
    expect(note.textContent).not.toMatch(/sent to/i);
    expect(note.closest("[title]")?.getAttribute("title")).toContain("mock");
  });

  it("a real destination plus the built-ins reads 'This PC', not 'this-pc' or 'mock'", async () => {
    primeAutonomy();
    api.postResponses["/autonomy/briefing"] = {
      text: "x",
      pushed: { "this-pc": { ok: true }, mock: { ok: true } },
    };
    render(<AutonomyPage />);
    fireEvent.click(await screen.findByRole("button", { name: /send briefing/i }));
    const note = await screen.findByText(/Briefing sent to/);
    expect(note.textContent).toContain("This PC");
    expect(note.textContent).not.toMatch(/this-pc|\bmock\b/);
    expect(note.closest("[title]")?.getAttribute("title")).toMatch(/this-pc.*mock|mock.*this-pc/);
  });
});

describe("Review fix: Browser action hints claim no more than the policy does", () => {
  it("no 'always ask' (personal details are a heuristic) and no 'list below' (the list is beside/above)", () => {
    const s = code("app/computeruse/page.tsx");
    expect(s).not.toMatch(/always ask you first/i);
    expect(s).not.toMatch(/on your list below/i);
  });
});
