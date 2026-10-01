/**
 * v1.297.0 — the skill curator: provenance badges, pin / archive on every
 * card, and the collapsed Curator panel (candidates, archive, sweep).
 *
 * WHAT THESE TESTS GUARD — the WIRE and the honest degradations:
 *  - "Agent-made" / "From a proposal" badges from `created_by`;
 *  - the pin toggles POST …/pin or …/unpin by the row's `pinned`, the icon is
 *    filled and aria-pressed when pinned;
 *  - Archive confirms inline ("Sure?") and only then POSTs …/archive;
 *  - the panel is collapsed by default, the dry-run box is CHECKED by
 *    default, Sweep now POSTs /skills/curator/run {dry_run} and shows the
 *    result sentence (dry and real differ), Restore POSTs …/restore;
 *  - without a curator (404 / no data) the panel AND the per-card actions
 *    are absent — their routes do not exist on that daemon.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  errors: {} as Record<string, { status: number; message: string }>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  postResults: {} as Record<string, unknown>,
  postErrors: {} as Record<string, { status: number; message: string }>,
  reloads: [] as string[],
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: path ? (hooks.errors[path] ?? null) : null,
    loading: false,
    reload: () => {
      hooks.reloads.push(path ?? "");
    },
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: path ? (hooks.errors[path] ?? null) : null,
    loading: false,
    reload: () => {
      hooks.reloads.push(path ?? "");
    },
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
    API_BASE: "",
    ijToken: () => "",
    get: (path: string) => Promise.resolve(hooks.api[path] ?? {}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    post: (path: string, body?: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      if (path in hooks.postErrors) {
        const e = hooks.postErrors[path];
        return Promise.reject(new ApiError(e.message, e.status));
      }
      return Promise.resolve(path in hooks.postResults ? hooks.postResults[path] : { ok: true });
    },
  };
});

vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import SkillsPage from "@/app/skills/page";
import { SkillCurator, curatorHeadline, settingPairs, sweepSentence } from "@/components/skills/SkillCurator";
import type { SkillCuratorView } from "@/lib/types";

const SKILLS = {
  skills: [
    { name: "ledger-sum", description: "sum a ledger", source: "custom", created_by: "agent", pinned: false },
    { name: "tidy-notes", description: "tidy notes", source: "custom", created_by: "proposal", pinned: true },
    { name: "mine", description: "my own", source: "user", created_by: "user", pinned: false },
  ],
  counts: { custom: 2, user: 1 },
};

// The daemon's REAL shape: `SkillCurator.overview()` is `status()` with the
// two count fields replaced by the lists — every status field sits FLAT on
// the view. (The first cut of this file fed a nested `status` object the
// daemon never sends, and the headline/badge/settings read it: green tests,
// a panel that always said "never swept" on the real daemon.)
function curatorView(over: Partial<SkillCuratorView> = {}): SkillCuratorView {
  return {
    enabled: true,
    last_sweep_at: null,
    last_result: null,
    settings: { idle_days: 30, min_age_days: 7 },
    archive_dir: "C:\ij\skills\.archive",
    candidates: [
      {
        name: "ledger-sum",
        created_by: "agent",
        use_count: 1,
        inject_count: 4,
        last_used_at: null,
        age_days: 40,
        idle_days: 33,
        reason: "unused for 33 days",
      },
    ],
    archived: [{ name: "old-one", archived_at: "2026-09-01T00:00:00Z", description: "an old skill" }],
    ...over,
  };
}

beforeEach(() => {
  hooks.api = { "/skills": SKILLS };
  hooks.errors = {};
  hooks.posts = [];
  hooks.postResults = {};
  hooks.postErrors = {};
  hooks.reloads = [];
});

afterEach(() => cleanup());

/* ------------------------------------------------------------- helpers --- */

describe("SkillCurator helpers", () => {
  it("curatorHeadline counts the lists and names the last sweep", () => {
    expect(curatorHeadline(curatorView())).toBe("Curator — 1 candidate · 1 archived · never swept");
    const v = curatorView({ candidates: [], archived: [], last_sweep_at: new Date().toISOString() });
    expect(curatorHeadline(v)).toMatch(/^Curator — 0 candidates · 0 archived · last sweep /);
  });

  it("sweepSentence: a dry run reads would_archive (its archived is [] by contract), a real one says what went and the backup", () => {
    // The daemon's dry run: `archived: []`, the candidates under `would_archive`.
    expect(sweepSentence({ dry_run: true, archived: [], would_archive: ["a"], kept: ["b", "c"], backup: null }, true)).toBe(
      "Dry run: would archive 1 skill (a) and keep 2.",
    );
    expect(sweepSentence({ dry_run: true, archived: [], would_archive: [], kept: ["b"], backup: null }, true)).toBe(
      "Dry run: nothing would be archived; 1 kept.",
    );
    expect(sweepSentence({ archived: ["a", "z"], kept: ["b"], backup: "C:\\bk\\skills-1.zip" }, false)).toBe(
      "Archived 2 skills (a, z); 1 kept. Backup at C:\\bk\\skills-1.zip.",
    );
  });

  it("settingPairs flattens keys and non-string values", () => {
    expect(settingPairs({ idle_days: 30, kinds: ["agent"] })).toEqual([
      ["idle days", "30"],
      ["kinds", '["agent"]'],
    ]);
  });
});

