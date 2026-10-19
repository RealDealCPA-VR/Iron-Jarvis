/**
 * v1.354.0 lane 4D — Settings and panels without engine words (G59), Settings
 * rows named like the pages they open (G67), destructive actions handled one
 * way (G68). Every check drives the REAL component or helper; only the API,
 * the daemon hook and neighbours that are not under test are stood in.
 *
 *   G59  Settings › Maintenance offers "Restart Iron Jarvis" (two presses,
 *        POST /shutdown as before), and a project board that fails to load
 *        says "The board couldn't load. Try again." — no HTTP code and no
 *        "daemon" in the visible text; the code rides the line's title, and
 *        "Try again" really re-reads.
 *   G67  Everything's Directory → its old address → the Settings row → the
 *        page: all four say "Directory".
 *   G68  Removing a writing sample takes two presses; no native confirm is
 *        asked by a delete (source pin, with the known prompts listed).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readdirSync, statSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { copyPieces, readSrc, DASHBOARD_ROOT } from "./helpers/dashGuard";

const H = vi.hoisted(() => {
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
    gets: {} as Record<string, unknown>,
    calls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    dels: [] as string[],
    redirects: [] as string[],
    refresh: () => {},
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: async (p: string) => {
    H.calls.push(p);
    const r = p in H.gets ? H.gets[p] : H.gets[p.split("?")[0]];
    if (typeof r === "function") {
      const v = (r as () => unknown)();
      if (v instanceof Error) throw v;
      return v;
    }
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  post: async (p: string, body?: unknown) => {
    H.posts.push({ path: p, body });
    return {};
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async (p: string) => {
    H.dels.push(p);
    return {};
  },
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: H.refresh }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    H.redirects.push(to);
    throw new Error("NEXT_REDIRECT");
  },
  usePathname: () => "/settings",
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
// Not under test here: the Maintenance card's backup/restore list (its own
// reads), the old full form, and the board's lanes.
vi.mock("@/components/settings/MaintenanceTools", () => ({ MaintenanceTools: () => null }));
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form" />,
  LocalCapabilitiesCard: () => null,
  ContextWindowsCard: () => null,
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => <div data-testid="kanban-stub" /> }));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, reload: () => {} }) }));

import { __resetApiCache } from "@/lib/apiCache";
import { MaintenanceCard } from "@/components/settings/MaintenanceCard";
import { SettingsHome } from "@/components/settings/SettingsHome";
import TrainPage from "@/components/settings/pages/TrainPage";
import { ProjectSurface } from "@/components/project/ProjectSurfaces";
import { couldntLoad, couldntWhileNoAnswer, failureDetail, NOT_RESPONDING } from "@/lib/loadFailed";
import { RESTART_WORDS } from "@/lib/restartWords";
import { relocatedRouteOf, settingsRowLabel } from "@/lib/settingsRows";
import { RELOCATED } from "@/lib/relocated";
import { surfaceFor, surfacesIn } from "@/lib/surfaces";
import Marketplace from "@/app/marketplace/page";

const W = { timeout: 10_000 };

beforeEach(() => {
  __resetApiCache();
  H.gets = {};
  H.calls = [];
  H.posts = [];
  H.dels = [];
  H.redirects = [];
  window.history.replaceState({}, "", "/");
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

/* ================================================================== G59 */

describe("G59: the words a panel says when a read fails", () => {
  it("one sentence shape: '<Thing> couldn't load.' + the next step; the code is detail only", () => {
    expect(couldntLoad("The board", { status: 500, message: "boom" })).toBe("The board couldn't load. Try again.");
    expect(couldntLoad("The board", { status: 0, message: "offline" })).toBe(
      "The board couldn't load. Iron Jarvis isn't responding.",
    );
    expect(NOT_RESPONDING).toBe("Iron Jarvis isn't responding.");
    expect(failureDetail({ status: 500, message: "Internal Server Error" })).toBe("HTTP 500: Internal Server Error");
    expect(failureDetail({ status: 404, message: "" })).toBe("HTTP 404");
    // Nothing answered: there is no code to show.
    expect(failureDetail({ status: 0, message: "Failed to fetch" })).toBeUndefined();
    expect(failureDetail(null)).toBeUndefined();
    expect(couldntWhileNoAnswer("upload")).toBe("Couldn't upload. Iron Jarvis isn't responding.");
  });
});

