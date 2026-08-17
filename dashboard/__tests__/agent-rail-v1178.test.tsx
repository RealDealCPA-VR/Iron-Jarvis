import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * The agent rail (v1.178.0, P2) — the Agents page as a ROOM, not a form.
 *
 * WHAT THESE TESTS GUARD:
 *  - the faces stand in a persistent LEFT RAIL: one per roster agent, drawn
 *    face or stored portrait, offline said in words and not just in colour;
 *  - clicking a face SELECTS that agent and the selection drives the page —
 *    the job-post card preselects it, and an existing 1:1 thread with exactly
 *    that agent opens ("continue working with"). It must never CREATE one: a
 *    click on a portrait is not consent to POST;
 *  - the selection is ANNOUNCED (aria-current), not just tinted;
 *  - the gear-with-a-face opens BOTH setup doors (local + remote) through the
 *    card's own disclosure, so its persisted state stays honest;
 *  - an older daemon that doesn't serve /agents/roster leaves the page exactly
 *    as it was — no rail, no gear, no empty column, no error.
 */

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body: unknown) => {
      hooks.posts.push({ path, body });
      return Promise.resolve({ id: "s-new", status: "active" });
    },
  };
});

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "whileHover",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

// The round-table is a live surface with its own polling; all these tests need
// from it is WHICH thread the page told it to open.
vi.mock("@/components/agents/RoundTable", () => ({
  RoundTable: ({ threadId }: { threadId: string }) => (
    <div data-testid="round-table">{threadId}</div>
  ),
}));

import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import AgentsPage from "@/app/agents/page";

const ROSTER: RosterEntry[] = [
  {
    name: "supervisor",
    kind: "builtin",
    description: "Plans and delegates",
    delegable: false,
    healthy: true,
    stats: null,
  },
  {
    name: "builder",
    kind: "builtin",
    description: "Builds things",
    delegable: true,
    healthy: true,
    stats: null,
  },
  {
    name: "custom:analyst",
    kind: "dynamic",
    description: "Your analyst",
    delegable: true,
    healthy: true,
    stats: null,
  },
  {
    name: "remote:opus-box",
    kind: "remote",
    description: "The other machine",
    delegable: true,
    healthy: true,
    stats: null,
    last_active: "2026-08-14T09:00:00+00:00",
    avatar: "/agents/opus-box/avatar",
  },
  {
    name: "remote:down-box",
    kind: "remote",
    description: "Currently unreachable",
    delegable: true,
    healthy: false,
    stats: null,
  },
];

/** Two threads: the newest (auto-selected) is a panel of nobody, and the 1:1
 *  with the analyst is NOT the default — so "the rail opened it" can't pass by
 *  accident. */
const THREADS = [
  {
    id: "t-other",
    title: "Newest thread",
    participants: [],
    message_count: 3,
    updated_at: "2026-08-14T10:00:00Z",
  },
  {
    id: "t-analyst",
    title: "Talk with analyst",
    participants: [
      { key: "dynamic:analyst", source: "dynamic", name: "analyst", role: "" },
    ],
    message_count: 5,
    updated_at: "2026-08-13T10:00:00Z",
  },
];

const rail = () => screen.getByTestId("roster-rail");
const railButton = (name: RegExp | string) =>
  within(rail()).getByRole("button", { name });
const targetSelect = () =>
  screen.getByLabelText("Who takes it") as HTMLSelectElement;

