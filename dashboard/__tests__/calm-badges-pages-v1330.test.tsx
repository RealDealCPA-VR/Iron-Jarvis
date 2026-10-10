/**
 * v1.330.0 (calm chat wave 11, M4): the calm ConfirmButton and Badge (wave 10
 * L2 made them for Connections) on every other calm page, and the last two
 * copies of the no-dash walker read the shared one.
 *
 * The wave 10 audit found the calm pages still drawing the default (bordered,
 * tinted) Badge and ConfirmButton: the Workflows run history and step chips,
 * the canvas's run strip, the Agents page's inbox chip, a custom or remote
 * agent's Delete, a remote's kind chip, the built-ins' fallback chips and a
 * project's Completed outcome. They are the calm variant now (no border, no
 * fill, the dot carries the tone; Delete is a quiet ghost that fills on
 * hover and still takes two presses). The remote row's hand-rolled bordered
 * "disabled" pill is a calm Badge, and an agent's model is quiet mono words,
 * so a row has one look. Build keeps ONE default use on purpose (the close
 * dialog; build-calm-files-v1329 names it and says why).
 *
 * agents-team-calm-v1329 and connections-calm-k2-v1329 each carried their own
 * copy of copyPieces/asides. They import __tests__/helpers/dashGuard.ts now
 * (which also catches a lone dash that a `{" "}` sibling turns into an
 * aside), with the same file lists.
 */

import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  dels: [] as string[],
}));

