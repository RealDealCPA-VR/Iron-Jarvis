/**
 * UX wave 4, track T2 — automation forms (v1.316.0).
 *
 * WHAT THE USER SAW (scratch screenshots fresh__/desk__/tall__, verified):
 *  - form-labels-not-associated: every form on Templates, Webhooks, Reflexes,
 *    Sentinels, Schedules and Notifications put a sibling <label>Text</label>
 *    above its control with no htmlFor/id — a screen reader hears "edit text"
 *    with no name, and clicking the label does not focus the field. The
 *    coordinator's <Field label hint?> (components/ui.tsx, useId) ties them;
 *    a hand-written htmlFor/id pair is equally fine. A label that names a
 *    GROUP (the ChooserTiles radiogroups, a read-only Badge) is not a <label>
 *    at all — the group keeps its own aria-label.
 *  - templates-own-items-buried: "Saved templates · 3" — the user's own
 *    one-click tasks — sat ~1180 px down, below a 10-card Starter library,
 *    and the starters already added showed twice. Saved rows prefixed the
 *    description with an italic "— " that read like a stray dash.
 *  - agent-names-raw-lowercase: built-in agents outside the workflow editor's
 *    five (guide, maintainer, memory, automation) read as raw lowercase ids
 *    in the Schedules / Templates / Sentinels pickers (an <option> is never
 *    CSS-capitalised) and on the rows; a CUSTOM agent's name was capitalised
 *    by the Badge's CSS. lib/agentWorlds.agentLabel(name, {builtin}) is the
 *    rule, with `builtin` from GET /agents' builtin list.
 *  - name-mismatch-across-pages: the literal "Channels →" / "Tools →
 *    Plug-ins" words were already retired in v1.314.0 (verified); what is
 *    left is (a) the vocabulary.test.ts ratchet does not yet scan Webhooks,
 *    Sentinels and Schedules (the verifier: extend vocabulary.test.ts), and
 *    (b) one word for one thing across Reflexes/Templates — a reflex's task
 *    text was called a "Task template" / "template:" while a template is the
 *    saved prompt on /templates.
 *  - carry-empty-add-scrolls-to-form (W2/W3 carry-over): the Reflexes and
 *    Sentinels empty-state Add opened the page's own form ABOVE the list but
 *    did not move there; on a phone a scrolled-down user saw nothing happen.
 *
 * WHAT MUST NOT CHANGE (anti-vacuity controls below): every existing
 * aria-label (tests use getByLabelText("Direction"), "Signal source",
 * "Task text", "Agent", "Agent type", "Watcher kind", "Risk", "Destination",
 * "Destination name", "Folder"…); option VALUES stay agent ids; the Use link
 * still carries the raw agent id; every starter and its Add button stay (Add
 * still POSTs /templates); Use / Edit / Delete stay on each saved row; the
 * header Add buttons on Reflexes/Sentinels still toggle exactly as before;
 * a reflex still posts `task_template` (the wire is unchanged).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/* -------------------------------------------------------------------------- */
/*  Harness (the ux-wave2-automation-v1314 pattern)                            */
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
    base: "http://127.0.0.1:8797",
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
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
    return { ok: true };
  },
  put: async () => ({}),
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
import ChannelsPage from "@/app/channels/page";
import { Field } from "@/components/ui";

/** Every element scrollIntoView was called on (jsdom has none of its own). */
const scrolled: Element[] = [];

beforeAll(() => {
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolled.push(this);
  } as typeof Element.prototype.scrollIntoView;
});