beforeEach(() => {
  hooks.api = {
    "/agents": { builtin: ["supervisor", "builder"], dynamic: [] },
    "/agents/remote": { agents: [] },
    "/models": { models: [] },
    "/agents/roster": { roster: ROSTER },
    "/agents/threads": { threads: THREADS },
    "/sessions": { sessions: [] },
    "/projects": { projects: [] },
  };
  hooks.posts = [];
  // jsdom has no scrollIntoView; the page's handlers call it.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/* ------------------------------------------------------------- the column --- */

describe("the rail — a face per agent", () => {
  it("draws one clickable face per roster agent", () => {
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    const rows = within(rail()).getAllByRole("button");
    expect(rows).toHaveLength(ROSTER.length);
    // v1.179.0: a remote row now also carries its "Remote" pill — the user
    // asked for that indicator on every remote agent after it moved off the
    // deleted detail block. Strip the provenance words too, so this still
    // pins the NAMES and their order rather than being loosened away.
    expect(
      rows.map((b) =>
        b.textContent?.replace(/offline/gi, "").replace(/remote/gi, "").trim(),
      ),
    ).toEqual([
      "supervisor",
      "builder",
      "analyst",
      "opus-box",
      "down-box",
    ]);
    // ...and the pill is genuinely there, on the remote rows only.
    const remoteRows = rows.filter((b) => /remote/i.test(b.textContent ?? ""));
    expect(remoteRows).toHaveLength(2); // opus-box + down-box
    // Four drawn faces + the one agent with a stored portrait = five faces.
    expect(within(rail()).getAllByTestId("agent-face")).toHaveLength(4);
    // The portrait is queried by NODE, not by alt text: beside its own visible
    // name it is decorative, so it carries alt="" and aria-hidden — a portrait
    // that announced the name a second time is a screen-reader stutter.
    const portraits = rail().querySelectorAll("img");
    expect(portraits).toHaveLength(1);
    expect((portraits[0] as HTMLImageElement).src).toContain(
      "http://test/agents/opus-box/avatar",
    );
    expect(portraits[0].getAttribute("alt")).toBe("");
  });

  it("says 'offline' in words, not only in colour", () => {
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    // The accessible name of the unreachable remote's row carries the state —
    // a rose-tinted icon alone reaches nobody using a screen reader.
    expect(railButton(/down-box\s*offline/)).toBeTruthy();
    expect(within(rail()).getByRole("button", { name: "builder" })).toBeTruthy();
  });

  it("hands back the kind, the BARE name, and whether it can take work", () => {
    const onSelect = vi.fn();
    render(<RosterStrip entries={ROSTER} onSelect={onSelect} />);
    fireEvent.click(railButton("analyst"));
    expect(onSelect).toHaveBeenCalledWith("dynamic", "analyst", true);
    // Non-delegable and unhealthy are BOTH "can't take work" — the page must
    // not have to re-derive that rule.
    fireEvent.click(railButton("supervisor"));
    expect(onSelect).toHaveBeenLastCalledWith("builtin", "supervisor", false);
    fireEvent.click(railButton(/down-box/));
    expect(onSelect).toHaveBeenLastCalledWith("remote", "down-box", false);
  });
});

describe("the rail — the selection is announced", () => {
  it("marks the clicked face aria-current, and only that one", () => {
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    fireEvent.click(railButton("analyst"));
    expect(railButton("analyst").getAttribute("aria-current")).toBe("true");
    const marked = within(rail())
      .getAllByRole("button")
      .filter((b) => b.getAttribute("aria-current") === "true");
    expect(marked).toHaveLength(1);
    // And the detail below follows the same selection (one source of truth).
    expect(screen.getByText("Your analyst")).toBeTruthy();
  });

  it("announces nobody as current until somebody is actually picked", () => {
    // ADVERSARIAL REVIEW. `selected` falls back to entries[0] so the detail
    // block below has something to preview — but a PREVIEW is not a SELECTION.
    // Marking the fallback row active tinted the first face and announced it
    // aria-current on first paint, and the roster's first entry is the
    // supervisor: `delegable: false`, the one agent that can never take work.
    // A user reading "supervisor is current" then typed a job and Post sent it
    // to the Team. Nothing is current until a face (or the narrow picker) says
    // so.
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    expect(
      within(rail())
        .getAllByRole("button")
        .filter((b) => b.getAttribute("aria-current") === "true"),
    ).toHaveLength(0);
    fireEvent.click(railButton("builder"));
    expect(railButton("builder").getAttribute("aria-current")).toBe("true");
  });

  it("renders no column at all for a page with no selection to drive", () => {
    // The older in-flow composition: a column of faces nobody can select is
    // decoration, so it simply isn't drawn.
    render(<RosterStrip entries={ROSTER} />);
    expect(screen.queryByTestId("roster-rail")).toBeNull();
    expect(screen.getByLabelText("Choose an agent")).toBeTruthy();
  });
});

/* ------------------------------------------------- selection drives the page */

describe("the page — clicking a face", () => {
  it("preselects that agent in the job-post card", async () => {
    render(<AgentsPage />);
    expect(targetSelect().value).toBe("__team__");
    fireEvent.click(railButton("analyst"));
    await waitFor(() => expect(targetSelect().value).toBe("custom:analyst"));
  });

  it("never preselects an agent that cannot take the work", async () => {
    render(<AgentsPage />);
    fireEvent.click(railButton(/down-box/));
    // An unreachable remote (and the non-delegable supervisor) leave the
    // target on Team rather than putting a name in the box the card would
    // quietly fall back from at submit time.
    await waitFor(() => expect(targetSelect().value).toBe("__team__"));
    fireEvent.click(railButton("supervisor"));
    await waitFor(() => expect(targetSelect().value).toBe("__team__"));
  });

  it("leaves no STALE target behind when the next pick cannot take work", async () => {
    // ADVERSARIAL REVIEW. Skipping the preselect for an un-workable agent is
    // only half the job: pick a workable one FIRST and the old target survives
    // the next pick. Measured before the fix — rail ring + aria-current on
    // "down-box", job card still reading "custom:analyst", so Post would have
    // sent the job to the analyst while the page's own highlight named the
    // offline box. The rail is the page's primary control now; it may never
    // point at one agent while the work is aimed at another.
    render(<AgentsPage />);
    fireEvent.click(railButton("analyst"));
    await waitFor(() => expect(targetSelect().value).toBe("custom:analyst"));
    fireEvent.click(railButton(/down-box/));
    await waitFor(() => {
      expect(railButton(/down-box/).getAttribute("aria-current")).toBe("true");
      expect(targetSelect().value).toBe("__team__");
    });
    // Same for the non-delegable supervisor, reached from a live target.
    fireEvent.click(railButton("analyst"));
    await waitFor(() => expect(targetSelect().value).toBe("custom:analyst"));
    fireEvent.click(railButton("supervisor"));
    await waitFor(() => expect(targetSelect().value).toBe("__team__"));
  });

  it("continues an existing 1:1 thread with exactly that agent", async () => {
    render(<AgentsPage />);
    // The auto-selected thread is the newest one, not the analyst's.
    await waitFor(() =>
      expect(screen.getByTestId("round-table").textContent).toBe("t-other"),
    );
    fireEvent.click(railButton("analyst"));
    await waitFor(() =>
      expect(screen.getByTestId("round-table").textContent).toBe("t-analyst"),
    );
  });

  it("keeps the highlight through the re-render the click itself causes", async () => {
    // THE PAGE OWNS THE SELECTION. When the rail kept it internally, the
    // highlight was lost the moment the click's own state updates remounted
    // the strip: the rail then pointed at one agent while the job card was
    // aimed at another — the page disagreeing with itself, silently. (The
    // framer-motion test double makes that remount happen every render, which
    // is exactly why this test can see it at all.)
    render(<AgentsPage />);
    fireEvent.click(railButton("analyst"));
    // BOTH assertions live INSIDE the waitFor. The highlight is page state and
    // the target is a CHILD EFFECT one commit downstream of it; asserting the
    // second one after the await is the exact shape that flaked CI in
    // v1.177.1 — it happens to be synchronous under act() today, and "today"
    // is not a guarantee.
    await waitFor(() => {
      expect(railButton("analyst").getAttribute("aria-current")).toBe("true");
      expect(targetSelect().value).toBe("custom:analyst");
    });
  });

  it("the narrow-width picker means the same thing as the faces", async () => {
    // The <select> IS the rail below md, not a second control with its own
    // idea of who is selected — choosing there drives the page identically.
    render(<AgentsPage />);
    fireEvent.change(screen.getByLabelText("Choose an agent"), {
      target: { value: "custom:analyst" },
    });
    await waitFor(() => expect(targetSelect().value).toBe("custom:analyst"));
    expect(railButton("analyst").getAttribute("aria-current")).toBe("true");
  });

  it("creates nothing — an agent with no thread just becomes the selection", async () => {
    render(<AgentsPage />);
    fireEvent.click(railButton("builder"));
    await waitFor(() => expect(targetSelect().value).toBe("builder"));
    // The open thread is untouched and no POST went out: starting a
    // conversation stays behind the explicit Talk button.
    expect(screen.getByTestId("round-table").textContent).toBe("t-other");
    expect(hooks.posts).toHaveLength(0);
  });
});

/* -------------------------------------------------------------- the gear --- */

describe("the gear with a face", () => {
  it("opens the create surface — local AND remote behind one door", async () => {
    render(<AgentsPage />);
    // Collapsed to start with (nothing in localStorage).
    expect(screen.queryByText("Create an agent")).toBeNull();
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());
    expect(screen.getByText("Connect a remote agent")).toBeTruthy();
  });

  it("says what it does — the drawn gear is decorative", () => {
    render(<AgentsPage />);
    const gear = screen.getByRole("button", {
      name: /configure a local or remote agent/i,
    });
    expect(gear).toBe(screen.getByTestId("roster-gear"));
    const drawn = within(gear).getByTestId("gear-face");
    // Decorative graphic: aria-hidden with NO role — the button around it
    // already carries the name.
    expect(drawn.getAttribute("aria-hidden")).toBe("true");
    expect(drawn.getAttribute("role")).toBeNull();
  });

  it("keeps setup behind the gear even when it was left open before", async () => {
    // REWRITTEN for v1.179.0, and the inversion is the point. In v1.178.0 the
    // gear only REVEALED a card that the page already rendered, so a persisted
    // "open" meant setup greeted you on arrival. The user asked for the
    // opposite: "the set up agents should all be contained in the new agent
    // gear face on the left pane and not shown unless the user decided to
    // configure an agent." So a stored open flag no longer puts a form on the
    // page — only the click does.
    window.localStorage.setItem("ij_agents_setup_open", "1");
    render(<AgentsPage />);
    await waitFor(() => expect(screen.getByTestId("roster-gear")).toBeTruthy());
    expect(screen.queryByText("Create an agent")).toBeNull();

    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());
    // ...and it folds away again, so the gear is a door, not a one-way reveal.
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.queryByText("Create an agent")).toBeNull());
  });
});

/* ------------------------------------------------------ honest degradation --- */

describe("an older daemon that does not serve /agents/roster", () => {
  beforeEach(() => {
    delete hooks.api["/agents/roster"];
  });

  it("leaves the page working exactly as it did — no rail, no gear, no error", async () => {
    render(<AgentsPage />);
    expect(screen.queryByTestId("roster-rail")).toBeNull();
    expect(screen.queryByTestId("roster-gear")).toBeNull();
    // Everything the page had before is still there and still live.
    expect(screen.getByText("Give work")).toBeTruthy();
    expect(targetSelect().value).toBe("__team__");
    await waitFor(() =>
      expect(screen.getByTestId("round-table").textContent).toBe("t-other"),
    );
  });
});