/* ---------------------------------------------------------- the page --- */

describe("skills page — badges, pin, archive", () => {
  it("shows Agent-made and From a proposal, nothing extra for the user's own", () => {
    hooks.api["/skills/curator"] = curatorView();
    render(<SkillsPage />);
    expect(screen.getByText("Agent-made")).toBeInTheDocument();
    expect(screen.getByText("From a proposal")).toBeInTheDocument();
    expect(screen.getAllByTestId("skill-provenance")).toHaveLength(2);
  });

  it("the pin POSTs pin for an unpinned row and unpin for a pinned one; pinned is filled + pressed", async () => {
    hooks.api["/skills/curator"] = curatorView();
    render(<SkillsPage />);
    const pinA = screen.getByTestId("skill-pin-ledger-sum");
    const pinB = screen.getByTestId("skill-pin-tidy-notes");
    expect(pinA).toHaveAttribute("aria-pressed", "false");
    expect(pinB).toHaveAttribute("aria-pressed", "true");
    expect(pinA.querySelector("svg")?.getAttribute("fill")).toBe("none");
    expect(pinB.querySelector("svg")?.getAttribute("fill")).toBe("currentColor");
    fireEvent.click(pinA);
    await waitFor(() => expect(hooks.reloads).toContain("/skills"));
    expect(hooks.posts).toEqual([{ path: "/skills/curator/ledger-sum/pin", body: undefined }]);
    expect(hooks.reloads).toContain("/skills/curator");
    hooks.reloads = [];
    fireEvent.click(pinB);
    await waitFor(() => expect(hooks.reloads).toContain("/skills"));
    expect(hooks.posts[1]).toEqual({ path: "/skills/curator/tidy-notes/unpin", body: undefined });
  });

  it("Archive arms first ('Sure?'), then POSTs …/archive", async () => {
    hooks.api["/skills/curator"] = curatorView();
    render(<SkillsPage />);
    const btn = screen.getByTestId("skill-archive-ledger-sum");
    fireEvent.click(btn);
    expect(hooks.posts).toEqual([]);
    expect(btn.textContent).toBe("Sure?");
    fireEvent.click(btn);
    await waitFor(() => expect(hooks.reloads).toContain("/skills"));
    expect(hooks.posts).toEqual([{ path: "/skills/curator/ledger-sum/archive", body: undefined }]);
  });

  it("a failed POST shows the daemon's message", async () => {
    hooks.api["/skills/curator"] = curatorView();
    hooks.postErrors["/skills/curator/ledger-sum/pin"] = { status: 500, message: "skills folder read-only" };
    render(<SkillsPage />);
    fireEvent.click(screen.getByTestId("skill-pin-ledger-sum"));
    await waitFor(() => expect(screen.getByText("skills folder read-only")).toBeInTheDocument());
    expect(hooks.reloads).toEqual([]);
  });

  it("without a curator (404): no panel, no pin, no archive — the list still renders", () => {
    hooks.errors["/skills/curator"] = { status: 404, message: "Not Found" };
    render(<SkillsPage />);
    expect(screen.getByText("ledger-sum")).toBeInTheDocument();
    expect(screen.queryByTestId("curator-panel")).toBeNull();
    expect(screen.queryByTestId("skill-pin-ledger-sum")).toBeNull();
    expect(screen.queryByTestId("skill-archive-ledger-sum")).toBeNull();
  });
});

/* --------------------------------------------------------- the panel --- */

