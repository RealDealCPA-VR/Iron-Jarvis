/**
 * v1.329.0 — calm chat wave 8, J4: the mission screen (/agents) gets the calm
 * look.
 *
 * The closing audit (fx__agents__desk.png) found /agents the one page still
 * built from pre-calm stacked cards:
 *  - a bordered left menu (New task, Chat, Projects, Agents, Tools, Files,
 *    Settings) repeating the sidebar's own navigation;
 *  - a boxed objective card with a filled accent Start;
 *  - a boxed "Your projects" card and a boxed "Team" card;
 *  - copy with dash asides ("Quiet — nothing running").
 *
 * Now: the objective composer is the page's ONE card, drawn like the chat
 * composer (borderless box inside, the project a ghost chip, Start quiet
 * until there is an objective, then the one accent). "Your projects",
 * "Recent objectives", "Team", the project's work and the live activity are
 * plain sections with quiet sentence-case labels and hairlines. The menu is
 * two text tabs, New task and Your team, the only two places nothing else in
 * the app offers (Chat, Projects and Settings are sidebar rows; Tools and
 * Files are on Everything and in Ctrl K).
 */

import React from "react";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const S = vi.hoisted(() => ({ api: {} as Record<string, unknown> }));

function apiState(path: string | null) {
  return { data: path ? (S.api[path] ?? null) : null, error: null, loading: false, reload: () => {} };
}

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => apiState(path),
  usePolledApi: (path: string | null) => apiState(path),
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
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    get: (path: string) => Promise.resolve(S.api[path] ?? {}),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/agents/AgentFace", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  default: (p: { name: string }) => <span data-testid="face" data-name={p.name} />,
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => null }));

import { MissionScreen } from "@/components/agents/mission/MissionScreen";
import { LiveActivity } from "@/components/agents/mission/LiveActivity";
import { AgentCards } from "@/components/agents/mission/AgentCards";
import { REMOTE_SEES, countsLine, waitingKindLabel } from "@/lib/agentWorlds";

const classes = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/** A card look: the app's card class, a full border box, or a filled panel. */
function looksLikeCard(el: Element): boolean {
  const c = classes(el);
  return (
    c.includes("card-surface") ||
    c.includes("border") ||
    c.some((x) => /^bg-ink-\d+/.test(x)) ||
    c.some((x) => /^rounded-(xl|2xl|\[\d+px\])$/.test(x) && c.some((y) => y.startsWith("shadow")))
  );
}

/** Every element on the screen that looks like a card. */
function cards(root: Element): Element[] {
  return Array.from(root.querySelectorAll("*")).filter(looksLikeCard);
}

beforeEach(() => {
  S.api = {
    "/agents/roster": {
      roster: [
        { name: "builder", kind: "builtin", description: "hands-on doer for files, shell, and documents", delegable: true, healthy: true },
        { name: "guide", kind: "builtin", description: "the Iron Jarvis expert who explains the app and finds your things in it", delegable: true, healthy: true },
      ],
    },
    "/projects": { projects: [{ id: "p1", name: "Harbor Street Cafe", status: "active" }] },
    "/missions": {
      missions: [{ id: "s9", objective: "close the books", status: "completed", waiting: false, project_id: null }],
    },
    "/agents/worlds": {
      worlds: [
        {
          project: { id: "p1", name: "Harbor Street Cafe" },
          team: [],
          thread_id: null,
          counts: { waiting: 0, running: 0, queued: 0, done_7d: 0 },
        },
      ],
      general: { thread_count: 0 },
    },
    "/projects/p1/world": {
      project: { id: "p1", name: "Harbor Street Cafe" },
      team: [],
      waiting: [],
      completed: [],
    },
    "/missions?project_id=p1": { missions: [] },
  };
});
afterEach(cleanup);

function frontDoor(over: Partial<React.ComponentProps<typeof MissionScreen>> = {}) {
  const props = { missionId: "", onOpen: vi.fn(), onNew: vi.fn(), onTeam: vi.fn(), onProject: vi.fn(), ...over };
  const view = render(<MissionScreen {...props} />);
  return { props, container: view.container };
}

/* ---------------------------------------------------------------- 1 ---- */

