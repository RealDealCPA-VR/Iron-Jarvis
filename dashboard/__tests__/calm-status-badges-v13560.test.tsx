/**
 * v1.356.0, wave 6 lane 6C (G80): calm primitives are the default.
 *
 * Badge and ConfirmButton had two looks: older pages drew a bordered, tinted
 * pill and a hairline-bordered button, calm pages a dot-with-words chip and a
 * quiet ghost — and the Schedules list said its status as coloured text. Now
 * the calm look IS the default (components/ui.tsx), the old look is
 * `variant="bordered"` and is passed only where it is deliberate (Build's
 * boxed "Close this terminal?" dialog), and the Schedules status renders
 * through the calm Badge with lib/statusWords.
 *
 * ACCEPTANCE: status badges look the same on Sessions, Tools and Schedules.
 * The three REAL pages are rendered (only the transport and app-wide hooks
 * are doubles) and their status chips are compared: the same element shape,
 * the same classes, the tone on the dot, the words neutral.
 */

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError, responses: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: async (p: string) => {
    const r = api.responses[p.split("?")[0]];
    return r === undefined ? {} : JSON.parse(JSON.stringify(r));
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ models: [], usable: [], data: { models: [] }, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ health: null, checking: false, online: true, refresh: () => {} }),
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
vi.mock("@/components/NewSessionForm", () => ({ NewSessionForm: () => null, NEW_SESSION_TASK_ID: "new-task" }));
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
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout", "whileHover", "whileTap", "whileInView", "viewport"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  const motion = new Proxy({} as Record<string, unknown>, {
    get: (_t, tag) => {
      const key = String(tag);
      if (!cache.has(key)) cache.set(key, tagFor(key));
      return cache.get(key);
    },
  });
  // v1.250.0 mock contract: every framer-motion mock exports `m`.
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    useReducedMotion: () => true,
  };
});

import { __resetApiCache } from "@/lib/apiCache";
import { CALM_BADGE } from "@/components/ui";

const SessionsPage = (await import("@/app/sessions/page")).default;
const SchedulesPage = (await import("@/app/schedules/page")).default;
const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;

beforeEach(() => {
  api.responses = {};
  __resetApiCache();
  window.localStorage.clear();
});
afterEach(() => cleanup());

const W = { timeout: 5_000 };

/** The chip's class tokens without the per-use case rule (keepCase drops
 *  `capitalize`; it is about the WORDS, not the look). */
const look = (el: Element) =>
  (el.getAttribute("class") ?? "")
    .split(/\s+/)
    .filter((c) => c && c !== "capitalize")
    .sort();
const dotOf = (el: Element) => (el.querySelector("span")?.getAttribute("class") ?? "").split(/\s+/).sort();

async function sessionsChip(): Promise<HTMLElement> {
  api.responses["/sessions"] = {
    sessions: [
      {
        id: "s-fail",
        task: "task s-fail",
        agent_type: "builder",
        provider: "mock",
        model: "mock-model",
        status: "failed",
        workspace_path: "C:/w",
        summary: "",
        created_at: "2026-10-18T10:00:00Z",
        finished_at: null,
      },
    ],
  };
  api.responses["/projects"] = { projects: [] };
  render(<SessionsPage />);
  const link = await screen.findByRole("link", { name: "task s-fail" }, W);
  const row = link.closest("tr") as HTMLElement;
  return within(row).getByText("Failed");
}

async function schedulesChip(): Promise<HTMLElement> {
  api.responses["/schedules"] = {
    schedules: [
      {
        name: "nightly",
        cron: "0 2 * * *",
        kind: "task",
        enabled: true,
        next_run: null,
        last_run: "2026-10-18T02:00:00Z",
        trigger_type: "cron",
        payload_json: JSON.stringify({ task: "Review the week." }),
        project_id: null,
        last_status: "error",
        last_detail: "The run failed: the endpoint is down",
        last_session_id: "",
      },
    ],
  };
  api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
  render(<SchedulesPage />);
  return (await screen.findByTestId("schedule-status-nightly", {}, W)) as HTMLElement;
}