vi.mock("@/lib/useApi", () => {
  const read = () => ({ data: null, error: null, loading: false, reload: () => {} });
  return { useApi: read, usePolledApi: read };
});

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
    get: (p: string) => Promise.resolve(p === "/tools" ? { tools: [] } : {}),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: (p: string) => {
      hooks.dels.push(p);
      return Promise.resolve({});
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
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover", "layout"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

import {
  BuiltinFaces,
  RemoteAgentsSection,
  YourAgentsSection,
  type DynamicAgentFull,
} from "@/components/agents/SetupCard";
import type { RemoteAgentInfo } from "@/components/agents/identity";
import { AssignmentRow } from "@/components/agents/AgentInbox";
import { CompletedList } from "@/components/agents/world/CompletedList";
import type { Assignment } from "@/lib/types";
import { asides } from "./helpers/dashGuard";
import { calmUses, uncalm, type KeptDefault } from "./helpers/calmVariant";

beforeEach(() => {
  hooks.dels = [];
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const tokens = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
const BORDER = /^(?:[a-z-]+:)*border(?:-|$)/;
const noBorder = (el: Element) => tokens(el).filter((c) => BORDER.test(c));

/** A calm Badge: the calm marker, no border, no fill, and its dot. */
function expectCalmBadge(chip: HTMLElement, dot: string) {
  expect(chip.getAttribute("data-badge-variant")).toBe("calm");
  expect(noBorder(chip)).toEqual([]);
  expect(tokens(chip).filter((c) => /^bg-/.test(c))).toEqual([]);
  expect(tokens(chip.querySelector("span"))).toContain(dot);
}

/* ================================================= the agent rows, rendered */

const SKEPTIC: DynamicAgentFull = {
  name: "skeptic",
  description: "challenges assumptions",
  base_type: "builder",
  tools: [],
  effective_tools: ["read_file"],
  provider: "fleet-lab",
  model: "glm",
} as DynamicAgentFull;

const HERMES: RemoteAgentInfo = {
  name: "hermes",
  base_url: "http://192.168.1.50:8080/run",
  kind: "http-task",
  enabled: false,
  has_credential: true,
  inbound_enabled: false,
  model: "fable-1",
} as RemoteAgentInfo;

describe("the Agents page's rows draw the calm Badge and ConfirmButton", () => {
  it("a custom agent's Delete is a calm ghost that still takes two presses; its model is quiet words", async () => {
    render(
      <YourAgentsSection
        dynamic={[SKEPTIC]}
        models={[]}
        faces={{}}
        facesSupported={false}
        onChanged={() => {}}
        onFaceChanged={() => {}}
      />,
    );
    const del = screen.getByTitle('Delete agent "skeptic"');
    expect(del.getAttribute("data-confirm-variant")).toBe("calm");
    expect(noBorder(del)).toEqual([]);
    expect(tokens(del)).toContain("hover:bg-white/[0.06]");
    fireEvent.click(del);
    expect(del.textContent).toBe("Confirm?");
    expect(del.getAttribute("data-armed")).toBe("true");
    expect(hooks.dels).toEqual([]);
    fireEvent.click(del);
    await waitFor(() => expect(hooks.dels).toEqual(["/agents/skeptic"]));

    const model = screen.getByText("fleet-lab · glm");
    expect(noBorder(model)).toEqual([]);
    expect(tokens(model).filter((c) => /^bg-/.test(c))).toEqual([]);
    expect(tokens(model)).toEqual(expect.arrayContaining(["font-mono", "text-[11px]", "text-zinc-400"]));
  });

  it("a remote's kind and disabled chips are calm Badges, its Delete a calm ghost; one look in the row", async () => {
    render(
      <RemoteAgentsSection remotes={[HERMES]} faces={{}} facesSupported={false} onChanged={() => {}} onFaceChanged={() => {}} />,
    );
    const row = screen.getByTitle('Remove remote agent "hermes"').closest("li") as HTMLElement;
    expectCalmBadge(within(row).getByText("http-task"), "bg-accent");
    const off = within(row).getByText("disabled");
    expectCalmBadge(off, "bg-zinc-500");
    expect(tokens(off)).not.toContain("capitalize");
    const model = within(row).getByText("fable-1");
    expect(noBorder(model)).toEqual([]);
    // Nothing in the row is a bordered box any more.
    const boxed = Array.from(row.querySelectorAll("*")).filter((el) => noBorder(el).length > 0);
    expect(boxed.map((el) => el.textContent)).toEqual([]);
    const del = within(row).getByTitle('Remove remote agent "hermes"');
    expect(del.getAttribute("data-confirm-variant")).toBe("calm");
    fireEvent.click(del);
    fireEvent.click(del);
    await waitFor(() => expect(hooks.dels).toEqual(["/agents/remote/hermes"]));
  });

  it("the built-ins' fallback chips (no face routes) are calm Badges", () => {
    render(<BuiltinFaces builtin={["builder", "planner"]} faces={{}} facesSupported={false} onFaceChanged={() => {}} />);
    for (const name of ["builder", "planner"]) expectCalmBadge(screen.getByText(name), "bg-accent");
  });
});

/* ============================================ the inbox and Completed lists */

const assignment = (over: Partial<Assignment>): Assignment =>
  ({
    id: "a1",
    project_id: null,
    assignee: "builder",
    task: "Draft the supplier price list",
    title: "Draft the supplier price list",
    priority: 0,
    status: "queued",
    source: "user",
    reason: "",
    payload: {},
    idempotency_key: null,
    coalesced_count: 0,
    attempts: 0,
    failure_count: 0,
    blocked_reason: null,
    held_reason: null,
    depth: 0,
    session_id: null,
    last_error: null,
    created_at: null,
    claimed_at: null,
    started_at: null,
    finished_at: null,
    updated_at: null,
    ...over,
  }) as Assignment;

describe("the inbox row and a project's Completed list draw calm chips", () => {
  it.each([
    ["queued", "queued", "bg-zinc-500"],
    ["done", "completed", "bg-tone-success"],
    ["failed", "failed", "bg-tone-danger"],
  ] as const)("an inbox row %s: a calm chip reading %s, dot %s", (status, word, dot) => {
    render(<AssignmentRow assignment={assignment({ status })} onChanged={() => {}} />);
    const row = screen.getByTestId(`inbox-${status}-a1`);
    // The tone is the dot's (statusTone of the word), never a tinted pill.
    expectCalmBadge(within(row).getByText(word), dot);
  });

  it("a finished item's outcome is a calm chip with the tone on its dot", () => {
    render(
      <CompletedList
        projectId="p1"
        items={[{ id: "s1", title: "Price list", agent: "builder", finished_at: null, outcome: "completed", files: [] }]}
      />,
    );
    const item = screen.getByTestId("world-completed-item-s1");
    expectCalmBadge(within(item).getByText("completed"), "bg-tone-success");
  });
});

/* ===================================================== the calm-variant reader */

describe("the calm-variant reader (helpers/calmVariant.ts)", () => {
  it("finds every Badge and ConfirmButton, and reports the default ones and a spread", () => {
    const probe = [
      'const a = <Badge value="Ready" tone="green" />;',
      "const b = <ConfirmButton onConfirm={() => void go()} label=\"Delete\" />;",
      'const c = <ConfirmButton variant="default" onConfirm={go} />;',
      'const d = <Badge {...rest} variant="calm" value="x" />;',
      'const e = <ConfirmButton variant="calm" onConfirm={go} label="Delete" />;',
      'const f = <Badge variant="calm" value="Ready" />;',
      "// <Badge value=\"in a comment\" />",
      'const g = "<Badge value=\\"in a string\\" />";',
      "const h = <BadgeCheck size={13} />;",
    ].join("\n");
    expect(calmUses("probe.tsx", probe)).toHaveLength(6);
    expect(uncalm("probe.tsx", probe).map((s) => s.split(" ")[0])).toEqual([
      "probe.tsx:1",
      "probe.tsx:2",
      "probe.tsx:3",
      "probe.tsx:4",
    ]);
  });

  it("a kept use is excused only by its file, element and label", () => {
    const kept: KeptDefault[] = [{ rel: "probe.tsx", tag: "ConfirmButton", label: "Close", why: "a boxed dialog" }];
    const probe = [
      '<ConfirmButton onConfirm={go} label="Close" />;',
      '<ConfirmButton onConfirm={go} label="Delete" />;',
      '<Badge value="Close" />;',
    ].join("\n");
    expect(uncalm("probe.tsx", probe, kept).map((s) => s.split(" ")[0])).toEqual(["probe.tsx:2", "probe.tsx:3"]);
    expect(uncalm("other.tsx", probe, kept)).toHaveLength(3);
  });
});

/* ================================================ the files this wave touched */

const CALM_PAGES = [
  "app/workflows/page.tsx",
  "components/workflow/WorkflowCanvas.tsx",
  "components/agents/SetupCard.tsx",
  "components/agents/AgentInbox.tsx",
  "components/agents/world/CompletedList.tsx",
];

describe("every use the audit named is calm now", () => {
  it.each(CALM_PAGES)("%s: every Badge and ConfirmButton says variant=\"calm\"", (rel) => {
    expect(calmUses(rel).length, rel).toBeGreaterThan(0);
    expect(uncalm(rel)).toEqual([]);
  });

  it("Build keeps exactly one default use, the close dialog's button, and nothing else", () => {
    const build = calmUses("app/terminals/page.tsx");
    expect(build.filter((u) => !u.calm).map((u) => `${u.tag}:${u.label}`)).toEqual(["ConfirmButton:Close terminal"]);
  });
});

/* ============================== the last two copies of the no-dash walker */

const HERE = __dirname;
const readTest = (name: string) => readFileSync(path.join(HERE, name), "utf8").replace(/\r\n/g, "\n");

describe("agents-team-calm-v1329 and connections-calm-k2-v1329 read the shared no-dash reader", () => {
  const GUARDS = ["agents-team-calm-v1329.test.tsx", "connections-calm-k2-v1329.test.tsx"];

  it.each(GUARDS)("%s imports asides from the shared reader and keeps no walker of its own", (name) => {
    const src = readTest(name);
    expect(src).toMatch(/import \{[^}]*\basides\b[^}]*\} from "\.\/helpers\/dashGuard";/);
    for (const own of [
      "createSourceFile",
      "forEachChild",
      "function copyPieces",
      "function asides",
      "const ASIDE",
      "const DASH_ONLY",
      'from "typescript"',
    ]) {
      expect(src, `${name} still has ${own}`).not.toContain(own);
    }
  });

  it("their file lists did not shrink", () => {
    const team = readTest("agents-team-calm-v1329.test.tsx");
    for (const rel of [
      "components/agents/AgentsModal.tsx",
      "components/agents/AgentInbox.tsx",
      "components/agents/AgentFiles.tsx",
      "components/agents/AgentCoach.tsx",
      "components/agents/AgentPortrait.tsx",
      "components/agents/PortraitCropper.tsx",
      "components/agents/SetupCard.tsx",
      "components/agents/teamLook.ts",
      "components/agents/mission/TeamScreen.tsx",
      "components/agents/world/WorldBoard.tsx",
    ])
      expect(team, rel).toContain(`"${rel}",`);
    expect(team).toContain("expect(TEAM_FILES.flatMap((rel) => asides(rel))).toEqual([]);");
    const conn = readTest("connections-calm-k2-v1329.test.tsx");
    expect(conn).toContain('"components/settings/pages/ConnectionsPage.tsx",');
    expect(conn).toContain('const CONN_DIR = "components/connections";');
    expect(conn).toMatch(/readdirSync\(path\.join\(ROOT, CONN_DIR\)\)/);
    expect(conn).toContain('"app/fleet/page.tsx",');
    expect(conn).toContain('"lib/fleet.ts",');
    expect(conn).toContain("expect(COPY_FILES.flatMap((rel) => asides(rel))).toEqual([]);");
  });

  it("the shared reader also catches a lone dash a {\" \"} sibling makes an aside (coverage grew)", () => {
    const probe = ['const f = <p>run <b>x</b>{" "}', '  —{" "}', '  {"the run is waiting."}</p>;'].join("\n");
    expect(asides("probe.tsx", probe)).toHaveLength(1);
  });
});