describe("the objective composer is the page's one card", () => {
  it("the front door draws exactly one card, and it is the composer", () => {
    const { container } = frontDoor();
    const card = screen.getByTestId("mission-composer-card");
    expect(cards(screen.getByTestId("mission-screen"))).toEqual([card]);
    // Everything the user starts a task with lives inside it.
    expect(within(card).getByTestId("mission-input")).toBeTruthy();
    expect(within(card).getByTestId("mission-project")).toBeTruthy();
    expect(within(card).getByTestId("mission-start")).toBeTruthy();
    // The box is borderless inside the card (no field box of its own).
    expect(classes(screen.getByTestId("mission-input"))).not.toContain("field");
    expect(classes(screen.getByTestId("mission-input"))).toContain("bg-transparent");
    // The project is a ghost chip, not a field.
    const pick = screen.getByTestId("mission-project");
    expect(classes(pick)).not.toContain("field");
    expect(classes(pick)).toContain("bg-transparent");
    expect(container.querySelector(".card-surface")).toBeNull();
  });

  it("Start is quiet until there is an objective, then the one accent on the screen", () => {
    const { container } = frontDoor();
    const start = screen.getByTestId("mission-start") as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(classes(start)).not.toContain("btn-accent");
    expect(container.querySelectorAll(".btn-accent")).toHaveLength(0);
    fireEvent.change(screen.getByTestId("mission-input"), { target: { value: "write the Q3 summary" } });
    expect(start.disabled).toBe(false);
    expect(classes(start)).toContain("btn-accent");
    expect(container.querySelectorAll(".btn-accent")).toHaveLength(1);
    expect(start.textContent).toContain("Start");
  });

  it("a project's screen keeps the same one card (no project chip: the project is fixed)", () => {
    frontDoor({ projectId: "p1" });
    const card = screen.getByTestId("mission-composer-card");
    expect(cards(screen.getByTestId("mission-screen"))).toEqual([card]);
    expect(screen.queryByTestId("mission-project")).toBeNull();
    expect(screen.getByTestId("mission-project-work")).toBeTruthy();
  });
});

/* ---------------------------------------------------------------- 2 ---- */

describe("the other sections are plain, with quiet sentence-case labels and hairlines", () => {
  const LABEL = (section: Element, text: string) =>
    Array.from(section.querySelectorAll("h2")).find((h) => h.textContent === text) ?? null;

  it("Your projects, Recent objectives and Team: no card, a quiet label, a hairline", () => {
    frontDoor();
    for (const [id, label] of [
      ["mission-projects", "Your projects"],
      ["mission-recent", "Recent objectives"],
      ["mission-team", "Team"],
    ] as const) {
      const section = screen.getByTestId(id);
      expect(looksLikeCard(section), id).toBe(false);
      const h = LABEL(section, label);
      expect(h, id).not.toBeNull();
      const c = classes(h);
      expect(c, id).toContain("font-medium");
      expect(c, id).toContain("text-zinc-400");
      expect(c, id).not.toContain("uppercase");
      expect(c, id).not.toContain("font-semibold");
      // A hairline sets it off: on the section, or on its list.
      const lined = [section, ...Array.from(section.querySelectorAll("ul"))].some((el) =>
        classes(el).includes("hairline"),
      );
      expect(lined, id).toBe(true);
    }
  });

  it("the Team section is set off by a hairline above it on a phone and to its left from lg", () => {
    frontDoor();
    const c = classes(screen.getByTestId("mission-team"));
    for (const k of ["border-t", "hairline", "lg:border-l", "lg:border-t-0"]) expect(c).toContain(k);
  });

  it("a project's work is plain text tabs over a hairline", () => {
    frontDoor({ projectId: "p1" });
    const work = screen.getByTestId("mission-project-work");
    expect(looksLikeCard(work)).toBe(false);
    const tab = within(work).getByTestId("project-tab-board");
    expect(classes(tab)).not.toContain("border-b-2");
    expect(tab.getAttribute("aria-selected")).toBe("true");
    expect(classes(tab)).toContain("text-zinc-100");
    expect(classes(within(work).getByTestId("project-tab-completed"))).toContain("text-zinc-500");
  });

  it("a project's back control and Pick a team are ghosts, not bordered buttons", () => {
    frontDoor({ projectId: "p1" });
    for (const id of ["mission-back", "mission-edit-team"]) {
      const c = classes(screen.getByTestId(id));
      expect(c, id).not.toContain("btn-ghost");
      expect(c, id).not.toContain("border");
      expect(c, id).toContain("hover:bg-white/[0.06]");
    }
  });

  it("an objective's teammates are hairline rows in the Team section, not boxed cards", () => {
    render(
      <AgentCards
        onChanged={() => {}}
        members={[
          {
            session_id: "c1",
            agent: "researcher",
            name: "Researcher",
            kind: "builtin",
            task: "research the three main competitors",
            status: "done",
            progress: { pct: 100, label: "", basis: "done" },
            activity: "",
            waiting_on: null,
            steps: 3,
            result: "found three competitors",
            files: [],
            started_at: null,
            finished_at: null,
            provider: "claude-cli",
            model: "opus",
          },
        ] as never}
      />,
    );
    const row = screen.getByTestId("mission-card-researcher");
    expect(looksLikeCard(row)).toBe(false);
    expect(classes(row).some((x) => x.startsWith("bg-"))).toBe(false);
    const list = screen.getByTestId("mission-cards");
    expect(classes(list)).toContain("hairline");
    expect(classes(list)).toContain("divide-y");
    // The detail's labels are sentence case, never shouted.
    fireEvent.click(within(row).getAllByRole("button")[0]);
    const handed = within(row).getByText("Handed");
    expect(classes(handed)).not.toContain("uppercase");
  });

  it("the live activity log is a plain section", () => {
    render(<LiveActivity running lines={[{ at: null, text: "Builder wrote summary.md", tone: "ok" }] as never} />);
    const log = screen.getByTestId("mission-activity");
    expect(looksLikeCard(log)).toBe(false);
    expect(classes(log.querySelector("h2"))).toContain("text-zinc-400");
  });
});