describe("G59: a project board that fails to load", () => {
  const BOARD = "/sessions?project_id=proj_1";
  const line = (text: string) => (_: string, el: Element | null) => el?.tagName === "P" && el.textContent === text;

  it("says 'The board couldn't load. Try again.' with no HTTP code or daemon in the text, and Try again re-reads", async () => {
    let fail = true;
    H.gets[BOARD] = () => (fail ? new H.FakeApiError("Internal Server Error", 500) : { sessions: [] });
    render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);

    const p = await screen.findByText(line("The board couldn't load. Try again."), {}, W);
    expect(p.textContent).not.toMatch(/HTTP|daemon|500/i);
    // The code is kept for whoever is troubleshooting, on hover only.
    expect(p.getAttribute("title")).toBe("HTTP 500: Internal Server Error");

    // "Try again" is a press that does what it says: it re-reads AT ONCE
    // (counted right after the press, so the board's own 4 s poll cannot be
    // what read again).
    const reads = H.calls.filter((c) => c === BOARD).length;
    fail = false;
    await act(async () => {
      fireEvent.click(within(p).getByRole("button", { name: "Try again" }));
    });
    expect(H.calls.filter((c) => c === BOARD).length).toBeGreaterThan(reads);
    expect(await screen.findByText(/No sessions in this project yet/, {}, W)).toBeInTheDocument();
  });

  it("the media list (read once, never polled) says the same and its Try again re-reads", async () => {
    const MEDIA = "/creative/items?project_id=proj_1&limit=200";
    let fail = true;
    H.gets[MEDIA] = () => (fail ? new H.FakeApiError("Not Found", 404) : { items: [] });
    render(<ProjectSurface projectId="proj_1" hasRoot view="media" />);
    const p = await screen.findByText(line("Media couldn't load. Try again."), {}, W);
    expect(p.getAttribute("title")).toBe("HTTP 404: Not Found");
    expect(p.textContent).not.toMatch(/HTTP|daemon|404/i);
    fail = false;
    fireEvent.click(within(p).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/No media in this project yet/, {}, W)).toBeInTheDocument();
  });

  it("when nothing answers it says so in the app's name, never 'the daemon'", async () => {
    H.gets[BOARD] = () => new H.FakeApiError("Failed to fetch", 0);
    render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);
    const p = await screen.findByText(line("The board couldn't load. Iron Jarvis isn't responding."), {}, W);
    expect(p.getAttribute("title")).toBeNull();
    expect(within(p).queryByRole("button")).toBeNull();
  });

  it("the Projects page's own board says the same words (jsdom cannot mount that page: source pin)", () => {
    const src = readSrc("app/projects/[id]/page.tsx");
    const board = src.slice(src.indexOf("function ProjectBoard("), src.indexOf("function ActivityList("));
    expect(board).toContain('<LoadFailedLine thing="The board" error={error} />');
    expect(board).toContain('<LoadFailedLine thing="The board" error={error} onRetry={refreshAll} />');
    expect(board).not.toMatch(/daemon/i);
  });
});

describe("G59: Settings › Maintenance offers 'Restart Iron Jarvis'", () => {
  it("the heading and the button name the app; two presses still POST /shutdown", async () => {
    const { container } = render(<MaintenanceCard />);
    const btn = screen.getByRole("button", { name: "Restart Iron Jarvis" });
    expect(screen.getAllByText("Restart Iron Jarvis")).toHaveLength(2); // the heading and the button
    expect(btn.getAttribute("title")).toBe(RESTART_WORDS.title);
    expect(container.textContent).not.toMatch(/daemon/i);
    expect(container.innerHTML).not.toMatch(/daemon/i); // titles too

    fireEvent.click(btn);
    expect(H.posts).toEqual([]); // the first press only arms it
    fireEvent.click(screen.getByRole("button", { name: "Confirm restart" }));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toEqual(["/shutdown"]), W);
  });

  it("the older full form's Maintenance card uses the SAME words (one table, both cards)", () => {
    const legacy = readSrc("components/settings/pages/LegacySettingsForm.tsx");
    expect(legacy).toContain("<SectionLabel>{RESTART_WORDS.label}</SectionLabel>");
    expect(legacy).toContain("label={RESTART_WORDS.label}");
    expect(legacy).toContain("title={RESTART_WORDS.title}");
    expect(legacy).toContain("{RESTART_WORDS.needed}");
    expect(RESTART_WORDS.label).toBe("Restart Iron Jarvis");
    expect(RESTART_WORDS.title).toContain("Iron Jarvis stops tidily and the desktop app starts it again");
    for (const words of Object.values(RESTART_WORDS)) expect(words).not.toMatch(/daemon/i);
  });
});

