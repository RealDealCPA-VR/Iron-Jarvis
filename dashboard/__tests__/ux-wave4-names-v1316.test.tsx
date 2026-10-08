/**
 * v1.316.0 — UX wave 4 ("automation, memory, connections, system"), track
 * T6 names. Findings pinned here (scratchpad ux_wave4.json; fix_adjustment
 * wins over proposed_fix):
 *
 *  - agent-names-raw-lowercase / carry-session-agent-select: an agent's name
 *    reads as a NAME wherever CSS `capitalize` cannot reach it — a <select>'s
 *    option text and a placeholder sentence. The shared rule is
 *    `lib/agentWorlds.agentLabel(name, {builtin, origin})`: a BUILT-IN id
 *    reads as a word ("builder" → "Builder", "file_manager" → "File
 *    manager"), a custom agent keeps the name the user typed, a mission
 *    coordinator is "Jarvis". Whether a name is built-in comes from the
 *    DAEMON (GET /agents `builtin`, a roster row's `kind`), never a list kept
 *    in the page — so a built-in this file invents ("tax_helper") must be
 *    worded too. Option VALUES stay the raw id (what a form posts, what a
 *    filter matches, what a deep link names); an avatar is still drawn from
 *    the RAW name.
 *  - carry-denied-row-words: a DENIED Time-travel tool row leads "Tried to
 *    save a file" (the toolWords phrase, lower-case after "Tried to") — it
 *    read "Save a file", as if it had happened. The raw-id chip and the red
 *    "denied" badge stay exactly as they are; an allowed row is unchanged.
 *
 * Anti-vacuity (every describe has one): the forms still POST the raw id
 * (/sessions agent_type, /agents/<custom>/spawn, /assignments assignee), the
 * filter still filters by the raw id, a deep-linked ?agent= still selects,
 * custom names are never re-cased, the roster's provenance words ride along,
 * the face is the face of the raw name, and the denied row keeps its badge,
 * its raw id and its ledger line.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------ */