afterEach(() => {
  cleanup();
  api.base = "http://127.0.0.1:8797";
  api.gets = [];
  api.posts = [];
  api.responses = {};
  scrolled.length = 0;
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

/** Source text, CRLF-normalised (this checkout is autocrlf). */
const src = (rel: string): string =>
  readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

/* -------------------------------------------------------------------------- */
/*  The label detector                                                         */
/* -------------------------------------------------------------------------- */

const LABELABLE = /^(INPUT|SELECT|TEXTAREA|BUTTON|METER|OUTPUT|PROGRESS)$/;

/** Every <label> on screen whose text is NOT tied to a control — by htmlFor
 *  pointing at ONE labelable element, or by wrapping one. Returns their text. */
function untiedLabels(root: ParentNode = document): string[] {
  const bad: string[] = [];
  root.querySelectorAll("label").forEach((node) => {
    const label = node as HTMLLabelElement;
    const text = (label.textContent ?? "").replace(/\s+/g, " ").trim();
    if (label.htmlFor) {
      const same = Array.from(document.querySelectorAll("[id]")).filter(
        (e) => e.id === label.htmlFor,
      );
      if (same.length !== 1 || !LABELABLE.test(same[0].tagName)) bad.push(text);
      return;
    }
    if (!label.querySelector("input,select,textarea,button,meter,output,progress")) bad.push(text);
  });
  return bad;
}

/** The control a visible label text names, through the LABEL (not an
 *  aria-label): proves the click-to-focus link a sighted user gets. */
function controlOfLabel(text: string | RegExp): HTMLElement {
  const labels = Array.from(document.querySelectorAll("label")).filter((l) => {
    const t = (l.textContent ?? "").replace(/\s+/g, " ").trim();
    return typeof text === "string" ? t === text : text.test(t);
  }) as HTMLLabelElement[];
  expect(labels.length, `exactly one <label> reads ${String(text)}`).toBe(1);
  const label = labels[0];
  const el = label.htmlFor
    ? document.getElementById(label.htmlFor)
    : (label.querySelector("input,select,textarea") as HTMLElement | null);
  expect(el, `<label> ${String(text)} is tied to no control`).not.toBeNull();
  return el as HTMLElement;
}

describe("0 the detector is real (anti-vacuity)", () => {
  it("flags a bare sibling label and passes a <Field> and a wrapping label", () => {
    render(
      <div data-testid="bare">
        <label>Loose</label>
        <input />
      </div>,
    );
    expect(untiedLabels(screen.getByTestId("bare"))).toEqual(["Loose"]);
    cleanup();
    render(
      <div data-testid="tied">
        <Field label="Tied">
          <input />
        </Field>
        <label>
          <input type="checkbox" /> Wrapped
        </label>
      </div>,
    );
    expect(untiedLabels(screen.getByTestId("tied"))).toEqual([]);
  });
});

/* ========================================================================== */
/*  A. Every label names its control                                           */
/* ========================================================================== */

function seedTemplates(templates: unknown[] = [], starters: unknown[] = []) {
  api.responses["/templates"] = { templates };
  api.responses["/templates/starters"] = { starters };
  api.responses["/templates/suggestions"] = { suggestions: [] };
  api.responses["/agents"] = { builtin: ["builder", "planner", "guide"], dynamic: [{ name: "ledger-checker" }] };
  api.responses["/models"] = { models: [] };
}

function seedReflex(rules: unknown[] = []) {
  api.responses["/reflex/rules"] = { rules };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/agents/remote"] = { agents: [] };
  api.responses["/webhooks"] = { webhooks: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/triggers"] = {};
}

function seedSentinels(sentinels: unknown[] = [], builtin = ["builder", "maintainer"]) {
  api.responses["/sentinels"] = { enabled: true, sentinels };
  api.responses["/agents"] = { builtin, dynamic: [{ name: "ledger-checker" }] };
}

function seedSchedules(schedules: unknown[] = []) {
  api.responses["/schedules"] = { schedules };
  api.responses["/workflows"] = { workflows: [{ name: "nightly-close" }] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/comm/channels"] = { channels: [] };
  api.responses["/agents"] = { builtin: ["builder", "guide"], dynamic: [{ name: "remy" }] };
}

const TELEGRAM_TYPE = {
  type: "telegram",
  fields: [
    { key: "token", label: "Bot token", secret: true },
    { key: "chat_id", label: "Chat ID", secret: false },
    { key: "inbound_enabled", label: "Two-way", secret: false, type: "bool" },
  ],
};
const TG = {
  name: "tg",
  type: "telegram",
  builtin: false,
  last_test_ok: true,
  last_test_at: "2026-09-02T10:00:00Z",
  events: [],
  inbound_enabled: false,
  chat_enabled: false,
  allowed_senders_count: 0,
  last_poll_error: null,
  last_poll_error_at: null,
};

function seedChannels() {
  api.responses["/comm/channels"] = { channels: [TG] };
  api.responses["/comm/channel-types"] = { types: [TELEGRAM_TYPE] };
}

describe("A1 Templates: every label names its control", () => {
  it("Name / When to use it / Task are reachable by their label; the selects keep their aria-labels", async () => {
    seedTemplates();
    render(<TemplatesPage />);
    await screen.findByLabelText("Agent type");
    expect(controlOfLabel("Name").tagName).toBe("INPUT");
    expect(controlOfLabel(/^When to use it/)).toHaveAttribute("placeholder", "e.g. Use each morning to get oriented");
    expect(controlOfLabel("Task").tagName).toBe("TEXTAREA");
    expect(untiedLabels()).toEqual([]);
    // Anti-vacuity: the existing names survive.
    expect(screen.getByLabelText("Agent type")).toHaveAttribute("aria-label", "Agent type");
    expect(screen.getByLabelText("Model")).toHaveAttribute("aria-label", "Model");
  });
});

describe("A2 Webhooks: every label names its control (both directions)", () => {
  it("inbound: name, event types and secret are labelled; Direction keeps its aria-label", async () => {
    api.responses["/webhooks"] = { webhooks: [] };
    render(<WebhooksPage />);
    fireEvent.click(await screen.findByRole("button", { name: /add webhook/i }));
    await screen.findByPlaceholderText("github-push");
    expect(controlOfLabel(/^Name \(used in the address\)/)).toHaveAttribute("placeholder", "github-push");
    expect(controlOfLabel("Event types").tagName).toBe("INPUT");
    expect(controlOfLabel(/^Secret name/).tagName).toBe("INPUT");
    expect(untiedLabels()).toEqual([]);
    expect(screen.getByLabelText("Direction")).toHaveAttribute("aria-label", "Direction");
  });

  it("outbound: Target URL is labelled too", async () => {
    api.responses["/webhooks"] = { webhooks: [] };
    render(<WebhooksPage />);
    fireEvent.click(await screen.findByRole("button", { name: /add webhook/i }));
    fireEvent.change(await screen.findByLabelText("Direction"), { target: { value: "outbound" } });
    expect(controlOfLabel("Target URL")).toHaveAttribute("placeholder", "https://example.com/hook");
    expect(untiedLabels()).toEqual([]);
  });
});

describe("A3 Reflexes: the add form and the calendar trigger name their fields", () => {
  it("default form (webhook, no inbound webhooks yet) + calendar: every label tied", async () => {
    seedReflex();
    render(<ReflexPage />);
    fireEvent.click((await screen.findAllByRole("button", { name: /add reflex/i }))[0]);
    await screen.findByLabelText("Signal source");
    expect(controlOfLabel("Webhook slug")).toHaveAttribute("placeholder", "github-push");
    expect(controlOfLabel(/^Name \(optional\)/).tagName).toBe("INPUT");
    // The calendar trigger (visible in fresh__reflex.png).
    expect(controlOfLabel("iCal URL")).toHaveAttribute("type", "url");
    expect(controlOfLabel(/^Lead/)).toHaveAttribute("type", "number");
    expect(untiedLabels()).toEqual([]);
    // Anti-vacuity: the selects keep their names.
    expect(screen.getByLabelText("Signal source")).toHaveAttribute("aria-label", "Signal source");
    expect(screen.getByLabelText("Action")).toHaveAttribute("aria-label", "Action");
  });

  it("a session action reveals the task and project fields — tied too", async () => {
    seedReflex();
    render(<ReflexPage />);
    fireEvent.click((await screen.findAllByRole("button", { name: /add reflex/i }))[0]);
    fireEvent.change(await screen.findByLabelText("Action"), { target: { value: "session" } });
    const task = await screen.findByPlaceholderText("Triage this: {body}");
    expect(document.querySelector(`label[for="${task.id}"]`), "the task box has a label").not.toBeNull();
    expect(untiedLabels()).toEqual([]);
    expect(screen.getByLabelText("Project")).toHaveAttribute("aria-label", "Project");
  });
});

describe("A4 Sentinels: the add form names its fields", () => {
  it("Name / Path to watch / Glob / Suggested task tied; Watcher kind, Agent type, Risk keep aria-labels", async () => {
    seedSentinels();
    render(<SentinelsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /add sentinel/i }));
    await screen.findByPlaceholderText("downloads-watch");
    expect(controlOfLabel("Name")).toHaveAttribute("placeholder", "downloads-watch");
    expect(controlOfLabel(/Path to watch/).tagName).toBe("INPUT");
    expect(controlOfLabel(/^Glob/)).toHaveAttribute("placeholder", "*.pdf");
    expect(controlOfLabel(/^Suggested task/).tagName).toBe("INPUT");
    expect(untiedLabels()).toEqual([]);
    for (const n of ["Watcher kind", "Agent type", "Risk"]) {
      expect(screen.getByLabelText(n)).toHaveAttribute("aria-label", n);
    }
  });
});

describe("A5 Schedules: add form (every kind), the folded knobs and the editor", () => {
  it("task kind + knobs: every label tied; Task text / Agent / Folder keep their aria-labels", async () => {
    seedSchedules([]);
    render(<SchedulesPage />);
    await screen.findByLabelText("Task text");
    expect(controlOfLabel("The task")).toHaveAttribute("aria-label", "Task text");
    expect(controlOfLabel("Who runs it")).toHaveAttribute("aria-label", "Agent");
    expect(controlOfLabel("Name")).toHaveAttribute("placeholder", "morning-briefing");
    expect(controlOfLabel(/Repeat/)).toHaveAttribute("aria-label", "Repeat");
    expect(untiedLabels()).toEqual([]);
    // The knobs (folded, still in the DOM) keep their names.
    expect(screen.getByLabelText("Folder")).toHaveAttribute("aria-label", "Folder");
    expect(screen.getByLabelText("Skip memory")).toHaveAttribute("aria-label", "Skip memory");
    // The kind chooser is a named radiogroup, not a <label> pointing nowhere.
    expect(screen.getByRole("radiogroup", { name: "Schedule kind" })).toBeInTheDocument();
  });

  it("once / advanced repeat and the workflow + event kinds: still tied", async () => {
    seedSchedules([]);
    render(<SchedulesPage />);
    const repeat = (await screen.findByLabelText("Repeat")) as HTMLSelectElement;
    fireEvent.change(repeat, { target: { value: "__once__" } });
    expect(controlOfLabel(/Run at/)).toHaveAttribute("type", "datetime-local");
    expect(untiedLabels()).toEqual([]);
    fireEvent.change(repeat, { target: { value: "__advanced__" } });
    expect(controlOfLabel("Cron expression")).toHaveAttribute("placeholder", "0 9 * * *");
    expect(untiedLabels()).toEqual([]);
    fireEvent.click(screen.getByRole("radio", { name: /Run a saved workflow/ }));
    await screen.findByLabelText("Workflow to run");
    expect(untiedLabels()).toEqual([]);
    fireEvent.click(screen.getByRole("radio", { name: /Emit an event/ }));
    expect(controlOfLabel("Event type")).toHaveAttribute("aria-label", "Event type");
    expect(untiedLabels()).toEqual([]);
  });

  it("the edit dialog's labels are tied too", async () => {
    seedSchedules([
      {
        name: "nightly",
        cron: "0 2 * * *",
        kind: "task",
        enabled: true,
        next_run: null,
        last_run: null,
        trigger_type: "cron",
        payload_json: JSON.stringify({ task: "Nightly rounds." }),
        skills: [],
        workspace_root: "",
        context_from: "",
        script: null,
        skip_memory: false,
      },
    ]);
    render(<SchedulesPage />);
    fireEvent.click(await screen.findByTestId("schedule-edit-nightly"));
    const editor = await screen.findByTestId("schedule-editor");
    expect(untiedLabels(editor)).toEqual([]);
    expect(within(editor).getByLabelText("Schedule name")).toHaveAttribute("readonly");
    expect(within(editor).getByLabelText("Cron expression")).toHaveAttribute("aria-label", "Cron expression");
  });
});

describe("A6 Notifications: the add/edit form and the test send name their fields", () => {
  it("test send: Message is labelled; Destination keeps its aria-label", async () => {
    seedChannels();
    render(<ChannelsPage />);
    await screen.findByLabelText("Destination");
    expect(controlOfLabel("Message").tagName).toBe("TEXTAREA");
    expect(screen.getByLabelText("Destination")).toHaveAttribute("aria-label", "Destination");
  });

  it("adding a Telegram destination: every label tied (type tiles stay a named radiogroup)", async () => {
    seedChannels();
    render(<ChannelsPage />);
    fireEvent.click((await screen.findAllByRole("button", { name: /add destination/i }))[0]);
    fireEvent.click(await screen.findByRole("radio", { name: /Telegram/ }));
    await screen.findByLabelText("Destination name");
    expect(controlOfLabel("Name")).toHaveAttribute("aria-label", "Destination name");
    expect(untiedLabels()).toEqual([]);
    expect(screen.getByRole("radiogroup", { name: "Destination type" })).toBeInTheDocument();
  });

  it("editing a destination: the read-only Type is not a <label> pointing at nothing", async () => {
    seedChannels();
    render(<ChannelsPage />);
    fireEvent.click(await screen.findByTitle("Edit tg"));
    await screen.findByLabelText("Destination name");
    expect(untiedLabels()).toEqual([]);
    // Anti-vacuity: the type is still SHOWN while editing.
    expect(screen.getAllByText("Telegram").length).toBeGreaterThanOrEqual(1);
  });
});

/* ========================================================================== */
/*  B. Templates: the user's own templates first                               */
/* ========================================================================== */

const STARTERS = [
  {
    id: "s-inbox",
    name: "Inbox triage",
    task: "Check my inbox and sort it",
    description: "Use each morning",
    agent_type: "builder",
    requirements: [],
    ready: true,
    already_added: true,
  },
  {
    id: "s-week",
    name: "Weekly review",
    task: "Summarise the week's work",
    description: "Use on Fridays",
    agent_type: "planner",
    requirements: [],
    ready: true,
    already_added: false,
  },
  {
    id: "s-brief",
    name: "Client brief",
    task: "Draft a one-page client brief",
    description: "Use before a client call",
    agent_type: "builder",
    requirements: [],
    ready: true,
    already_added: false,
  },
];

const SAVED = [
  {
    id: "t1",
    name: "Inbox triage",
    agent_type: "builder",
    task: "Check my inbox and sort it",
    description: "Use each morning",
    created_at: "2026-10-07T10:00:00Z",
    requirements: [],
    ready: true,
  },
  {
    id: "t2",
    name: "Ledger sweep",
    agent_type: "ledger-checker",
    task: "Sweep the ledger for odd entries",
    description: "End of month",
    created_at: "2026-10-06T10:00:00Z",
    requirements: [],
    ready: true,
  },
  {
    id: "t3",
    name: "Docs pass",
    agent_type: "guide",
    task: "Tidy the help pages",
    description: "After a release",
    created_at: "2026-10-05T10:00:00Z",
    requirements: [],
    ready: true,
  },
];

/** A saved row: from its Edit button up to the element holding its description. */
function savedRow(name: string, description: string): HTMLElement {
  let el: HTMLElement | null = screen.getByTitle(`Edit template "${name}"`);
  while (el && !(el.textContent ?? "").includes(description)) el = el.parentElement;
  expect(el, `saved row for ${name}`).not.toBeNull();
  return el as HTMLElement;
}

/** The disclosure holding the starter library. */
function starterDetails(): HTMLDetailsElement {
  const details = screen.getByText("Weekly review").closest("details");
  expect(details, "the starter library sits in a <details> disclosure").not.toBeNull();
  return details as HTMLDetailsElement;
}

describe("B1 Templates: saved templates come before the starter library", () => {
  it("with saved templates: Saved first, starters folded under 'Browse starters (3)'", async () => {
    seedTemplates(SAVED, STARTERS);
    render(<TemplatesPage />);
    const saved = await screen.findByText(/^Saved templates · 3/);
    await screen.findByText("Weekly review");
    const details = starterDetails();
    expect(saved.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(details.open).toBe(false);
    const summary = details.querySelector("summary");
    expect(summary?.textContent ?? "").toMatch(/Browse starters/i);
    expect(summary?.textContent ?? "").toContain("(3)");
  });

  it("with no saved templates the library is open (a new user still sees it at once)", async () => {
    seedTemplates([], STARTERS);
    render(<TemplatesPage />);
    await screen.findByText("Weekly review");
    await waitFor(() => expect(api.gets).toContain("/templates"));
    await waitFor(() => expect(starterDetails().open).toBe(true));
  });

  it("starters already added sort to the end of the library", async () => {
    seedTemplates(SAVED, STARTERS);
    render(<TemplatesPage />);
    await screen.findByText("Weekly review");
    const details = starterDetails();
    const added = within(details).getByText("Inbox triage");
    for (const fresh of ["Weekly review", "Client brief"]) {
      const el = within(details).getByText(fresh);
      expect(el.compareDocumentPosition(added) & Node.DOCUMENT_POSITION_FOLLOWING, fresh).toBeTruthy();
    }
    // Anti-vacuity: the added one still says Added; nothing was dropped.
    expect(within(details).getByText("Added")).toBeInTheDocument();
    expect(within(details).getByText("Client brief")).toBeInTheDocument();
  });

  it("every starter's Add still works (it POSTs the starter, folded or not)", async () => {
    seedTemplates(SAVED, STARTERS);
    render(<TemplatesPage />);
    await screen.findByText("Weekly review");
    fireEvent.click(screen.getByTitle('Add "Weekly review" to your templates'));
    await waitFor(() => expect(api.posts.some((p) => p.path === "/templates")).toBe(true));
    expect(api.posts.find((p) => p.path === "/templates")!.body).toEqual({
      name: "Weekly review",
      task: "Summarise the week's work",
      agent_type: "planner",
      description: "Use on Fridays",
    });
    expect(screen.getByTitle('Add "Client brief" to your templates')).toBeInTheDocument();
  });

  it("a saved row's description has no stray leading dash and reads as 'When to use'", async () => {
    seedTemplates(SAVED, STARTERS);
    render(<TemplatesPage />);
    await screen.findByText("Ledger sweep");
    const row = savedRow("Ledger sweep", "End of month");
    expect(row.textContent ?? "").not.toMatch(/—\s*End of month/);
    expect(row.textContent ?? "").toMatch(/When to use/i);
    // Anti-vacuity: Use / Edit / Delete are all still on the row.
    const use = within(row).getByRole("link", { name: /use/i });
    expect(use.getAttribute("href")).toContain("agent=ledger-checker");
    expect(within(row).getByTitle('Edit template "Ledger sweep"')).toBeInTheDocument();
    expect(within(row).getByTitle('Delete template "Ledger sweep"')).toBeInTheDocument();
  });
});

/* ========================================================================== */
/*  C. Agent names: built-ins in words, custom names as typed, values = ids     */
/* ========================================================================== */

const optionText = (sel: HTMLSelectElement, value: string) =>
  Array.from(sel.options).find((o) => o.value === value)?.textContent ?? "";

describe("C1 Templates: the picker and the saved rows read agent names, values stay ids", () => {
  it("built-in guide reads 'Guide'; a custom agent keeps its typed name; values unchanged", async () => {
    seedTemplates(SAVED, []);
    render(<TemplatesPage />);
    const picker = (await screen.findByLabelText("Agent type")) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(picker.options).map((o) => o.value)).toEqual([
        "builder",
        "planner",
        "guide",
        "ledger-checker",
      ]),
    );
    expect(optionText(picker, "builder")).toMatch(/^Builder/);
    expect(optionText(picker, "guide")).toMatch(/^Guide/);
    expect(optionText(picker, "ledger-checker")).toMatch(/^ledger-checker/);
  });

  it("a saved row names a built-in in words, and never re-cases a custom agent", async () => {
    seedTemplates(SAVED, []);
    render(<TemplatesPage />);
    await screen.findByText("Docs pass");
    const docs = savedRow("Docs pass", "After a release");
    expect(within(docs).getByText("Guide")).toBeInTheDocument();
    // The Use link still carries the raw id (the value is unchanged).
    expect(within(docs).getByRole("link", { name: /use/i }).getAttribute("href")).toContain("agent=guide");
    const ledger = savedRow("Ledger sweep", "End of month");
    const chip = within(ledger).getByText("ledger-checker");
    expect(chip.closest(".capitalize"), "a custom name is shown as typed (no CSS capitalize)").toBeNull();
  });
});

describe("C2 Schedules: 'Who runs it' and the row read agent names, values stay ids", () => {
  const ROW = {
    name: "docs-nightly",
    cron: "0 2 * * *",
    kind: "task",
    enabled: true,
    next_run: null,
    last_run: null,
    trigger_type: "cron",
    payload_json: JSON.stringify({ task: "Tidy the help pages.", agent_type: "guide" }),
  };

  it("guide reads 'Guide' in the picker; remy stays 'remy'; values are ids", async () => {
    seedSchedules([]);
    render(<SchedulesPage />);
    const picker = (await screen.findByLabelText("Agent")) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(picker.options).map((o) => o.value)).toContain("remy"));
    expect(Array.from(picker.options).map((o) => o.value)).toEqual(["builder", "guide", "remy"]);
    expect(optionText(picker, "guide")).toMatch(/^Guide/);
    expect(optionText(picker, "builder")).toMatch(/^Builder/);
    expect(optionText(picker, "remy")).toMatch(/^remy/);
  });

  it("the row says 'Guide' and keeps the id in its title", async () => {
    seedSchedules([ROW]);
    render(<SchedulesPage />);
    const who = await screen.findByTestId("schedule-agent");
    await waitFor(() => expect(who.textContent ?? "").toContain("Guide"));
    expect(who).toHaveAttribute("title", "guide");
  });
});