async function toolsChip(): Promise<HTMLElement> {
  api.responses["/mcp/servers"] = {
    servers: [
      {
        name: "brave",
        command: "npx",
        args: ["-y", "@example/brave"],
        tools_loaded: 0,
        tool_names: [],
        last_error: "it stopped",
        state: "failed",
        failures: 0,
        reason: "it stopped",
        fix: null,
        stderr_tail: "",
        env_keys: [],
        secret_keys: [],
      },
    ],
  };
  api.responses["/mcp/catalog"] = { catalog: [] };
  api.responses["/tools/custom"] = { tools: [] };
  api.responses["/connectors"] = { connectors: [], categories: [] };
  render(<ToolsPage />);
  return (await screen.findByTestId("mcp-state-brave", {}, W)) as HTMLElement;
}

describe("ACCEPTANCE: status badges look the same on Sessions, Tools and Schedules", () => {
  it("one chip: the calm shell, the tone on the dot, the words neutral", async () => {
    const sessions = await sessionsChip();
    const sessionsLook = { shell: look(sessions), dot: dotOf(sessions), tone: sessions.getAttribute("data-tone") };
    cleanup();
    __resetApiCache();
    const schedules = await schedulesChip();
    const schedulesLook = { shell: look(schedules), dot: dotOf(schedules), tone: schedules.getAttribute("data-tone") };
    cleanup();
    __resetApiCache();
    const tools = await toolsChip();
    const toolsLook = { shell: look(tools), dot: dotOf(tools), tone: tools.getAttribute("data-tone") };

    // Every one is the calm Badge.
    for (const [where, el] of [["sessions", sessions], ["schedules", schedules], ["tools", tools]] as const) {
      expect(el.getAttribute("data-badge-variant"), where).toBe("calm");
    }
    // The same shell on all three pages: CALM_BADGE, nothing else.
    const calm = CALM_BADGE.split(/\s+/).filter(Boolean).sort();
    expect(sessionsLook.shell).toEqual(calm);
    expect(schedulesLook.shell).toEqual(calm);
    expect(toolsLook.shell).toEqual(calm);
    // A failure reads red on Sessions and Schedules (the one status table),
    // the Tools pack that did not start amber — each on the dot, same shape.
    expect(sessionsLook.tone).toBe("red");
    expect(schedulesLook.tone).toBe("red");
    expect(sessionsLook.dot).toEqual(schedulesLook.dot);
    expect(toolsLook.dot.filter((c) => !/^bg-/.test(c))).toEqual(sessionsLook.dot.filter((c) => !/^bg-/.test(c)));
    // No border, no fill, no glow on any of them.
    for (const shell of [sessionsLook.shell, schedulesLook.shell, toolsLook.shell]) {
      expect(shell.filter((c) => /^(?:[a-z-]+:)*border(?:-|$)/.test(c) || /^bg-/.test(c))).toEqual([]);
    }
  });

  it("the Schedules status speaks the one status table's word and keeps its old test id and data", async () => {
    const chip = await schedulesChip();
    expect(chip.textContent).toBe("Failed");
    expect(chip.getAttribute("data-status")).toBe("error");
    await waitFor(() => expect(screen.getByTestId("schedule-detail-nightly").textContent).toContain("the endpoint is down"));
  });
});

describe("the bordered look stays only where it is deliberate", () => {
  const src = (rel: string) => readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

  it("components/ui.tsx: calm is the default for Badge and ConfirmButton", () => {
    const ui = src("components/ui.tsx");
    expect(ui).toMatch(/export type BadgeVariant = "default" \| "calm" \| "bordered";/);
    expect(ui).toMatch(/export type ConfirmVariant = "default" \| "calm" \| "bordered";/);
    // Only `bordered` reaches the old look (both primitives).
    expect(ui.match(/if \(variant !== "bordered"\) \{/g) ?? []).toHaveLength(2);
  });

  it("the ONE bordered use in the app is Build's 'Close this terminal?' confirm", () => {
    const root = path.join(__dirname, "..");
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const ent of readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, ent.name);
        if (ent.isDirectory()) {
          if (ent.name !== "node_modules" && !ent.name.startsWith(".")) walk(full);
        } else if (/\.tsx?$/.test(ent.name) && readFileSync(full, "utf8").includes('variant="bordered"')) {
          hits.push(path.relative(root, full).replace(/\\/g, "/"));
        }
      }
    };
    walk(path.join(root, "app"));
    walk(path.join(root, "components"));
    expect(hits).toEqual(["app/terminals/page.tsx"]);
    const page = src("app/terminals/page.tsx");
    const at = page.indexOf('variant="bordered"');
    expect(page.slice(at, at + 400)).toContain('label="Close terminal"');
  });
});
