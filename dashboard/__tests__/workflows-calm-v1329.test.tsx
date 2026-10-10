/**
 * v1.329.0 — calm chat wave 9, K3: the workflow editor (/workflows) has the
 * calm look of the chat.
 *
 * The final audit found /workflows still in the pre-calm look next to the
 * calm chat: a boxed "Describe a workflow" card, an editor card with bordered
 * Load / Save / Schedule / Add step and a filled accent Run workflow, and a
 * boxed Saved workflows card (plus boxed Templates, Build with chat and Run
 * history cards), with dash asides in the page's own copy.
 *
 * Now, like the chat:
 *  - the Describe box is the page's ONE card (drawn like the chat composer),
 *    and its Build it stays quiet until there is something to build;
 *  - the editor, the saved list, Templates, Build with chat and Run history
 *    are plain sections: a quiet sentence-case <h2> over one hairline;
 *  - the toolbar is borderless ghosts that fill on hover, with ONE filled
 *    primary (Run workflow);
 *  - the run strip speaks in tone tokens;
 *  - the copy on screen has no dash asides.
 *
 * The REAL page and the REAL canvas render here (React Flow is stubbed, as in
 * canvas-v1170: jsdom cannot lay it out).
 */

import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { responses: {} as Record<string, unknown>, FakeApiError };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, subtitle }: { title: string; subtitle?: string }) => (
    <header>
      <h1>{title}</h1>
      <p data-testid="page-subtitle">{subtitle}</p>
    </header>
  ),
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (t: string, c: string) => (t ? `${t} ${c}` : c),
}));
vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  const { createElement } = await import("react");
  return {
    ...actual,
    ReactFlow: ({ children }: { children?: ReactNode }) =>
      createElement("div", { "data-testid": "rf-canvas" }, children),
    ReactFlowProvider: ({ children }: { children?: ReactNode }) => createElement("div", null, children),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    useReactFlow: () => ({ fitView: async () => false }),
  };
});
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import WorkflowsPage from "@/app/workflows/page";
import { RunProgress } from "@/components/workflow/WorkflowCanvas";
import type { WorkflowRun } from "@/lib/types";
import { readdirSync } from "node:fs";
import path from "node:path";
import { calmUses, uncalm } from "./helpers/calmVariant";
import { DASHBOARD_ROOT } from "./helpers/dashGuard";

const RUNS_PATH = "/workflows/runs?limit=50";

beforeEach(() => {
  window.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  localStorage.clear();
  api.responses = {};
});
afterEach(cleanup);

const tokens = (el: Element) => String(el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/** Any literal Tailwind hue class (the tone tokens are the calm way). */
const LITERAL_HUE =
  /(?:^|\s)(?:[a-z]+:)*(?:text|bg|border|ring)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}/;

async function renderEmpty() {
  api.responses["/workflows"] = { workflows: [] };
  api.responses[RUNS_PATH] = { runs: [] };
  const view = render(<WorkflowsPage />);
  // Wait for the thing the assertions read: the Describe box (it shows only
  // once /workflows has answered with none) and the editor.
  await screen.findByTestId("workflow-describe-card");
  await screen.findByTestId("workflow-editor");
  await screen.findByRole("heading", { name: /^Saved workflows/ });
  return view;
}