describe("C3 Sentinels: the picker and the row read agent names, values stay ids", () => {
  const SENTINEL = {
    id: "sentinel_1",
    name: "intake",
    kind: "file",
    config: { path: "E:\\intake" },
    task: "triage new intake scans",
    agent_type: "maintainer",
    risk: "low",
    enabled: true,
    last_checked_at: null,
    last_error: null,
    created_at: "2026-09-01T10:00:00Z",
  };

  it("maintainer reads 'Maintainer' in the picker; a custom agent keeps its name", async () => {
    seedSentinels([]);
    render(<SentinelsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /add sentinel/i }));
    const picker = (await screen.findByLabelText("Agent type")) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(picker.options).map((o) => o.value)).toContain("ledger-checker"));
    expect(Array.from(picker.options).map((o) => o.value)).toEqual(["builder", "maintainer", "ledger-checker"]);
    expect(optionText(picker, "maintainer")).toMatch(/^Maintainer/);
    expect(optionText(picker, "ledger-checker")).toMatch(/^ledger-checker/);
  });

  it("the row reads '→ Maintainer', the id still reachable in a title", async () => {
    seedSentinels([SENTINEL]);
    render(<SentinelsPage />);
    const who = await screen.findByText(/→\s*Maintainer/);
    expect(who.closest("[title]")?.getAttribute("title") ?? "").toContain("maintainer");
  });
});