/** The files this lane reworded: no user-visible string in them names the
 *  engine. Module paths ("@/lib/daemon") and export names
 *  ("DaemonTokenCard", a camelCase identifier) are code, not copy, and are
 *  skipped; comments are never read (copyPieces). */
const isCode = (text: string) =>
  /^(@\/|\.\/|\.\.\/)/.test(text) || (/^[A-Za-z_$][\w$]*$/.test(text) && /[a-z][A-Z]/.test(text));
const G59_FILES = [
  "components/settings/MaintenanceCard.tsx",
  "components/settings/pages/LegacySettingsForm.tsx",
  "components/settings/SettingsHome.tsx",
  "components/agents/mission/MissionComposer.tsx",
  "components/chat/DocPreview.tsx",
  "app/creative/page.tsx",
  "app/projects/[id]/page.tsx",
  "components/project/ProjectSurfaces.tsx",
  "components/workflow/SavedWorkflows.tsx",
  "components/project/ProjectSchedules.tsx",
  "components/settings/pages/ToolsPage.tsx",
  "components/workflow/WorkflowCanvas.tsx",
  "components/settings/pages/TrainPage.tsx",
  "components/LoadFailedLine.tsx",
  "lib/loadFailed.ts",
  "lib/restartWords.ts",
];

describe("G59: no engine words in the copy of these panels", () => {
  it.each(G59_FILES)("%s: no user-visible string says 'daemon'", (rel) => {
    const pieces = copyPieces(rel).filter((p) => !isCode(p.text));
    expect(pieces.length, `anti-vacuity: ${rel} has copy`).toBeGreaterThan(0);
    expect(pieces.filter((p) => /daemon/i.test(p.text)).map((p) => `${rel}:${p.line} ${p.text.trim()}`)).toEqual([]);
  });

  it("anti-vacuity: the reader does see a daemon word in copy", () => {
    const probe = [
      'const a = "Restart daemon";',
      "// the daemon in a comment",
      'import x from "@/lib/daemon";',
      'const b = named(load, "DaemonTokenCard");',
      'const c = "Daemon";',
    ].join("\n");
    const hits = copyPieces("probe.tsx", probe).filter((p) => !isCode(p.text) && /daemon/i.test(p.text));
    expect(hits.map((h) => h.text)).toEqual(["Restart daemon", "Daemon"]);
  });
});

/* ================================================================== G67 */

