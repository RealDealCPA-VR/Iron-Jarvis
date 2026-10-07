import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

/**
 * v1.171.0 P1 — faces everywhere (TeamTree, kanban SessionCard, RoundTable,
 * PanelPicker).
 *
 * What can fail silently here, and is therefore pinned:
 *
 *  - ONE IDENTITY PER NAME: the face is seeded from the agent's BARE name on
 *    EVERY surface. Session surfaces (TeamTree, kanban) hold a bare
 *    agent_type; thread surfaces hold a participant key "<source>:<name>" —
 *    seeding by the full key hashed to a DIFFERENT shape/color, so the same
 *    agent wore two faces (the v1.171.0 review's cross-surface split). Every
 *    thread surface now seeds through faceIdentity(key) / p.name, and the
 *    rendered shape is compared against faceShape(<bare name>) with a
 *    meta-guard proving the bare and key seeds really differ (otherwise the
 *    assertion couldn't catch a regression).
 *  - MOOD TRUTH: moods must come from REAL state through the one shared
 *    moodForStatus mapping (or tracked round/error state) — a mutation that
 *    hardcodes mood="idle" (or maps status → mood locally and wrongly) renders
 *    fine and lies. Each surface is asserted with a status whose mood differs
 *    from the "idle" default, so the hardcode mutant dies.
 *  - PORTRAITS WIN: an option that carries a stored portrait must render the
 *    <img>, not the geometric face — and the portrait must survive into the
 *    picker's footer chip (a second, separately-coded render site).
 *  - DECORATIVE BESIDE A LABEL: a face sitting immediately beside its own
 *    visible name is title="" (no duplicate SVG <title> text node — the
 *    getByText ambiguity that forced title="" on TeamTree) AND wrapped
 *    aria-hidden (title="" alone left role="img" with an EMPTY aria-label, an
 *    invalid ARIA state). A face with NO adjacent label (RoundTable's
 *    empty-state panel) keeps its label — it is the only identity carrier.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    calls: [] as string[],
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => {
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));

// Markdown rendering is not under test — the real react-markdown pipeline
// just slows the suite down and adds nothing to a face assertion.
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
  };
});

import AgentFace, {
  faceColor,
  faceShape,
  moodForStatus,
} from "@/components/agents/AgentFace";
import { TeamTree, type TeamResponse } from "@/components/sessions/TeamTree";
import { CardInner } from "@/components/kanban/SessionCard";
import type { SessionView } from "@/lib/types";
import { faceIdentity } from "@/components/agents/AgentFace";

// jsdom has no scrollIntoView; the RoundTable pins its transcript with it.
window.HTMLElement.prototype.scrollIntoView = () => {};

afterEach(() => {
  cleanup();
  api.calls = [];
  api.responses = {};
});

/* ---------------------------------------------------------------- helpers */

/** All rendered faces whose accessible label (title ?? name) matches —
 *  only labeled (non-decorative) faces show up here. */
function facesLabelled(label: string): HTMLElement[] {
  return screen
    .getAllByTestId("agent-face")
    .filter((f) => f.getAttribute("aria-label") === label);
}

/** All rendered geometric faces wearing the deterministic shape of `name` —
 *  how decorative (unlabeled) faces are found. */
function facesOf(name: string): HTMLElement[] {
  return screen
    .getAllByTestId("agent-face")
    .filter((f) => f.getAttribute("data-face-shape") === faceShape(name));
}

/** A decorative face must be hidden from the accessibility tree. Since the
 *  coordinator's AgentFace fix (P1 review defect 4), decorative mode renders
 *  aria-hidden with NO role and NO aria-label — an empty aria-label on
 *  role="img" was the invalid-ARIA state this helper used to (wrongly) pin. */
function expectDecorative(face: HTMLElement) {
  expect(face.getAttribute("aria-label")).toBeNull();
  expect(face.getAttribute("role")).toBeNull();
  expect(face.closest('[aria-hidden="true"]')).not.toBeNull();
  expect(face.querySelector("title")).toBeNull();
}

function sv(id: string, status: string, over: Partial<SessionView> = {}): SessionView {
  return {
    id,
    task: `task ${id}`,
    agent_type: "coder",
    provider: "mock",
    model: "mock-model",
    status,
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-08-12T10:00:00Z",
    finished_at: null,
    ...over,
  };
}