/* ========================================================================== */
/*  D. One name for one thing                                                  */
/* ========================================================================== */

describe("D1 Reflexes: a reflex's task text is a task, not a 'template'", () => {
  it("the form names it as a task; it still posts task_template (the wire is unchanged)", async () => {
    seedReflex();
    render(<ReflexPage />);
    fireEvent.click((await screen.findAllByRole("button", { name: /add reflex/i }))[0]);
    fireEvent.change(await screen.findByLabelText("Action"), { target: { value: "session" } });
    fireEvent.change(screen.getByPlaceholderText("github-push"), { target: { value: "gh" } });
    const task = (await screen.findByPlaceholderText("Triage this: {body}")) as HTMLTextAreaElement;
    const label = document.querySelector(`label[for="${task.id}"]`);
    expect(label, "the task box has a label").not.toBeNull();
    expect(label!.textContent ?? "").not.toMatch(/template/i);
    expect(label!.textContent ?? "").toMatch(/task/i);
    // The placeholder help still explains {body}/{text}/{slug}.
    expect(document.body.textContent).toContain("{slug}");
    fireEvent.change(task, { target: { value: "Triage this: {body}" } });
    fireEvent.submit(task.closest("form")!);
    await waitFor(() => expect(api.posts.some((p) => p.path === "/reflex/rules")).toBe(true));
    const body = api.posts.find((p) => p.path === "/reflex/rules")!.body as Record<string, unknown>;
    expect(body.task_template).toBe("Triage this: {body}");
    expect(body.action).toBe("session");
  });

  it("a row shows its task without a 'template:' prefix; the full text stays in a title", async () => {
    seedReflex([
      {
        id: "reflex_2",
        name: "on-message",
        source: "comm",
        match: "deploy",
        action: "session",
        target: "",
        task_template: "Look into: {text}",
        project_id: null,
        enabled: true,
        created_at: "2026-09-05T10:00:00Z",
        last_fired_at: null,
        fire_count: 0,
        last_error: null,
        last_result: null,
      },
    ]);
    render(<ReflexPage />);
    const shown = await screen.findByTitle("Look into: {text}");
    expect(shown.textContent ?? "").toContain("Look into: {text}");
    expect(shown.textContent ?? "").not.toMatch(/\btemplate:/i);
  });

  it("source: no user-visible 'Task template' on the reflex page", () => {
    const code = src("app/reflex/page.tsx")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toMatch(/^\s*Task template\s*$/m);
    expect(code).not.toMatch(/>\s*template:\s*\{/);
    expect(code).toContain("task_template"); // the wire field is untouched
  });
});

describe("D2 vocabulary.test.ts scans the rest of the automation pages (the verifier's ratchet)", () => {
  it("webhooks, sentinels and schedules are in its plug-in + destination scans", () => {
    const v = src("__tests__/vocabulary.test.ts");
    for (const f of ["app/webhooks/page.tsx", "app/sentinels/page.tsx", "app/schedules/page.tsx"]) {
      const hits = v.split(`"${f}"`).length - 1;
      expect(hits, `vocabulary.test.ts should scan ${f} for plug-in AND our 'channel' words`).toBeGreaterThanOrEqual(2);
    }
  });

  it("…and those pages are clean today (so the ratchet holds, not just exists)", () => {
    for (const f of ["app/webhooks/page.tsx", "app/sentinels/page.tsx", "app/schedules/page.tsx"]) {
      const code = src(f)
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "");
      expect(code, f).not.toMatch(/Plug-ins?|plug-ins?/);
      expect(code, f).not.toMatch(/Channels page|^\s*Channels\b/m);
      expect(code, f).not.toMatch(/\b(?:Email|Telegram|inbound|comm) channels?\b/i);
    }
  });
});

