/**
 * v1.356.0 (G77) — labels say what they lead to.
 *
 * ACCEPTANCE (dashboard half): hovering "low trust" says what the run cannot
 * do. Also: a setting's "after a restart" tag says where the restart is (and,
 * given the page's own jump, opens it), and the Sessions page's clear button
 * names everything it deletes ("Clear finished" also removed failed and
 * stopped sessions). The Status map's Models/Browser words are the daemon's
 * (tests/test_status_map_words_v13560.py); the deny-set agreement is pinned
 * in tests/test_trust_words_v13560.py.
 *
 * Real components throughout; only the network is mocked.
 */

import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
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
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  sseUrl: (p: string) => p,
  get: (path: string) => {
    const r = H.responses[path];
    return r === undefined ? Promise.reject(new H.FakeApiError(`unmocked GET ${path}`, 404)) : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    if (path === "/sessions/clear") return Promise.resolve({ cleared: 3 });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (H.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (H.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }) }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], loading: false, error: null, reload: () => {} }) }));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: null, refresh: () => {}, epoch: 0 }),
}));
vi.mock("@/components/NewSessionForm", () => ({ NewSessionForm: () => null, NEW_SESSION_TASK_ID: "new-task" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/sessions",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
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
    AnimatePresence: ({ children }: { children?: ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import TrustChip from "@/components/TrustChip";
import { CardInner } from "@/components/kanban/SessionCard";
import {
  MAINTENANCE_PLACE,
  RESTART_TAG_LINK_TITLE,
  RESTART_TAG_TITLE,
  SettingRow,
  type SchemaSetting,
} from "@/components/settings/SettingRow";
import SessionsPage from "@/app/sessions/page";
import { LOW_TRUST_CANNOT, LOW_TRUST_FAMILIES, lowTrustTitle } from "@/lib/trustWords";
import type { SessionView } from "@/lib/types";

const REASON = "started from an inbound email message";

function sv(over: Partial<SessionView> = {}): SessionView {
  return {
    id: "s-1",
    task: "triage the inbox",
    agent_type: "coder",
    provider: "mock",
    model: "mock-model",
    status: "running",
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-10-01T10:00:00Z",
    finished_at: null,
    ...over,
  } as SessionView;
}

beforeEach(() => {
  H.responses = {};
  H.posts.length = 0;
});

afterEach(() => {
  cleanup();
});

/* ============================================================= low trust */

describe("hovering 'low trust' says what the run cannot do", () => {
  it("the hover names settings, schedules, rules, connections and memory after the daemon's reason", () => {
    render(<TrustChip trust="low" reason={REASON} />);
    const chip = screen.getByTestId("trust-chip");
    const title = chip.getAttribute("title") ?? "";
    expect(title.startsWith(`${REASON}. `)).toBe(true);
    for (const word of ["settings", "schedules", "rules", "connections", "memory"]) {
      expect(title).toContain(word);
    }
    expect(title).toContain("can't");
    expect(title).toContain("It can still read, search and answer.");
  });

  it("a screen reader hears the same words; the visible chip still reads 'low trust'", () => {
    render(<TrustChip trust="low" reason={REASON} />);
    const chip = screen.getByTestId("trust-chip");
    const sr = chip.querySelector(".sr-only");
    expect(sr?.textContent).toContain(LOW_TRUST_CANNOT);
    // Everything that is not screen-reader-only is unchanged.
    const visible = Array.from(chip.childNodes)
      .filter((n) => !(n instanceof HTMLElement && n.classList.contains("sr-only")))
      .map((n) => n.textContent)
      .join("")
      .trim();
    expect(visible).toBe("low trust");
  });

  it("with no reason it still says what the run cannot do; a tainted run keeps 'since'", () => {
    expect(lowTrustTitle("", "")).toBe(`This run is in low trust. ${LOW_TRUST_CANNOT}`);
    expect(lowTrustTitle(REASON, "10:05")).toBe(`${REASON} — since 10:05. ${LOW_TRUST_CANNOT}`);
  });

  it("the session board's card wears the same hover", () => {
    render(<CardInner session={sv({ origin: "comm:email", trust: "low", trust_reason: REASON })} lane="active" />);
    expect(screen.getByTestId("trust-chip").getAttribute("title")).toBe(`${REASON}. ${LOW_TRUST_CANNOT}`);
  });

  it("CONTROL: full trust still renders nothing", () => {
    const { container } = render(<TrustChip trust="full" reason="x" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("every family the sentence names has its tools listed (the deny set is pinned in Python)", () => {
    const words = LOW_TRUST_FAMILIES.map((f) => f.words);
    expect(words).toEqual(
      expect.arrayContaining(["settings", "schedules", "rules", "connections", "memory"]),
    );
    for (const f of LOW_TRUST_FAMILIES) expect(f.tools.length).toBeGreaterThan(0);
  });
});

/* ===================================================== after a restart */

const RESTART_ROW: SchemaSetting = {
  key: "sandbox_runtime",
  label: "Sandbox runtime",
  group: "system",
  section: "Advanced",
  type: "enum",
  options: [
    { value: "auto", label: "Auto" },
    { value: "docker", label: "Docker" },
  ],
  restart: true,
};

describe("'after a restart' says where the restart is", () => {
  it("with the page's jump it is a button that opens Settings › System › Maintenance", () => {
    const open = vi.fn();
    render(<SettingRow def={RESTART_ROW} value="auto" changed={false} onChange={() => {}} onOpenMaintenance={open} />);
    const tag = screen.getByRole("button", { name: "after a restart" });
    expect(tag.getAttribute("title")).toBe(RESTART_TAG_LINK_TITLE);
    expect(RESTART_TAG_LINK_TITLE).toContain(MAINTENANCE_PLACE);
    expect(RESTART_TAG_LINK_TITLE).toContain("Restart Iron Jarvis");
    fireEvent.click(tag);
    expect(open).toHaveBeenCalledTimes(1);
    // The look is the quiet warn word it always was.
    expect(tag.className).toContain("text-tone-warn");
    expect(tag.className).toContain("text-[11px]");
  });

  it("without it, the tag names the place in words (never a /settings?section= link)", () => {
    const { container } = render(
      <SettingRow def={RESTART_ROW} value="auto" changed={false} onChange={() => {}} />,
    );
    const tag = screen.getByTestId("setting-sandbox_runtime-restart");
    expect(tag.tagName).toBe("SPAN");
    expect(tag.textContent).toBe("after a restart");
    expect(tag.getAttribute("title")).toBe(RESTART_TAG_TITLE);
    expect(RESTART_TAG_TITLE).toContain("Settings › System › Maintenance");
    expect(container.querySelector('a[href*="section="]')).toBeNull();
  });

  it("CONTROL: a setting that applies at once has no tag", () => {
    render(
      <SettingRow def={{ ...RESTART_ROW, restart: false }} value="auto" changed={false} onChange={() => {}} />,
    );
    expect(screen.queryByTestId("setting-sandbox_runtime-restart")).toBeNull();
  });
});

/* ====================================================== Sessions: clear */

describe("the Sessions page's clear button names everything it deletes", () => {
  it("reads 'Clear finished, failed and stopped', asks first, and clears those three", async () => {
    H.responses["/sessions"] = { sessions: [sv()] };
    H.responses["/projects"] = { projects: [] };
    render(<SessionsPage />);
    const clear = screen.getByRole("button", { name: /Clear finished, failed and stopped/ });
    expect(clear.getAttribute("title")).toMatch(/finished, failed or was stopped/);
    expect(screen.queryByRole("button", { name: /^Clear finished$/ })).toBeNull();
    fireEvent.click(clear);
    expect(H.posts.some((p) => p.path === "/sessions/clear")).toBe(false); // two presses
    fireEvent.click(screen.getByRole("button", { name: /Delete them all\?/ }));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/sessions/clear")?.body).toEqual({
        statuses: ["completed", "failed", "cancelled"],
      }),
    );
    expect(await screen.findByText("Cleared 3 finished, failed or stopped sessions.")).toBeInTheDocument();
  });
});
