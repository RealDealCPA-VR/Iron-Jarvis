/**
 * v1.360.0 (lane 10B fix round) — ONE name for the user's own model server,
 * on every surface that names the pick.
 *
 * The lane made the reply, the mission screen and the run page call the
 * custom endpoint's "fleet" pick by its machine ("DGX Spark", the label the
 * user gave the fleet node). The review found the user's real install still
 * said "Your model server" on the composer chip, the model menu, the Settings
 * model lists, and "your model server" on the helper card and row: those call
 * sites handed lib/onboarding `modelRowName` / lib/answeredModel
 * `answeredModelName` no catalog, and the custom slot's own rows carry no
 * name — the machine's name lives on its fleet twin (`fleet-custom`).
 *
 * Each surface now hands over the /models rows it already holds (the helper
 * card and row read the one /models reader, lib/useModels). This file feeds
 * the LIVE shape (custom rows with no name; fleet-custom rows named "DGX
 * Spark") through the REAL chat page (chip, provider submenu, filter matches,
 * recent rows), the REAL "Try again with" menu, the REAL helper card
 * (RunResultCard › RunMeta) and row (AgentRunReceipt), the REAL Templates
 * page and the REAL Settings default-model list. The other direction: the
 * same install before the user named the machine (the registry's default
 * label "Custom endpoint" on the twin) reads "Your model server" /
 * "your model server" everywhere, exactly as before.
 *
 * Harness: the last-words-model-rows-v13590 chat harness (only the
 * transport, the stream hook and the daemon context mocked).
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: { getResponses: {} as Record<string, unknown> },
    health: { default_provider: "custom", default_model: "fleet" },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (p: string) => {
    const r = H.api.getResponses[p];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${p}`, 404);
    return JSON.parse(JSON.stringify(r));
  },
  post: async () => ({}),
  put: async (p: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(p);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async () => ({ reply: "ok" }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    refresh: () => {},
    health: {
      status: "ok",
      version: "test",
      default_provider: H.health.default_provider,
      default_model: H.health.default_model,
      providers: [
        { provider: "claude-cli", available: true, class: "cli" },
        { provider: "custom", available: true, class: "local" },
        { provider: "fleet-custom", available: true, class: "local" },
      ],
    },
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
// The Templates page's shell, kept simple (the chat page uses the module's
// other exports, which stay real).
vi.mock("@/components/motion", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/motion")>()),
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/PageHeader")>()),
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1 data-testid="page-title">{title}</h1>
      {actions}
    </div>
  ),
}));

import ChatPage from "@/app/chat/page";
import TemplatesPage from "@/app/templates/page";
import { RunResultCard, type RunResult } from "@/components/chat/RunResultCard";
import { AgentRunReceipt, runModelName } from "@/components/chat/AgentRunReceipt";
import { RegenerateMenu } from "@/components/chat/RegenerateMenu";
import { DefaultModelPicker, modelOptionText } from "@/components/settings/DefaultModelPicker";
import { modelText, modelTitle } from "@/components/ModelRowBits";
import { matchModels, RECENT_MODELS_KEY } from "@/lib/recentModels";
import type { ModelOption } from "@/lib/types";

/** The live /models rows on the user's install (2026-10-19): the custom
 *  slot's rows carry no name; their fleet twin carries the machine's. */
const NAMED: ModelOption[] = [
  { provider: "claude-cli", model: "subscription", kind: "cli", available: true },
  { provider: "custom", model: "fleet", kind: "local", available: true },
  { provider: "custom", model: "glm", kind: "local", available: true },
  { provider: "fleet-custom", model: "fleet", name: "DGX Spark", kind: "local", available: true },
  { provider: "fleet-custom", model: "glm", name: "DGX Spark", kind: "local", available: true },
] as ModelOption[];
/** The same install before the user named the machine: the registry's
 *  default label on the twin is not a name. */
const UNNAMED: ModelOption[] = NAMED.map((r) =>
  r.provider === "fleet-custom" ? { ...r, name: "Custom endpoint" } : r,
) as ModelOption[];