/* ---------------------------------------------------------------- 3 ---- */

describe("the menu is the page's own two places, never the sidebar again", () => {
  it("two quiet text tabs: New task and Your team, no links out, no box", () => {
    frontDoor();
    const rail = screen.getByTestId("mission-rail");
    expect(Array.from(rail.querySelectorAll("button")).map((b) => b.textContent)).toEqual(["New task", "Your team"]);
    expect(rail.querySelectorAll("a")).toHaveLength(0);
    expect(looksLikeCard(rail)).toBe(false);
    for (const label of ["Chat", "Projects", "Tools", "Files", "Settings"]) {
      expect(within(rail).queryByText(label)).toBeNull();
    }
    // The open one is in ink, the other muted; neither is a filled chip.
    const on = screen.getByTestId("mission-rail-new");
    const off = screen.getByTestId("mission-rail-agents");
    expect(on.getAttribute("aria-current")).toBe("page");
    expect(classes(on)).toContain("text-zinc-100");
    expect(classes(off)).toContain("text-zinc-500");
    for (const el of [on, off]) expect(classes(el).some((x) => x.startsWith("bg-"))).toBe(false);
  });

  it("both tabs still do their job", () => {
    const { props } = frontDoor();
    fireEvent.click(screen.getByTestId("mission-rail-agents"));
    expect(props.onTeam).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("mission-rail-new"));
    expect(props.onNew).toHaveBeenCalledTimes(1);
  });
});

/* ---------------------------------------------------------------- 4 ---- */

describe("the mission page's words are plain sentences", () => {
  it("an idle project says it plainly under its name", () => {
    expect(countsLine({ waiting: 0, running: 0, queued: 0, done_7d: 0 })).toBe("Nothing running right now");
    frontDoor();
    expect(within(screen.getByTestId("mission-project-p1")).getByText("Nothing running right now")).toBeTruthy();
  });

  it("a busy project still lists its counts", () => {
    expect(countsLine({ waiting: 2, running: 1, queued: 0, done_7d: 4 })).toBe(
      "2 waiting on you · 1 running · 4 done this week",
    );
  });

  it("the remote and waiting words carry no dash aside", () => {
    const aside = /(^|\s)[—–](\s|$)/;
    expect(REMOTE_SEES).toBe(
      "Remote agents see only the task Jarvis hands them, never the project's files or the other agents' work",
    );
    expect(waitingKindLabel("needs_you")).toBe("Stopped. It needs your answer");
    expect(waitingKindLabel("interrupted")).toBe("Cut off by a restart. Continue?");
    for (const t of [REMOTE_SEES, waitingKindLabel("needs_you"), waitingKindLabel("interrupted")]) {
      expect(t).not.toMatch(aside);
    }
  });
});

/* ---------------------------------------------------------------- 5 ---- */

describe("the mission files keep their labels quiet", () => {
  const ROOT = path.join(__dirname, "..");
  const DIR = "components/agents/mission";
  const files = [
    ...readdirSync(path.join(ROOT, DIR))
      .filter((f) => /\.tsx?$/.test(f))
      .map((f) => `${DIR}/${f}`),
    "app/agents/page.tsx",
  ];
  /** Code without comments, so a comment quoting an old class never counts. */
  const code = (rel: string) =>
    readFileSync(path.join(ROOT, rel), "utf8")
      .replace(/\r\n/g, "\n")
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

  it("no shouted (uppercase) label and no card-surface on the mission screen's sections", () => {
    const shouted = files.filter((rel) => /(^|[\s"'`])uppercase([\s"'`]|$)/.test(code(rel)));
    expect(shouted).toEqual([]);
    // card-surface is left only where it is the one panel of its screen: the
    // objective's result, its loading shape, Your team's panel, an old room
    // and the page's loading skeleton.
    const boxed = files.filter((rel) => code(rel).includes("card-surface")).sort();
    expect(boxed).toEqual(
      [
        "app/agents/page.tsx",
        `${DIR}/MissionOutput.tsx`,
        `${DIR}/MissionScreen.tsx`,
        `${DIR}/RoomTranscript.tsx`,
        `${DIR}/TeamScreen.tsx`,
      ].sort(),
    );
    const screenCode = code(`${DIR}/MissionScreen.tsx`);
    expect(screenCode.match(/card-surface/g)).toHaveLength(1); // the result's loading shape only
    expect(screenCode).toContain('data-testid="mission-output-loading"');
  });
});