describe("the Curator panel", () => {
  it("is collapsed by default to one line, opens on click, dry-run CHECKED by default", () => {
    hooks.api["/skills/curator"] = curatorView();
    render(<SkillsPage />);
    const panel = screen.getByTestId("curator-panel");
    expect(panel.textContent).toContain("Curator — 1 candidate · 1 archived · never swept");
    expect(within(panel).queryByTestId("curator-candidate-ledger-sum")).toBeNull();
    expect(within(panel).queryByTestId("curator-dry-run")).toBeNull();
    fireEvent.click(within(panel).getByTestId("curator-toggle"));
    expect(within(panel).getByTestId("curator-dry-run")).toBeChecked();
    const cand = within(panel).getByTestId("curator-candidate-ledger-sum");
    expect(cand.textContent).toContain("unused for 33 days");
    expect(within(panel).getByTestId("curator-restore-old-one")).toBeInTheDocument();
    // Settings, read-only.
    expect(panel.textContent).toContain("idle days");
    expect(panel.textContent).toContain("30");
  });

  it("Sweep now: dry run POSTs {dry_run: true} and says what WOULD go, without refetching", async () => {
    hooks.api["/skills/curator"] = curatorView();
    hooks.postResults["/skills/curator/run"] = {
      dry_run: true, archived: [], would_archive: ["ledger-sum"], kept: ["tidy-notes", "mine"], backup: null,
    };
    render(<SkillsPage />);
    fireEvent.click(screen.getByTestId("curator-toggle"));
    fireEvent.click(screen.getByTestId("curator-sweep"));
    await waitFor(() =>
      expect(screen.getByTestId("curator-sweep-result").textContent).toBe(
        "Dry run: would archive 1 skill (ledger-sum) and keep 2.",
      ),
    );
    expect(hooks.posts).toEqual([{ path: "/skills/curator/run", body: { dry_run: true } }]);
    expect(hooks.reloads).toEqual([]);
  });

  it("Sweep now unticked: POSTs {dry_run: false}, says what went + the backup, refetches both", async () => {
    hooks.api["/skills/curator"] = curatorView();
    hooks.postResults["/skills/curator/run"] = { archived: ["ledger-sum"], kept: ["tidy-notes", "mine"], backup: "C:\\bk\\skills.zip" };
    render(<SkillsPage />);
    fireEvent.click(screen.getByTestId("curator-toggle"));
    fireEvent.click(screen.getByTestId("curator-dry-run"));
    expect(screen.getByTestId("curator-dry-run")).not.toBeChecked();
    fireEvent.click(screen.getByTestId("curator-sweep"));
    await waitFor(() =>
      expect(screen.getByTestId("curator-sweep-result").textContent).toBe(
        "Archived 1 skill (ledger-sum); 2 kept. Backup at C:\\bk\\skills.zip.",
      ),
    );
    expect(hooks.posts).toEqual([{ path: "/skills/curator/run", body: { dry_run: false } }]);
    expect(hooks.reloads).toContain("/skills/curator");
    expect(hooks.reloads).toContain("/skills");
  });

  it("Restore POSTs …/restore and refetches; a candidate's Pin POSTs …/pin; Archive now confirms", async () => {
    hooks.api["/skills/curator"] = curatorView();
    render(<SkillsPage />);
    fireEvent.click(screen.getByTestId("curator-toggle"));
    fireEvent.click(screen.getByTestId("curator-restore-old-one"));
    await waitFor(() => expect(hooks.reloads).toContain("/skills/curator"));
    expect(hooks.posts).toEqual([{ path: "/skills/curator/old-one/restore", body: undefined }]);
    expect(hooks.reloads).toContain("/skills");

    hooks.reloads = [];
    fireEvent.click(screen.getByTestId("curator-pin-ledger-sum"));
    await waitFor(() => expect(hooks.reloads).toContain("/skills/curator"));
    expect(hooks.posts[1]).toEqual({ path: "/skills/curator/ledger-sum/pin", body: undefined });

    hooks.reloads = [];
    const arch = screen.getByTestId("curator-archive-ledger-sum");
    fireEvent.click(arch);
    expect(arch.textContent).toBe("Archive it?");
    expect(hooks.posts).toHaveLength(2);
    fireEvent.click(arch);
    await waitFor(() => expect(hooks.reloads).toContain("/skills/curator"));
    expect(hooks.posts[2]).toEqual({ path: "/skills/curator/ledger-sum/archive", body: undefined });
  });

  it("renders standalone with the view it is given (sweeps off badge when disabled)", () => {
    const v = curatorView({ enabled: false });
    render(<SkillCurator view={v} onRefresh={() => {}} onSkillsChanged={() => {}} />);
    expect(screen.getByTestId("curator-panel").textContent).toContain("sweeps off");
    // Enabled (the default): no badge.
    cleanup();
    render(<SkillCurator view={curatorView()} onRefresh={() => {}} onSkillsChanged={() => {}} />);
    expect(screen.getByTestId("curator-panel").textContent).not.toContain("sweeps off");
  });

  it("reads the daemon's FLAT status fields: last sweep in the headline, the settings when open", () => {
    const v = curatorView({ last_sweep_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString() });
    render(<SkillCurator view={v} onRefresh={() => {}} onSkillsChanged={() => {}} />);
    const panel = screen.getByTestId("curator-panel");
    expect(panel.textContent).toMatch(/last sweep /);
    expect(panel.textContent).not.toContain("never swept");
    fireEvent.click(within(panel).getByTestId("curator-toggle"));
    expect(panel.textContent).toContain("idle days");
    expect(panel.textContent).toContain("min age days");
  });
});