const w4 = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    posts: [] as { path: string; body?: unknown }[],
    audit: { entries: [] as unknown[], next_cursor: null as string | null, total: 0 },
    search: "",
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: w4.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  sseUrl: (p: string) => p,
  get: (path: string) => {
    if (path.startsWith("/audit")) return Promise.resolve(w4.audit);
    if (path === "/tools") return Promise.resolve({ tools: [] });
    const r = w4.responses[path];
    return r === undefined
      ? Promise.reject(new w4.FakeApiError(`unmocked GET ${path}`, 404))
      : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    w4.posts.push({ path, body });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

// Path-keyed synchronous data (the kanban-teams-v1168 / ux-wave2 / ux-wave3 harness).
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (w4.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (w4.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({ enabled: false, supported: false, toggle: () => {}, speak: () => {}, stop: () => {} }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(w4.search),
  usePathname: () => "/sessions",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
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

import SessionsPage from "@/app/sessions/page";
import { NewSessionForm } from "@/components/NewSessionForm";
import { AgentInbox } from "@/components/agents/AgentInbox";
import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import AgentFace, { resolveFace } from "@/components/agents/AgentFace";
import { TimeTravelFeed } from "@/components/TimeTravelFeed";
import { toolWords } from "@/lib/toolWords";
import type { AuditEntry, SessionView } from "@/lib/types";
import { SetupCardHarness } from "./helpers/setupCardHarness";

/* ---- fixtures + helpers ----------------------------------------------------- */

/** The daemon's GET /agents: `tax_helper` is a built-in NO list in the
 *  dashboard knows — it must still read as a word (builtin comes from the
 *  daemon, never a hardcoded list). `ledger-checker` is a custom agent: its
 *  typed name is never re-cased. */
const AGENTS = {
  builtin: ["builder", "researcher", "file_manager", "tax_helper"],
  dynamic: [{ name: "remy" }, { name: "ledger-checker" }],
};

const optionsOf = (sel: HTMLSelectElement) =>
  Array.from(sel.options).map((o) => [o.value, (o.textContent ?? "").trim()]);

function sv(id: string, over: Partial<SessionView> = {}): SessionView {
  return {
    id,
    task: `task ${id}`,
    agent_type: "builder",
    provider: "mock",
    model: "mock-1",
    status: "completed",
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-10-01T10:00:00Z",
    finished_at: null,
    ...over,
  };
}

/** The first non-blank text a reader meets inside `root`. */
function firstText(root: Element): string {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = (n.textContent ?? "").trim();
    if (t) return t;
  }
  return "";
}

function titled(root: ParentNode, needle: string): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[title]")).filter((el) =>
    (el.getAttribute("title") ?? "").includes(needle),
  );
}

beforeEach(() => {
  w4.responses = {};
  w4.posts = [];
  w4.audit = { entries: [], next_cursor: null, total: 0 };
  w4.search = "";
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

/* ========================================================================== */
/*  New session — the "Agent type" select                                      */
/* ========================================================================== */

describe("New session: the Agent type picker reads names, posts ids", () => {
  function seed() {
    w4.responses["/agents"] = AGENTS;
    w4.responses["/health"] = {
      default_provider: "mock",
      default_model: "mock-1",
      providers: [{ provider: "mock", available: true, class: "mock" }],
    };
    w4.responses["/models"] = { models: [{ provider: "mock", model: "mock-1", available: true }] };
  }
  const picker = () => screen.getByLabelText("Agent type") as HTMLSelectElement;

  it("built-in ids read as words (from the daemon's builtin list); values stay the raw ids", () => {
    seed();
    render(<NewSessionForm />);
    expect(optionsOf(picker())).toEqual([
      ["builder", "Builder"],
      ["researcher", "Researcher"],
      ["file_manager", "File manager"],
      ["tax_helper", "Tax helper"],
      ["remy", "remy (custom)"],
      ["ledger-checker", "ledger-checker (custom)"],
    ]);
    // the default pick is still the raw builder id
    expect(picker().value).toBe("builder");
  });

  it("no option text is a bare lower-case built-in id", () => {
    seed();
    render(<NewSessionForm />);
    const texts = Array.from(picker().options).map((o) => (o.textContent ?? "").trim());
    for (const id of AGENTS.builtin) expect(texts).not.toContain(id);
  });

  it("anti-vacuity: a picked built-in POSTs /sessions with the RAW agent_type", async () => {
    seed();
    render(<NewSessionForm />);
    fireEvent.change(screen.getByPlaceholderText(/Describe what the agent should do/), {
      target: { value: "Sort the receipts" },
    });
    fireEvent.change(picker(), { target: { value: "file_manager" } });
    fireEvent.click(screen.getByRole("button", { name: /Run session/ }));
    await waitFor(() => expect(w4.posts.length).toBe(1));
    expect(w4.posts[0].path).toBe("/sessions");
    expect((w4.posts[0].body as Record<string, unknown>).agent_type).toBe("file_manager");
  });

  it("anti-vacuity: a picked CUSTOM agent still spawns through /agents/<name>/spawn", async () => {
    seed();
    render(<NewSessionForm />);
    fireEvent.change(screen.getByPlaceholderText(/Describe what the agent should do/), {
      target: { value: "Check the ledger" },
    });
    fireEvent.change(picker(), { target: { value: "ledger-checker" } });
    fireEvent.click(screen.getByRole("button", { name: /Run session/ }));
    await waitFor(() => expect(w4.posts.length).toBe(1));
    expect(w4.posts[0].path).toBe("/agents/ledger-checker/spawn");
  });

  it("anti-vacuity: a deep-linked ?agent= still selects by the raw id", () => {
    seed();
    w4.search = "new=1&agent=researcher";
    render(<NewSessionForm />);
    expect(picker().value).toBe("researcher");
  });
});

/* ========================================================================== */
/*  Sessions list — the "Filter by agent" select                               */
/* ========================================================================== */

describe("Sessions list: the agent filter reads names, filters by ids", () => {
  const COORD = sv("s-coord", { task: "Write the quarter-end memo", agent_type: "supervisor", origin: "job:mission" });
  const PLAIN_SUP = sv("s-plain", { task: "Nightly tidy-up", agent_type: "supervisor", origin: "schedule:nightly-brief" });
  const BUILD = sv("s-build", { task: "List the files in the website folder", agent_type: "builder" });
  const TAX = sv("s-tax", { task: "Check the 1099 totals", agent_type: "tax_helper" });
  const CUSTOM = sv("s-custom", { task: "Match the ledger", agent_type: "ledger-checker" });

  function seed() {
    w4.responses["/sessions"] = { sessions: [COORD, PLAIN_SUP, BUILD, TAX, CUSTOM] };
    w4.responses["/projects"] = { projects: [] };
    w4.responses["/agents"] = { builtin: ["supervisor", "builder", "tax_helper"], dynamic: [{ name: "ledger-checker" }] };
  }
  const filter = () => screen.getByLabelText("Filter by agent") as HTMLSelectElement;
  const label = (value: string) =>
    (Array.from(filter().options).find((o) => o.value === value)?.textContent ?? "").trim();

  it("a built-in's option reads as a word (builtin from GET /agents, not a list in the page)", () => {
    seed();
    render(<SessionsPage />);
    expect(label("builder")).toBe("Builder");
    expect(label("tax_helper")).toBe("Tax helper");
  });

  it("the Jarvis rule is kept, and a plain supervisor beside it reads 'Supervisor'", () => {
    seed();
    render(<SessionsPage />);
    expect(label("supervisor")).toBe("Jarvis / Supervisor");
  });

  it("a custom agent keeps the name the user typed", () => {
    seed();
    render(<SessionsPage />);
    expect(label("ledger-checker")).toBe("ledger-checker");
  });

  it("anti-vacuity: values stay raw and the filter still matches agent_type exactly", () => {
    seed();
    render(<SessionsPage />);
    expect(Array.from(filter().options).map((o) => o.value)).toEqual(
      expect.arrayContaining(["", "supervisor", "builder", "tax_helper", "ledger-checker"]),
    );
    fireEvent.change(filter(), { target: { value: "builder" } });
    expect(screen.getByText(BUILD.task)).toBeInTheDocument();
    expect(screen.queryByText(TAX.task)).toBeNull();
    expect(screen.queryByText(COORD.task)).toBeNull();
    fireEvent.change(filter(), { target: { value: "supervisor" } });
    expect(screen.getByText(COORD.task)).toBeInTheDocument();
    expect(screen.getByText(PLAIN_SUP.task)).toBeInTheDocument();
  });

  it("anti-vacuity: the table cell is unchanged — raw id as TEXT, cased by CSS, raw in the title", () => {
    seed();
    render(<SessionsPage />);
    const row = screen.getByText(BUILD.task).closest("tr") as HTMLElement;
    const cell = row.querySelectorAll("td")[1] as HTMLElement;
    const holder = within(cell).getByText("builder");
    expect((holder.getAttribute("class") ?? "").split(/\s+/)).toContain("capitalize");
    expect(cell.getAttribute("title")).toBe("builder");
  });
});

/* ========================================================================== */
/*  An agent's inbox — the composer's placeholder                              */
/* ========================================================================== */

describe("Agent inbox: the composer's placeholder names the agent as a name", () => {
  const EMPTY_INBOX = {
    inbox: { queued: [], claimed: [], running: [], blocked: [], recent: [] },
    health: null,
  };

  it("a built-in reads 'What should Builder do next?'", () => {
    w4.responses["/agents/builder/inbox"] = EMPTY_INBOX;
    render(<AgentInbox name="builder" />);
    expect(screen.getByPlaceholderText("What should Builder do next? It waits its turn.")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/What should builder do next/)).toBeNull();
  });

  it("a built-in with an underscore reads as words ('File manager')", () => {
    w4.responses["/agents/file_manager/inbox"] = EMPTY_INBOX;
    render(<AgentInbox name="file_manager" />);
    expect(screen.getByPlaceholderText("What should File manager do next? It waits its turn.")).toBeInTheDocument();
  });

  it("anti-vacuity: a custom agent keeps its typed name, and Queue still posts the RAW roster name", async () => {
    w4.responses["/agents/custom%3Aledger-checker/inbox"] = EMPTY_INBOX;
    render(<AgentInbox name="custom:ledger-checker" />);
    const box = screen.getByPlaceholderText("What should ledger-checker do next? It waits its turn.");
    fireEvent.change(box, { target: { value: "Match March" } });
    fireEvent.click(screen.getByRole("button", { name: /Queue for/ }));
    await waitFor(() => expect(w4.posts.some((p) => p.path === "/assignments")).toBe(true));
    const body = w4.posts.find((p) => p.path === "/assignments")!.body as Record<string, unknown>;
    expect(body.assignee).toBe("custom:ledger-checker");
  });
});

/* ========================================================================== */
/*  The roster's narrow-width picker                                           */
/* ========================================================================== */

describe("Roster picker: a built-in reads as a name, provenance still rides along", () => {
  const ROSTER: RosterEntry[] = [
    { name: "builder", kind: "builtin", description: "hands-on doer", delegable: true, healthy: true, stats: null },
    { name: "file_manager", kind: "builtin", description: "files", delegable: true, healthy: true, stats: null },
    { name: "custom:ledger-checker", kind: "dynamic", description: "yours", delegable: true, healthy: true, stats: null },
    { name: "remote:hermes", kind: "remote", description: "far away", delegable: true, healthy: false, stats: null },
  ];
  const picker = () => document.getElementById("roster-pick") as HTMLSelectElement;

  it("option text names built-ins as words (from the row's kind); custom and remote names as typed", () => {
    render(<RosterStrip entries={ROSTER} />);
    expect(optionsOf(picker())).toEqual([
      ["builder", "Builder — Built-in"],
      ["file_manager", "File manager — Built-in"],
      ["custom:ledger-checker", "ledger-checker — Yours"],
      ["remote:hermes", "hermes — Remote (offline)"],
    ]);
  });

  it("anti-vacuity: the face is still drawn from the RAW name", () => {
    // The control can bite: the label's face is NOT the raw name's face.
    expect(resolveFace("Builder")).not.toEqual(resolveFace("builder"));
    render(<RosterStrip entries={ROSTER.slice(0, 1)} />);
    const rosterFace = screen.getAllByTestId("agent-face")[0];
    cleanup();
    render(<AgentFace name="builder" />);
    const rawFace = screen.getAllByTestId("agent-face")[0];
    for (const attr of ["data-face-shape", "data-face-color", "data-face-eyes"]) {
      expect(rosterFace.getAttribute(attr)).toBe(rawFace.getAttribute(attr));
    }
  });
});

/* ========================================================================== */
/*  Your agents — the employee fields' Base type and Reports to                */
/* ========================================================================== */

describe("Your agents: Base type and Reports to read names, post ids", () => {
  const BUILTIN = ["supervisor", "builder", "planner", "file_manager"];
  const ANALYST = { name: "analyst", description: "your analyst", base_type: "planner", tools: [] };

  async function openCreate() {
    render(
      <SetupCardHarness
        initialOpen
        builtin={BUILTIN}
        dynamic={[ANALYST]}
        remotes={[]}
        models={[{ provider: "openai", model: "gpt-5" }]}
        onAgentsChanged={() => {}}
        onRemotesChanged={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "skeptic" } });
    fireEvent.change(screen.getByLabelText("Persona prompt"), { target: { value: "You are a skeptic." } });
    fireEvent.click(screen.getByTestId("employee-details-toggle"));
    return within(screen.getByTestId("employee-details"));
  }

  it("Base type options read as words; values stay the raw ids", async () => {
    const details = await openCreate();
    const base = details.getByLabelText("Base type") as HTMLSelectElement;
    expect(optionsOf(base)).toEqual([
      ["builder", "Builder"],
      ["planner", "Planner"],
      ["file_manager", "File manager"],
    ]);
  });

  it("Reports to: You, the built-ins as words, the other custom agents as typed", async () => {
    const details = await openCreate();
    const reports = details.getByLabelText("Reports to") as HTMLSelectElement;
    expect(optionsOf(reports)).toEqual([
      ["", "You"],
      ["supervisor", "Supervisor"],
      ["builder", "Builder"],
      ["planner", "Planner"],
      ["file_manager", "File manager"],
      ["custom:analyst", "analyst"],
    ]);
  });

  it("anti-vacuity: create still POSTs the raw base_type and reports_to", async () => {
    const details = await openCreate();
    fireEvent.change(details.getByLabelText("Base type"), { target: { value: "file_manager" } });
    fireEvent.change(details.getByLabelText("Reports to"), { target: { value: "builder" } });
    fireEvent.click(screen.getByRole("button", { name: /^create agent$/i }));
    await screen.findByText(/"skeptic" is ready/);
    const created = w4.posts.find((p) => p.path === "/agents")!.body as Record<string, unknown>;
    expect(created.base_type).toBe("file_manager");
    expect(created.reports_to).toBe("builder");
  });
});

/* ========================================================================== */
/*  Time-travel — a denied row says it was only TRIED                          */
/* ========================================================================== */

const DENIED: AuditEntry = {
  id: "e-deny",
  ts: "2026-10-01T10:06:00Z",
  kind: "tool",
  actor: "",
  session_id: "s-1",
  tool: "write_file",
  verdict: "deny",
  ok: false,
  reversible: false,
  undoable: false,
  summary: "write_file denied",
};
const FAILED: AuditEntry = {
  id: "e-fail",
  ts: "2026-10-01T10:05:00Z",
  kind: "tool",
  actor: "",
  session_id: "s-1",
  tool: "rename_file",
  verdict: "allow",
  ok: false,
  reversible: false,
  undoable: false,
  summary: "rename_file error",
};
const ALLOWED: AuditEntry = {
  id: "e-write",
  ts: "2026-10-01T10:04:00Z",
  kind: "tool",
  actor: "",
  session_id: "s-1",
  tool: "write_file",
  verdict: "allow",
  ok: true,
  reversible: true,
  undoable: true,
  summary: "write_file ok",
};

describe("Time-travel: a denied tool row leads 'Tried to …'", () => {
  async function renderFeed(entries: AuditEntry[]) {
    w4.audit = { entries, next_cursor: null, total: entries.length };
    render(<TimeTravelFeed sessionId="s-1" />);
    await screen.findAllByTestId("timeline-line");
  }
  const lineOf = (id: string) => {
    const lines = screen.getAllByTestId("timeline-line");
    const hit = lines.find((l) => titled(l, id).length > 0 || (l.textContent ?? "").includes(id));
    return hit!.closest("li") as HTMLElement;
  };

  it("a DENIED row's headline is 'Tried to save a file' (toolWords, lower-case after 'Tried to')", async () => {
    await renderFeed([DENIED]);
    const row = screen.getAllByTestId("timeline-line")[0].closest("li") as HTMLElement;
    expect(firstText(row)).toBe(`Tried to ${toolWords("write_file")}`);
    expect(firstText(row)).toBe("Tried to save a file");
  });

  it("a row the ledger marks ok:false (same rule as the red badge) also reads 'Tried to …'", async () => {
    await renderFeed([FAILED]);
    const row = screen.getAllByTestId("timeline-line")[0].closest("li") as HTMLElement;
    expect(firstText(row)).toBe("Tried to rename or move a file");
  });

  it("anti-vacuity: an ALLOWED row is unchanged — 'Save a file', Undo still two-press", async () => {
    await renderFeed([ALLOWED, DENIED]);
    const undo = screen.getByRole("button", { name: /^Undo$/ });
    const row = undo.closest("li") as HTMLElement;
    expect(firstText(row)).toBe("Save a file");
    expect(row.textContent ?? "").not.toMatch(/Tried to/);
    fireEvent.click(undo);
    expect(w4.posts.some((p) => p.path.startsWith("/undo/"))).toBe(false);
    expect(within(row).getByRole("button", { name: /Confirm undo\?/ })).toBeInTheDocument();
  });

  it("anti-vacuity: the denied row keeps its red 'denied' badge, the raw-id chip and the ledger line", async () => {
    await renderFeed([DENIED]);
    const row = lineOf("write_file");
    expect(within(row).getByText("denied")).toBeInTheDocument();
    // the exact id: a muted chip with the id in its title (a grant is keyed on it)
    const chip = within(row).getByText("write_file");
    expect(chip.getAttribute("title")).toBe("write_file");
    // the ledger's raw summary is still reachable
    expect(titled(row, "write_file denied").length).toBeGreaterThan(0);
    // the row's node still wears the red tone
    expect(row.querySelector('[class*="rose"], [class*="red"]')).not.toBeNull();
  });
});