describe("G67: Settings rows are named like the pages they open", () => {
  it("every row that holds a relocated page takes that page's nav name", () => {
    const settingsRoutes = Object.entries(RELOCATED).filter(([, to]) => to.base === "/settings");
    expect(settingsRoutes.length).toBeGreaterThanOrEqual(9); // anti-vacuity
    for (const [route, to] of settingsRoutes) {
      const section = to.extra.section;
      expect(relocatedRouteOf(section)).toBe(route);
      expect(settingsRowLabel(section), section).toBe(surfaceFor(route)!.label);
    }
    // The four the audit named, and the two names that also disagreed.
    expect(settingsRowLabel("connections-accounts")).toBe("Connections");
    expect(settingsRowLabel("connections-apps")).toBe("Directory");
    expect(settingsRowLabel("connections-secrets")).toBe("Secrets");
    expect(settingsRowLabel("permissions-tools")).toBe("Tools");
    expect(settingsRowLabel("memory-profile")).toBe("You");
    expect(settingsRowLabel("memory-train")).toBe("Train Jarvis on me");
    // A panel that never had an address keeps its own words.
    expect(settingsRowLabel("system-maintenance", "Maintenance")).toBe("Maintenance");
  });

  it("no relocated row carries a name of its own in SettingsHome (one source for the word)", () => {
    const src = readSrc("components/settings/SettingsHome.tsx");
    for (const [, to] of Object.entries(RELOCATED).filter(([, t]) => t.base === "/settings")) {
      const at = src.indexOf(`id: "${to.extra.section}"`);
      expect(at, to.extra.section).toBeGreaterThan(-1);
      const entry = src.slice(at, src.indexOf("Comp:", at));
      expect(entry, to.extra.section).not.toMatch(/\blabel:/);
    }
  });

  it("clicking Directory in Everything lands on a Settings row and page both called Directory", async () => {
    // 1. Everything › Setup lists the Directory.
    const entry = surfacesIn("setup").find((s) => s.label === "Directory");
    expect(entry?.href).toBe("/marketplace");
    // 2. Its old address redirects into Settings (the real route module).
    await expect(Marketplace({ searchParams: Promise.resolve({}) })).rejects.toThrow("NEXT_REDIRECT");
    const to = H.redirects.at(-1)!;
    expect(to).toBe("/settings?section=connections-apps");
    // 3. Settings opens there: the row and the embedded page say Directory.
    H.gets["/settings/schema"] = {
      groups: [
        { id: "models", label: "Models", description: "Which AI answers." },
        { id: "connections", label: "Connections", description: "Accounts, keys, apps and alerts." },
      ],
      settings: [],
      secrets: [],
    };
    H.gets["/settings/values"] = { values: {} };
    H.gets["/config/ledger"] = { changes: [] };
    H.gets["/connectors"] = { connectors: [], categories: [] };
    H.gets["/mcp/import/sources"] = { sources: [] };
    window.history.replaceState({}, "", to);
    render(<SettingsHome />);
    const sub = await screen.findByTestId("settings-sub-connections-apps", {}, W);
    const row = within(sub).getAllByRole("button")[0];
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(row.querySelector("span span")?.textContent).toBe("Directory");
    const heading = await within(sub).findByRole("heading", { name: "Directory" }, W);
    expect(heading.tagName).toBe("H2");
    // The old third name is gone from the screen.
    expect(screen.queryByText("Apps & tools")).toBeNull();
  });

  it("a person who learned the old name still finds the row by searching it", async () => {
    H.gets["/settings/schema"] = {
      groups: [{ id: "connections", label: "Connections", description: "Accounts." }],
      settings: [],
      secrets: [],
    };
    H.gets["/settings/values"] = { values: {} };
    H.gets["/config/ledger"] = { changes: [] };
    window.history.replaceState({}, "", "/settings");
    render(<SettingsHome />);
    const box = await screen.findByLabelText(/search settings/i, {}, W);
    fireEvent.change(box, { target: { value: "apps & tools" } });
    const sub = await screen.findByTestId("settings-sub-connections-apps", {}, W);
    expect(within(sub).getAllByRole("button")[0].textContent).toContain("Directory");
  });
});

/* ================================================================== G68 */

describe("G68: removing a writing sample takes two presses", () => {
  it("the first press arms 'Remove sample?'; only the second deletes", async () => {
    H.gets["/profile/training"] = {
      about: true,
      voice_card: false,
      samples: 1,
      sample_chars: 420,
      memory_bases: 1,
      memory_items: 0,
      search_roots: 0,
      projects: 0,
    };
    H.gets["/profile/samples"] = {
      samples: [{ id: "smp_1", label: "Email to Dana", chars: 420, excerpt: "Hi Dana, thanks for…" }],
      total_chars: 420,
      max_samples: 20,
      min_chars_to_derive: 400,
    };
    render(<TrainPage />);
    const row = await screen.findByRole("group", { name: "Email to Dana" }, W);
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(H.dels).toEqual([]);
    const armed = within(row).getByRole("button", { name: "Remove sample?" });
    expect(armed.getAttribute("data-armed")).toBe("true");
    fireEvent.click(armed);
    await waitFor(() => expect(H.dels).toEqual(["/profile/samples/smp_1"]), W);
  });
});