function serve(models: ModelOption[]) {
  H.api.getResponses = {
    "/models": { models },
    "/routing": { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    "/templates": {
      templates: [
        {
          id: "t1",
          name: "Morning brief",
          agent_type: "builder",
          task: "Summarise the inbox",
          description: "",
          provider: "custom",
          model: "fleet",
          created_at: "2026-10-01T00:00:00Z",
        },
      ],
    },
    "/templates/starters": { starters: [] },
    "/templates/suggestions": { suggestions: [] },
    "/agents": { builtin: ["builder"], dynamic: [] },
  };
}

beforeEach(() => {
  serve(NAMED);
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const RESULT: RunResult = {
  found: true,
  session_id: "s1",
  status: "completed",
  task: "summarise the inbox",
  summary: "",
  steps: 2,
  tools_used: [],
  tools_failed: [],
  files_created: [],
  files_changed: [],
  errors: [],
  revertable: 0,
  duration_s: 12,
  outcome: "completed",
  provider: "custom",
  model: "fleet",
  agent_name: null,
  agent_type: "researcher",
  route_note: "",
  activity: [],
} as RunResult;

/* -------------------------------------------------------- the row helpers */

describe("the row helpers hand the catalog to the one rule", () => {
  it("modelText / modelTitle: the machine with the catalog, the menu's words without", () => {
    const row = { provider: "custom", model: "fleet" };
    expect(modelText(row, NAMED)).toBe("DGX Spark");
    expect(modelTitle(row, NAMED)).toBe("fleet");
    expect(modelText(row, UNNAMED)).toBe("Your model server");
    expect(modelText(row)).toBe("Your model server");
    // Any other model keeps its own words.
    expect(modelText({ provider: "custom", model: "glm" }, NAMED)).toBe("glm");
  });

  it("the Settings list's words and the run's words take the catalog too", () => {
    expect(modelOptionText({ provider: "custom", model: "fleet" }, NAMED)).toBe("DGX Spark");
    expect(modelOptionText({ provider: "custom", model: "fleet" }, UNNAMED)).toBe("Your model server");
    expect(runModelName({ provider: "custom", model: "fleet" }, NAMED)).toBe("DGX Spark");
    expect(runModelName({ provider: "custom", model: "fleet" }, UNNAMED)).toBe("your model server");
    expect(runModelName({ provider: "custom", model: "fleet" })).toBe("your model server");
  });

  it("the filter finds the pick by the name it now shows ('DGX'), and only then", () => {
    const hits = matchModels(NAMED, "DGX", 10).map((m) => `${m.provider}::${m.model}`);
    expect(hits).toContain("custom::fleet");
    // A model that is not the alias is found by its own words, never the machine's.
    expect(hits).not.toContain("custom::glm");
    expect(matchModels(UNNAMED, "DGX", 10)).toEqual([]);
  });
});

/* ------------------------------------------------------ the chat page */

async function openMenu() {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.click(await screen.findByTitle("Switch model"));
  return (await screen.findByTestId("model-filter")) as HTMLInputElement;
}

async function openGroup(name: string): Promise<HTMLElement> {
  const menu = screen.getByTestId("model-menu");
  const group = within(menu)
    .getAllByText(name)
    .map((el) => el.closest("button"))
    .find((b): b is HTMLButtonElement => !!b && b.getAttribute("aria-expanded") !== null);
  expect(group, `no group button named ${name}`).toBeTruthy();
  fireEvent.click(group as HTMLButtonElement);
  return await screen.findByTestId("model-sub");
}

describe("the composer's chip and model menu on the live rows", () => {
  it("the chip names the default pick by its machine", async () => {
    render(<ChatPage />);
    const trigger = await screen.findByTitle("Switch model");
    await waitFor(() => expect(trigger.textContent).toContain("DGX Spark"));
    expect(trigger.textContent).not.toMatch(/Your model server|\bfleet\b/);
  });

  it("the custom group's alias row reads 'DGX Spark', the raw id on hover", async () => {
    render(<ChatPage />);
    await openMenu();
    await openGroup("Your model server");
    await waitFor(() => expect(within(screen.getByTestId("model-sub")).getByText("glm")).toBeInTheDocument());
    const sub = screen.getByTestId("model-sub");
    expect(within(sub).getByText("DGX Spark")).toHaveAttribute("title", "fleet");
  });

  it("typing the machine's name finds the pick, and the match reads it", async () => {
    render(<ChatPage />);
    const box = await openMenu();
    fireEvent.change(box, { target: { value: "DGX" } });
    await waitFor(() => {
      const rows = screen.getAllByTestId("model-match");
      const alias = rows.find((r) => within(r).queryAllByText("Your model server").length > 0);
      expect(alias, "the custom slot's fleet row is a match").toBeTruthy();
      expect(within(alias as HTMLElement).getByText("DGX Spark")).toHaveAttribute("title", "fleet");
    });
  });

  it("a recent pick reads the same name", async () => {
    window.localStorage.setItem(RECENT_MODELS_KEY, JSON.stringify(["custom::fleet"]));
    render(<ChatPage />);
    await openMenu();
    const recent = await screen.findByTestId("model-recent");
    await waitFor(() => expect(within(recent).getByText("DGX Spark")).toHaveAttribute("title", "fleet"));
  });

  it("OTHER DIRECTION: a machine nobody named keeps 'Your model server' on the chip and the row", async () => {
    serve(UNNAMED);
    render(<ChatPage />);
    const trigger = await screen.findByTitle("Switch model");
    await waitFor(() => expect(trigger.textContent).toContain("Your model server"));
    expect(trigger.textContent).not.toMatch(/Custom endpoint|\bfleet\b/);
    await openMenu();
    await openGroup("Your model server");
    await waitFor(() => expect(within(screen.getByTestId("model-sub")).getByText("glm")).toBeInTheDocument());
    expect(within(screen.getByTestId("model-sub")).getByText("Your model server")).toHaveAttribute("title", "fleet");
  });
});

/* ------------------------------------------------ "Try again with" menu */

describe("the 'Try again with' menu names the pick the same way", () => {
  function openRegen(models: ModelOption[], current: string) {
    render(<RegenerateMenu models={models} recent={[]} current={current} onRegenerate={() => {}} />);
    fireEvent.click(screen.getByLabelText("Try again with another model"));
    return screen.getAllByTestId("regen-item");
  }

  it("'same model · DGX Spark', and the pick's own row reads 'DGX Spark'", () => {
    let items = openRegen(NAMED, "custom::fleet");
    expect(items[0].textContent).toContain("same model · DGX Spark");
    cleanup();
    items = openRegen(NAMED, "claude-cli::subscription");
    const alias = items.find((it) => it.getAttribute("data-choice") === "custom::fleet") as HTMLElement;
    expect(within(alias).getByText("DGX Spark")).toHaveAttribute("title", "fleet");
  });

  it("OTHER DIRECTION: unnamed, 'same model · Your model server'", () => {
    const items = openRegen(UNNAMED, "custom::fleet");
    expect(items[0].textContent).toContain("same model · Your model server");
  });
});

/* ---------------------------------------------- the helper card and row */

describe("the helper card and the helper row read the one /models reader", () => {
  it("the card under an agent's answer: 'answered by DGX Spark'", async () => {
    render(<RunResultCard result={RESULT} />);
    await waitFor(() => expect(screen.getByTestId("run-result-model").textContent).toBe("answered by DGX Spark"));
    expect(screen.getByTestId("run-result-model").getAttribute("title")).toBe("your own endpoint · fleet");
  });

  it("the settled helper row: 'Ran on DGX Spark'", async () => {
    render(
      <AgentRunReceipt run={{ session: "s1", startedAt: 1, endedAt: 2, steps: 1, provider: "custom", model: "fleet" }} />,
    );
    await waitFor(() => expect(screen.getByTestId("agent-run-model").textContent).toContain("Ran on DGX Spark"));
  });

  it("OTHER DIRECTION: unnamed, 'answered by your model server' / 'Ran on your model server'", async () => {
    serve(UNNAMED);
    render(<RunResultCard result={RESULT} />);
    render(
      <AgentRunReceipt run={{ session: "s2", startedAt: 1, endedAt: 2, steps: 1, provider: "custom", model: "fleet" }} />,
    );
    // Wait for the catalog's answer itself (a named twin would flip the words).
    await waitFor(() => expect(screen.getByTestId("agent-run-model").textContent).toContain("Ran on your model server"));
    expect(screen.getByTestId("run-result-model").textContent).toBe("answered by your model server");
  });
});

/* -------------------------------------------- Settings and model lists */

describe("Settings' default-model list and a template's chip", () => {
  it("the default-model list names the alias row by its machine", async () => {
    render(<DefaultModelPicker provider="custom" value="fleet" onChange={() => {}} />);
    await waitFor(() => {
      const opt = screen.getByRole("option", { name: "DGX Spark" }) as HTMLOptionElement;
      expect(opt.value).toBe("fleet");
    });
    expect(screen.queryByRole("option", { name: "Your model server" })).toBeNull();
  });

  it("OTHER DIRECTION: unnamed, the list says 'Your model server'", async () => {
    serve(UNNAMED);
    render(<DefaultModelPicker provider="custom" value="fleet" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "Your model server" })).toBeInTheDocument());
  });

  it("a saved template's chip: 'DGX Spark' (named) / 'Your model server' (unnamed)", async () => {
    render(<TemplatesPage />);
    await waitFor(() => expect(screen.getByTestId("template-model").textContent?.trim()).toBe("DGX Spark"));
    cleanup();
    serve(UNNAMED);
    render(<TemplatesPage />);
    await waitFor(() => expect(screen.getByTestId("template-model").textContent?.trim()).toBe("Your model server"));
  });
});