/* ========================================================================== */
/*  E. The empty state's Add takes you TO the form (as Schedules does)          */
/* ========================================================================== */

/** The empty state's own single button. */
async function emptyButton(): Promise<HTMLButtonElement> {
  const empty = (await screen.findAllByTestId("empty-state"))[0];
  const buttons = within(empty).getAllByRole("button");
  expect(buttons).toHaveLength(1);
  return buttons[0] as HTMLButtonElement;
}

function scrolledTo(form: HTMLElement): boolean {
  return scrolled.some((el) => form.contains(el) || el.contains(form));
}

describe("E1 Reflexes: 'Add your first reflex' scrolls to and focuses the opened form", () => {
  it("from a closed form: opens it, scrolls it into view, focus lands inside it", async () => {
    seedReflex();
    render(<ReflexPage />);
    fireEvent.click(await emptyButton());
    const form = (await screen.findByLabelText("Signal source")).closest("form") as HTMLElement;
    await waitFor(() => expect(form.contains(document.activeElement)).toBe(true));
    expect(scrolledTo(form)).toBe(true);
    expect(screen.getAllByLabelText("Signal source")).toHaveLength(1);
  });

  it("with the form already open (header first): the empty press still moves there", async () => {
    seedReflex();
    render(<ReflexPage />);
    const btn = await emptyButton();
    fireEvent.click(screen.getAllByRole("button", { name: /add reflex/i })[0]);
    const form = (await screen.findByLabelText("Signal source")).closest("form") as HTMLElement;
    (document.body as HTMLElement).focus();
    scrolled.length = 0;
    fireEvent.click(btn);
    await waitFor(() => expect(form.contains(document.activeElement)).toBe(true));
    expect(scrolledTo(form)).toBe(true);
    expect(screen.getAllByLabelText("Signal source")).toHaveLength(1);
  });

  it("the header Add is exactly as before: it toggles the form open and closed", async () => {
    seedReflex();
    render(<ReflexPage />);
    await emptyButton();
    const header = screen.getAllByRole("button", { name: /add reflex/i })[0];
    fireEvent.click(header);
    expect(await screen.findByLabelText("Signal source")).toBeInTheDocument();
    fireEvent.click(header);
    await waitFor(() => expect(screen.queryByLabelText("Signal source")).toBeNull());
  });
});