/** Every native confirm() call in the dashboard's own code, by file. A
 *  destructive action uses ConfirmButton (two presses, the one way); these
 *  are the prompts that stay native, each with its reason. A NEW call — in a
 *  delete handler above all — fails here until it is a ConfirmButton or is
 *  listed with its reason. */
const KNOWN_NATIVE_CONFIRMS: Record<string, { count: number; why: string }> = {
  "app/chat/page.tsx": { count: 1, why: "undo of a chat's file change (confirmUndoPrompt says what it will do)" },
  "components/terminal/PaneChat.tsx": { count: 1, why: "undo of a pane's file change (paneUndoPrompt)" },
  // v1.355.0 integrate: "End this session?" became a ConfirmButton.
  "app/creative/page.tsx": { count: 1, why: "publish to a public link (confirmPublish)" },
  "components/chat/DocPreview.tsx": { count: 1, why: "replace a file of the same name when saving a copy" },
  // v1.355.0 integrate: removing / hiding a memory-graph link is a picked
  // link + ConfirmButton in the rail now (no native dialog left there).
};

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(path.join(DASHBOARD_ROOT, dir))) {
    const rel = `${dir}/${name}`;
    const st = statSync(path.join(DASHBOARD_ROOT, rel));
    if (st.isDirectory()) out.push(...sourceFiles(rel));
    else if (/\.(ts|tsx)$/.test(name) && !name.endsWith(".d.ts")) out.push(rel);
  }
  return out;
}

/** Calls to the native prompt: `confirm(…)`, `window.confirm(…)`,
 *  `globalThis.confirm(…)` — read through the TypeScript parser, so a word
 *  in a comment or a string never counts. */
function nativeConfirms(rel: string, src = readSrc(rel)): number {
  const file = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, rel.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  let n = 0;
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node)) {
      const callee = node.expression;
      if (ts.isIdentifier(callee) && callee.text === "confirm") n += 1;
      else if (
        ts.isPropertyAccessExpression(callee) &&
        callee.name.text === "confirm" &&
        ts.isIdentifier(callee.expression) &&
        ["window", "globalThis", "self"].includes(callee.expression.text)
      )
        n += 1;
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return n;
}

describe("G68: destructive actions are handled one way (source pin)", () => {
  it("no native confirm() beyond the known undo / publish / replace prompts", () => {
    const files = ["app", "components", "lib"].flatMap(sourceFiles);
    expect(files.length).toBeGreaterThan(200); // anti-vacuity: the walk found the app
    const found: Record<string, number> = {};
    for (const rel of files) {
      const n = nativeConfirms(rel);
      if (n) found[rel] = n;
    }
    const known = Object.fromEntries(Object.entries(KNOWN_NATIVE_CONFIRMS).map(([k, v]) => [k, v.count]));
    expect(found, "a destructive action uses ConfirmButton (two presses); list a non-destructive prompt here with its reason").toEqual(known);
  });

  it("the canvas's workflow delete and the sample delete are ConfirmButtons, never a native prompt", () => {
    for (const rel of ["components/workflow/WorkflowCanvas.tsx", "components/settings/pages/TrainPage.tsx"]) {
      expect(nativeConfirms(rel), rel).toBe(0);
    }
    const canvas = readSrc("components/workflow/WorkflowCanvas.tsx");
    expect(canvas).toMatch(/<ConfirmButton\s+variant="calm"\s+onConfirm=\{\(\) => deleteDef\(d\.name\)\}/);
    const train = readSrc("components/settings/pages/TrainPage.tsx");
    expect(train).toMatch(/<ConfirmButton\s+variant="calm"\s+onConfirm=\{\(\) => removeSample\(s\.id\)\}/);
  });

  it("anti-vacuity: the reader counts every shape of the native prompt and nothing else", () => {
    const probe = [
      'if (!window.confirm("Delete?")) return;',
      'confirm("x");',
      'globalThis.confirm("y");',
      "// window.confirm in a comment",
      'const s = "window.confirm(";',
      "onConfirm();",
      "props.confirm();",
    ].join("\n");
    expect(nativeConfirms("probe.ts", probe)).toBe(3);
  });
});