/* --------------------------------------- every caller hands the catalog */

describe("no surface names a model row without the catalog it holds (scan)", () => {
  const ROOT = path.join(__dirname, "..");
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = path.join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== "node_modules" && !name.startsWith(".")) walk(full);
      } else if (/\.(tsx?|jsx?)$/.test(name)) {
        files.push(full);
      }
    }
  };
  walk(path.join(ROOT, "app"));
  walk(path.join(ROOT, "components"));

  /** Every call of `fn(` in `src` and whether it passes a second argument
   *  (a top-level comma inside its parentheses). */
  function calls(src: string, fn: string): { line: number; args: number }[] {
    const out: { line: number; args: number }[] = [];
    const rx = new RegExp(`(?<![\\w.])${fn}\\(`, "g");
    let m: RegExpExecArray | null;
    while ((m = rx.exec(src))) {
      // Skip the definition itself ("function modelText(").
      if (/function\s+$/.test(src.slice(Math.max(0, m.index - 12), m.index))) continue;
      let depth = 0;
      let commas = 0;
      let i = m.index + fn.length;
      for (; i < src.length; i++) {
        const c = src[i];
        if (c === "(" || c === "{" || c === "[") depth++;
        else if (c === ")" || c === "}" || c === "]") {
          depth--;
          if (depth === 0) break;
        } else if (c === "," && depth === 1) commas++;
      }
      const inner = src.slice(m.index + fn.length + 1, i).trim();
      const trailing = inner.endsWith(",") ? 1 : 0;
      out.push({ line: src.slice(0, m.index).split("\n").length, args: inner ? commas + 1 - trailing : 0 });
    }
    return out;
  }

  it("modelText, modelTitle, modelRowName, modelPickWords and modelOptionText always get the catalog", () => {
    const misses: string[] = [];
    let seen = 0;
    for (const f of files) {
      const src = readFileSync(f, "utf8").replace(/\r\n/g, "\n");
      for (const fn of ["modelText", "modelTitle", "modelRowName", "modelPickWords", "modelOptionText"]) {
        for (const c of calls(src, fn)) {
          seen++;
          // ModelRowBits defines the forwarding pair; its inner calls pass it on.
          if (c.args < 2) misses.push(`${path.relative(ROOT, f)}:${c.line} ${fn}`);
        }
      }
    }
    // Anti-vacuity: the surfaces this fix wired are really scanned.
    expect(seen).toBeGreaterThanOrEqual(30);
    expect(misses).toEqual([]);
  });

  it("only the reply receipt's own fallback names 'who answered' with an empty catalog", () => {
    const misses: string[] = [];
    for (const f of files) {
      const src = readFileSync(f, "utf8").replace(/\r\n/g, "\n");
      const rel = path.relative(ROOT, f).replace(/\\/g, "/");
      if (/answeredModelName\([^\n;]*?,\s*\[\]\s*\)/.test(src) && rel !== "components/chat/TurnReceipt.tsx") {
        misses.push(rel);
      }
    }
    // TurnReceipt's `answeredName` is the fallback for a caller that gave no
    // name: the daemon's route.label already names the machine there.
    expect(misses).toEqual([]);
  });
});