describe("E2 Sentinels: 'Watch a folder' scrolls to and focuses the opened form", () => {
  it("from a closed form: opens it, scrolls it into view, focus lands inside it", async () => {
    seedSentinels([]);
    render(<SentinelsPage />);
    fireEvent.click(await emptyButton());
    const form = (await screen.findByPlaceholderText("downloads-watch")).closest("form") as HTMLElement;
    await waitFor(() => expect(form.contains(document.activeElement)).toBe(true));
    expect(scrolledTo(form)).toBe(true);
    expect(screen.getAllByPlaceholderText("downloads-watch")).toHaveLength(1);
  });

  it("with the form already open: the empty press still moves there, and only one form", async () => {
    seedSentinels([]);
    render(<SentinelsPage />);
    const btn = await emptyButton();
    fireEvent.click(screen.getByRole("button", { name: /add sentinel/i }));
    const form = (await screen.findByPlaceholderText("downloads-watch")).closest("form") as HTMLElement;
    (document.body as HTMLElement).focus();
    scrolled.length = 0;
    fireEvent.click(btn);
    await waitFor(() => expect(form.contains(document.activeElement)).toBe(true));
    expect(scrolledTo(form)).toBe(true);
    expect(screen.getAllByPlaceholderText("downloads-watch")).toHaveLength(1);
  });

  it("the header Add sentinel is exactly as before: it toggles", async () => {
    seedSentinels([]);
    render(<SentinelsPage />);
    await emptyButton();
    const header = screen.getByRole("button", { name: /add sentinel/i });
    fireEvent.click(header);
    expect(await screen.findByPlaceholderText("downloads-watch")).toBeInTheDocument();
    fireEvent.click(header);
    await waitFor(() => expect(screen.queryByPlaceholderText("downloads-watch")).toBeNull());
  });
});