/* ------------------------------------------------- determinism + mapping */

describe("face determinism", () => {
  it("same name → same shape, render after render (and the shape is the seed's)", () => {
    const { unmount } = render(<AgentFace name="builder" />);
    const first = screen.getByTestId("agent-face").getAttribute("data-face-shape");
    unmount();
    render(<AgentFace name="builder" />);
    const second = screen.getByTestId("agent-face").getAttribute("data-face-shape");
    expect(first).toBe(second);
    // Not merely stable — it is the DETERMINISTIC shape for that name, so a
    // surface passing the wrong seed can be caught against faceShape().
    expect(first).toBe(faceShape("builder"));
  });

  it("moodForStatus is the one status→mood mapping, branch by branch", () => {
    // work: everything that means "running right now"
    for (const s of ["active", "running", "resuming", "cancelling", "ACTIVE"]) {
      expect(moodForStatus(s)).toBe("work");
    }
    // error / done
    expect(moodForStatus("failed")).toBe("error");
    expect(moodForStatus("error")).toBe("error");
    expect(moodForStatus("completed")).toBe("done");
    // everything unknown is idle — never an invented mood
    expect(moodForStatus("queued")).toBe("idle");
    expect(moodForStatus("cancelled")).toBe("idle");
    expect(moodForStatus("")).toBe("idle");
    expect(moodForStatus(null)).toBe("idle");
    expect(moodForStatus(undefined)).toBe("idle");
  });
});

/* ---------------------------------------------------------- faceIdentity */

describe("faceIdentity — THE canonical face seed for participant keys", () => {
  it("strips exactly the source prefix; bare names pass through", () => {
    expect(faceIdentity("builtin:builder")).toBe("builder");
    expect(faceIdentity("dynamic:remy")).toBe("remy");
    expect(faceIdentity("builder")).toBe("builder");
    // Only the FIRST colon is a prefix — the rest belongs to the name (the
    // same rule the display name has always used).
    expect(faceIdentity("remote:host:9000")).toBe("host:9000");
  });

  it("the bare and key seeds REALLY differ — the meta-guard behind every cross-surface assertion", () => {
    // If these ever hashed alike, the surface tests below could not tell a
    // key-seeded face from a name-seeded one and would go vacuously green.
    expect(faceShape("builtin:builder")).not.toBe(faceShape("builder"));
    expect(faceColor("dynamic:remy")).not.toBe(faceColor("remy"));
  });
});

/* ---------------------------------------------------------------- TeamTree */

/** root run r-root spawned s-child (active); s-child's run spawned s-grand
 *  (completed) — two nodes with two DIFFERENT real statuses, so a hardcoded
 *  mood cannot satisfy both assertions. */
const TEAM: TeamResponse = {
  found: true,
  session_id: "s-root",
  children: [
    {
      ...sv("s-child", "active"),
      parent_run_id: "r-root",
    },
    {
      ...sv("s-grand", "completed", { agent_type: "researcher" }),
      parent_run_id: "r-child",
    },
  ],
  runs: [
    { id: "r-root", session_id: "s-root", parent_id: null, agent_type: "supervisor", state: "completed" },
    { id: "r-child", session_id: "s-child", parent_id: "r-root", agent_type: "coder", state: "running" },
  ],
};