describe("the Describe box is the page's ONE card", () => {
  it("no card-surface anywhere on the page; the Describe box is the one composer-style card", async () => {
    const { container } = await renderEmpty();
    expect(container.querySelectorAll(".card-surface")).toHaveLength(0);
    const card = screen.getByTestId("workflow-describe-card");
    expect(tokens(card)).toContain("bg-ink-800");
    // The chat composer's hairline edge (lib/composerChips COMPOSER_CARD_EDGE).
    expect(card.className).toContain("shadow-[0_0_0_0.5px_rgb(var(--white)/0.12)");
    // Exactly one element on the page wears that edge.
    const edged = [...container.querySelectorAll("[class]")].filter((el) =>
      String(el.getAttribute("class")).includes("shadow-[0_0_0_0.5px_rgb(var(--white)/0.12)"),
    );
    expect(edged).toHaveLength(1);
  });

  it("Build it is quiet until there is something to build, then the accent", async () => {
    await renderEmpty();
    const build = screen.getByTestId("workflow-describe-build") as HTMLButtonElement;
    expect(build.disabled).toBe(true);
    expect(tokens(build)).not.toContain("btn-accent");
    const box = screen.getByLabelText(/Describe a workflow/);
    fireEvent.change(box, { target: { value: "Research a topic and brief me" } });
    await waitFor(() => expect(tokens(screen.getByTestId("workflow-describe-build"))).toContain("btn-accent"));
    expect((screen.getByTestId("workflow-describe-build") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("the editor and the lists are plain sections", () => {
  it.each(["Saved workflows", "Templates", "Build with chat", "Run history"])(
    "%s: a quiet sentence-case h2 over one hairline, no card",
    async (name) => {
      await renderEmpty();
      const h = screen.getByRole("heading", { name: new RegExp(`^${name}`) });
      expect(h.tagName).toBe("H2");
      expect(tokens(h)).not.toContain("uppercase");
      expect(tokens(h)).toContain("text-zinc-400");
      const section = h.closest("section")!;
      expect(section).not.toBeNull();
      expect(tokens(section)).not.toContain("card-surface");
      expect(section.className).not.toMatch(/(^|\s)bg-/);
      const body = h.closest("header")!.nextElementSibling!;
      expect(tokens(body)).toContain("border-t");
      expect(tokens(body)).toContain("hairline");
    },
  );

  it("the editor is a section with no card surface; the graph sits in one hairline frame", async () => {
    await renderEmpty();
    const editor = screen.getByTestId("workflow-editor");
    expect(editor.tagName).toBe("SECTION");
    expect(tokens(editor)).not.toContain("card-surface");
    // The v1.316.0 height cap is unchanged.
    expect(tokens(editor)).toContain("h-[min(64vh,680px)]");
    expect(tokens(editor)).toContain("max-sm:h-[min(80vh,680px)]");
    const frame = screen.getByTestId("workflow-canvas-frame");
    expect(tokens(frame)).toContain("hairline");
    expect(frame.className).not.toMatch(/(^|\s)bg-/);
    expect(within(frame).getByTestId("rf-canvas")).toBeInTheDocument();
  });

  it("template rows are rows over hairlines, not boxed tiles", async () => {
    await renderEmpty();
    const list = screen.getByTestId("workflow-templates");
    expect(tokens(list)).toContain("divide-y");
    const rows = within(list).getAllByRole("listitem");
    expect(rows.length).toBeGreaterThan(0);
    for (const li of rows) {
      expect(tokens(li).some((t) => t === "border" || t.startsWith("border-"))).toBe(false);
      expect(tokens(li).some((t) => t.startsWith("bg-"))).toBe(false);
    }
  });
});

describe("the toolbar is quiet ghosts with ONE filled primary", () => {
  it("Load, Save, Schedule… and Add step have no border and no fill at rest, and fill on hover", async () => {
    await renderEmpty();
    const editor = screen.getByTestId("workflow-editor");
    const controls = [
      within(editor).getByRole("button", { name: /Load/ }),
      within(editor).getByRole("button", { name: /^Save$/ }),
      within(editor).getByRole("link", { name: /Schedule…/ }),
      within(editor).getByRole("button", { name: /Add step/ }),
    ];
    for (const el of controls) {
      const t = tokens(el);
      expect(t, el.textContent ?? "").not.toContain("btn-ghost");
      expect(t.some((x) => x === "border" || /^border-(?!transparent)/.test(x)), el.textContent ?? "").toBe(false);
      expect(t.some((x) => x.startsWith("bg-")), el.textContent ?? "").toBe(false);
      expect(t, el.textContent ?? "").toContain("hover:bg-white/[0.06]");
    }
  });

  it("Run workflow is the one filled primary on the page (Build it is quiet while the box is empty)", async () => {
    const { container } = await renderEmpty();
    const filled = [...container.querySelectorAll(".btn-accent")];
    expect(filled).toHaveLength(1);
    expect(filled[0].textContent).toMatch(/Run workflow/);
  });
});

describe("the copy on screen is plain, with no dash asides", () => {
  it("the header, the Templates line and Build with chat read as sentences", async () => {
    const { container } = await renderEmpty();
    expect(screen.getByTestId("page-subtitle").textContent).toBe(
      "Wire agents into a visual, multi-step workflow, then run it. Describe one below, or send a terminal session here with its → Workflow button.",
    );
    expect(screen.getByText(/^No saved workflows yet\. Start from one of these\./)).toBeInTheDocument();
    expect(
      screen.getByText(/^Describe a process and the agent builds the steps into the editor above\. For example,/),
    ).toBeInTheDocument();
    expect(container.textContent ?? "").not.toMatch(/\s[—–]\s/);
  });
});

describe("the run strip speaks in tone tokens", () => {
  it("chips, the ask and a failed step use tone-* and no literal hue", () => {
    const run = {
      id: "r1",
      workflow_name: "Monthly close",
      status: "waiting",
      steps_json: JSON.stringify([
        { name: "Gather", agent: "planner", task: "x" },
        { name: "Draft", agent: "builder", task: "y" },
        { name: "Check", kind: "ask", message: "Send it?" },
      ]),
      outputs_json: JSON.stringify({
        Gather: { status: "completed", summary: "ok" },
        Draft: { status: "failed", summary: "it broke" },
      }),
      waiting_json: JSON.stringify({ index: 2, step: "Check", question: "Send it?", options: ["Yes", "No"] }),
    } as unknown as WorkflowRun;
    const { container } = render(<RunProgress run={run} onCancel={() => {}} cancelling={false} />);
    const strip = screen.getByTestId("run-progress");
    expect(tokens(strip)).not.toContain("card-surface");
    expect(strip.className).not.toMatch(/(^|\s)(border|bg-)/);
    expect(container.querySelector(".text-tone-success")).not.toBeNull();
    expect(container.querySelector(".text-tone-danger")).not.toBeNull();
    expect(container.querySelector(".text-tone-warn")).not.toBeNull();
    const all = [...container.querySelectorAll("[class]")].map((el) => String(el.getAttribute("class")));
    expect(all.filter((c) => LITERAL_HUE.test(c))).toEqual([]);
    // Anti-vacuity: the ask and its answers are really here.
    expect(within(screen.getByTestId("run-ask-gate")).getByText("Send it?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yes" })).toBeInTheDocument();
  });

  it("control: the detector flags a literal hue", () => {
    expect(LITERAL_HUE.test("rounded border-rose-500/25 text-rose-200")).toBe(true);
    expect(LITERAL_HUE.test("hover:bg-amber-500/20")).toBe(true);
    expect(LITERAL_HUE.test("text-tone-danger bg-tone-warn/10")).toBe(false);
  });
});

/* v1.330.0 (calm chat wave 11, M4): the run history's status chip, a run's
   step chips and the canvas's run strip were the default (bordered, tinted)
   Badge. Every <Badge> and <ConfirmButton> on the Workflows page and in the
   workflow editor's files is the calm variant now: no border, no fill, the
   dot carries the tone. A default one (or a spread that could hide the
   variant) fails here. */
describe("the Workflows page draws every Badge and ConfirmButton calm", () => {
  const WORKFLOW_DIR = "components/workflow";
  const FILES = [
    "app/workflows/page.tsx",
    ...readdirSync(path.join(DASHBOARD_ROOT, WORKFLOW_DIR))
      .filter((f) => f.endsWith(".tsx"))
      .map((f) => `${WORKFLOW_DIR}/${f}`),
  ];

  it.each(FILES)("%s has no default Badge or ConfirmButton", (rel) => {
    expect(uncalm(rel)).toEqual([]);
  });

  it("the guard sees the page's and the canvas's chips (anti-vacuity)", () => {
    const n = (rel: string) => calmUses(rel).length;
    // The run history chip + a run's step chip (page); the run strip's three
    // status chips + each step's chip (canvas).
    expect(n("app/workflows/page.tsx")).toBeGreaterThanOrEqual(2);
    expect(n("components/workflow/WorkflowCanvas.tsx")).toBeGreaterThanOrEqual(4);
  });

  it("the run strip's status chips render calm: no border, the dot carries the tone", () => {
    const run = {
      id: "r2",
      workflow_name: "Monthly close",
      status: "waiting",
      steps_json: JSON.stringify([{ name: "Gather", agent: "planner", task: "x" }]),
      outputs_json: JSON.stringify({ Gather: { status: "completed", summary: "ok" } }),
      waiting_json: JSON.stringify({ index: 0, step: "Gather", question: "Go on?", options: ["Yes"] }),
    } as unknown as WorkflowRun;
    render(<RunProgress run={run} onCancel={() => {}} cancelling={false} />);
    const strip = screen.getByTestId("run-progress");
    const chips = [...strip.querySelectorAll("[data-badge-variant]")];
    // The run's own chip and the step's chip.
    expect(chips.length).toBeGreaterThanOrEqual(2);
    for (const chip of chips) {
      expect(chip.getAttribute("data-badge-variant")).toBe("calm");
      expect(tokens(chip).filter((c) => /^border/.test(c))).toEqual([]);
    }
    const waiting = within(strip).getByText("waiting on you");
    expect(waiting.getAttribute("data-badge-variant")).toBe("calm");
    expect(tokens(waiting.querySelector("span") as Element)).toContain("bg-tone-warn");
  });
});