describe("TeamTree faces", () => {
  /** The face sitting beside a node's session link. Tree faces are decorative
   *  (title="" + aria-hidden — the agent's name IS the adjacent link), so
   *  they are found by row structure, not label. */
  function faceBeside(linkText: string): HTMLElement {
    const row = screen.getByText(linkText).closest("div")!;
    const face = row.querySelector<HTMLElement>('[data-testid="agent-face"]');
    expect(face).not.toBeNull();
    return face!;
  }

  it("every node wears a face seeded by its agent_type, mood from its REAL status", async () => {
    api.responses["/sessions/s-root/team"] = TEAM;
    render(<TeamTree sessionId="s-root" active={false} />);
    expect(await screen.findByText("Team · 2 agents")).toBeInTheDocument();

    const coder = faceBeside("coder");
    expect(coder.getAttribute("data-face-shape")).toBe(faceShape("coder"));
    expect(coder.getAttribute("data-face-mood")).toBe("work"); // active → work

    const researcher = faceBeside("researcher");
    expect(researcher.getAttribute("data-face-shape")).toBe(faceShape("researcher"));
    expect(researcher.getAttribute("data-face-mood")).toBe("done"); // completed → done
    // The done-smile is really drawn, not just an attribute.
    expect(researcher.querySelector('[data-testid="face-smile"]')).not.toBeNull();
  });

  it("a failed delegate shows the honest X-X eyes", async () => {
    api.responses["/sessions/s-root/team"] = {
      ...TEAM,
      children: [{ ...sv("s-child", "failed"), parent_run_id: "r-root" }],
    };
    render(<TeamTree sessionId="s-root" active={false} />);
    expect(await screen.findByText("Team · 1 agent")).toBeInTheDocument();
    const face = faceBeside("coder");
    expect(face.getAttribute("data-face-mood")).toBe("error");
    expect(face.querySelector('[data-testid="face-eyes-error"]')).not.toBeNull();
  });

  it("tree faces are decorative: no duplicate text node, hidden from the a11y tree", async () => {
    // The exact regression that would break the frozen v1.166 suite: an SVG
    // <title>coder</title> beside the "coder" link makes getByText ambiguous.
    api.responses["/sessions/s-root/team"] = TEAM;
    render(<TeamTree sessionId="s-root" active={false} />);
    expect(await screen.findByText("Team · 2 agents")).toBeInTheDocument();
    expect(screen.getAllByText("coder")).toHaveLength(1);
    // And decorative means REALLY decorative — not an img with an empty name.
    expectDecorative(faceBeside("coder"));
    expectDecorative(faceBeside("researcher"));
  });
});

/* -------------------------------------------------------- kanban SessionCard */

describe("kanban card faces", () => {
  it("a failed card's face shows error eyes, seeded by the agent type", () => {
    render(<CardInner session={sv("s1", "failed")} lane="failed" />);
    const face = screen.getByTestId("agent-face");
    expect(face.getAttribute("data-face-shape")).toBe(faceShape("coder"));
    expect(face.getAttribute("data-face-mood")).toBe("error");
    expect(face.querySelector('[data-testid="face-eyes-error"]')).not.toBeNull();
  });

  it("the mood tracks the session's REAL status, not the lane it renders in", () => {
    // A review-lane card is still an ACTIVE session — the face must say work.
    // (This is the nested-team situation too: children render in the parent's
    // column but keep their own truth.)
    render(<CardInner session={sv("s2", "active")} lane="review" />);
    expect(screen.getByTestId("agent-face").getAttribute("data-face-mood")).toBe(
      "work",
    );
  });

  it("an unknown status degrades to idle — never an invented mood", () => {
    render(<CardInner session={sv("s3", "queued")} lane="active" />);
    expect(screen.getByTestId("agent-face").getAttribute("data-face-mood")).toBe(
      "idle",
    );
  });

  it("the chip face is decorative — the agent type must not read 'codercoder'", () => {
    render(<CardInner session={sv("s4", "active")} lane="active" />);
    // The visible label beside the face is the ONLY "coder" text node — the
    // default SVG <title> used to duplicate it into the chip's textContent.
    expect(screen.getAllByText("coder")).toHaveLength(1);
    expectDecorative(screen.getByTestId("agent-face"));
  });
});

/* ------------------------------------------------------------- RoundTable */

const THREAD = {
  id: "t1",
  title: "Panel",
  participants: [
    { key: "builtin:builder", source: "builtin", name: "builder", role: "lead" },
    { key: "dynamic:remy", source: "dynamic", name: "remy", role: "critic" },
  ],
  message_count: 3,
  updated_at: "2026-08-12T10:00:00Z",
  messages: [
    { who: "user", content: "hi panel", at: "2026-08-12T10:00:00Z" },
    { who: "builtin:builder", content: "hello there", at: "2026-08-12T10:00:05Z" },
    {
      who: "dynamic:remy",
      content: "",
      at: "2026-08-12T10:00:09Z",
      error: "remy couldn't answer: provider unreachable",
    },
  ],
};


/* ------------------------------------------------------------- PanelPicker */

