/**
 * v1.455.0 (readiness Wave G, "chat finds the right thing"): the dashboard
 * half.
 *
 * - item 23: a page door carrying `install` IS the one install press
 *   (lib/cliInstall — the job Settings › Connections starts), read off the
 *   REAL daemon's door table (fixtures/page-doors-v1436.json); an unknown
 *   `install` is dropped, so the door is then its plain link;
 * - item 25: Local only offered ONCE when a file is attached to a chat that
 *   is not Local only and a model on this PC or the user's network is
 *   connected (lib/localOnlyOffer); Night shift, Try on and Local only are
 *   rows in Help, on Everything and in Ctrl K (lib/featureRows) that lead to
 *   a real Handbook heading;
 * - item 26: "Give this to the team as an objective" carries the request into
 *   the objective composer (lib/missionStart), never from a Local only chat.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({
  posts: [] as { path: string; body?: Record<string, unknown> }[],
  postResult: { id: "pane-9" } as unknown,
  daemon: { health: null, checking: false, online: true },
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
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
    get: () => Promise.resolve({}),
    post: (p: string, body?: Record<string, unknown>) => {
      H.posts.push({ path: p, body });
      return Promise.resolve(H.postResult);
    },
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/daemon", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useDaemon: () => H.daemon,
}));
vi.mock("next/link", () => ({
  default: ({ href, children, onClick, ...rest }: { href: string; children: React.ReactNode; onClick?: (e: unknown) => void }) => (
    <a href={href} onClick={(e) => onClick?.(e)} {...rest}>
      {children}
    </a>
  ),
}));

import { DoorsStrip, decodeDoors } from "@/components/chat/DoorsStrip";
import { INSTALL_JOBS, installingLine } from "@/lib/cliInstall";
import { LocalOnlyOffer } from "@/components/chat/LocalOnlyOffer";
import { LOCAL_ONLY_OFFER_KEEP, OFFER_SEEN_KEY, hasLocalModel, shouldOfferLocalOnly } from "@/lib/localOnlyOffer";
import { FEATURE_ROWS } from "@/lib/featureRows";
import { SURFACES, surfaceFor } from "@/lib/surfaces";
import { readGuideLink, sectionMatches, headingSlug } from "@/lib/helpLinks";
import {
  MISSION_OFFER_HREF,
  MISSION_START_KEY,
  isMissionOffer,
  stageMissionStart,
  startMissionFromDoor,
  takeMissionStart,
} from "@/lib/missionStart";
import { MissionComposer } from "@/components/agents/mission/MissionComposer";

const ROOT = path.resolve(__dirname, "..", "..");
const read = (p: string) => readFileSync(path.join(ROOT, p), "utf8").replace(/\r\n/g, "\n");
const FIXTURE = JSON.parse(read("dashboard/__tests__/fixtures/page-doors-v1436.json")) as {
  table: { steps: { id: string; door: { href: string; label: string; press?: string; install?: string } }[] };
};
const HANDBOOK_SLUGS = read("docs/HANDBOOK.md")
  .split("\n")
  .filter((l) => /^#{1,4} /.test(l))
  .map((l) => headingSlug(l.replace(/^#+/, "").trim()));

beforeEach(() => {
  H.posts.length = 0;
  window.localStorage.clear();
  window.sessionStorage.clear();
});

// ---------------------------------------------------------------- item 23
describe("an install door is the ONE install press", () => {
  const installs = FIXTURE.table.steps.filter((s) => s.door.install);

  it("the daemon gives an install door for each install job this dashboard has", () => {
    expect(new Set(installs.map((s) => s.door.install))).toEqual(new Set(Object.keys(INSTALL_JOBS)));
    expect(installs.map((s) => s.door.label)).toEqual(["Install Claude Code for me", "Install Codex for me"]);
  });

  it("keeps a known install, drops an unknown one (the door is then a link)", () => {
    const doors = decodeDoors([
      { href: "/connections", label: "Install Codex for me", install: "codex" },
      { href: "/x", label: "Odd", install: "evil-cli" },
    ]);
    expect(doors).toEqual([
      { href: "/connections", label: "Install Codex for me", install: "codex" },
      { href: "/x", label: "Odd" },
    ]);
  });

  it("pressing it starts the install job by name and says what happens next", async () => {
    render(<DoorsStrip doors={decodeDoors([installs[1].door])} />);
    const press = screen.getByRole("button", { name: "Install Codex for me" });
    await act(async () => {
      fireEvent.click(press);
    });
    expect(H.posts).toEqual([{ path: "/terminals/launch", body: { cli: "codex-install" } }]);
    expect(await screen.findByTestId("door-install-codex-started")).toHaveTextContent(installingLine("Codex"));
    // No link was drawn for it: the press IS the door.
    expect(screen.queryByRole("link")).toBeNull();
  });
});

// ---------------------------------------------------------------- item 25
const LOCAL = { provider: "ollama", model: "llama3.2", available: true, kind: "local" as const };
const CLOUD = { provider: "anthropic", model: "claude", available: true, kind: "api" as const };
const DEMO = { provider: "mock", model: "demo", available: true, kind: "local" as const };

describe("Local only is offered once", () => {
  it("needs a file, a chat not Local only, and a connected model that stays here", () => {
    const base = { attachedCount: 1, localOnly: false, models: [LOCAL], seen: false, shownHere: false, answered: false };
    expect(shouldOfferLocalOnly(base)).toBe(true);
    expect(shouldOfferLocalOnly({ ...base, attachedCount: 0 })).toBe(false);
    expect(shouldOfferLocalOnly({ ...base, localOnly: true })).toBe(false);
    expect(shouldOfferLocalOnly({ ...base, models: [CLOUD] })).toBe(false);
    expect(shouldOfferLocalOnly({ ...base, models: [{ ...LOCAL, available: false }] })).toBe(false);
    // The demo model "stays here" too, but is no model to keep a chat on.
    expect(hasLocalModel([DEMO])).toBe(false);
    // A cloud server behind a local-class endpoint says so (on_this_network).
    expect(hasLocalModel([{ ...LOCAL, on_this_network: false }])).toBe(false);
    // Once: shown before (another chat) and not this chat's — no line.
    expect(shouldOfferLocalOnly({ ...base, seen: true })).toBe(false);
    expect(shouldOfferLocalOnly({ ...base, seen: true, shownHere: true })).toBe(true);
    expect(shouldOfferLocalOnly({ ...base, answered: true })).toBe(false);
  });

  it("Keep turns Local only on through the page's own toggle, and it is remembered", () => {
    const onKeep = vi.fn();
    const { rerender } = render(<LocalOnlyOffer attachedCount={1} localOnly={false} models={[LOCAL]} onKeep={onKeep} />);
    expect(screen.getByTestId("local-only-offer")).toBeInTheDocument();
    expect(window.localStorage.getItem(OFFER_SEEN_KEY)).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: LOCAL_ONLY_OFFER_KEEP }));
    expect(onKeep).toHaveBeenCalledTimes(1);
    rerender(<LocalOnlyOffer attachedCount={1} localOnly={true} models={[LOCAL]} onKeep={onKeep} />);
    expect(screen.queryByTestId("local-only-offer")).toBeNull();
  });

  it("is never automatic and never shown again in a later chat", () => {
    window.localStorage.setItem(OFFER_SEEN_KEY, "1");
    const onKeep = vi.fn();
    render(<LocalOnlyOffer attachedCount={2} localOnly={false} models={[LOCAL]} onKeep={onKeep} />);
    expect(screen.queryByTestId("local-only-offer")).toBeNull();
    expect(onKeep).not.toHaveBeenCalled();
  });

  it("the chat page mounts it beside the attachments with the chip's own toggle", () => {
    const page = read("dashboard/app/chat/page.tsx");
    const at = page.indexOf("<LocalOnlyOffer");
    expect(at).toBeGreaterThan(0);
    const block = page.slice(at, at + 400);
    expect(block).toContain("attachedCount={attachments.length}");
    expect(block).toContain("if (!localOnlyRef.current) toggleLocalOnly();");
  });
});

describe("Night shift, Try on and Local only are found everywhere", () => {
  it("each row leads to a real Handbook heading through the Help page's own rule", () => {
    expect(FEATURE_ROWS.map((f) => f.id)).toEqual(["night-shift", "try-on", "local-only"]);
    for (const f of FEATURE_ROWS) {
      const link = readGuideLink(f.href.slice(f.href.indexOf("?")));
      expect(link?.guide).toBe("handbook");
      expect(HANDBOOK_SLUGS.some((s) => sectionMatches(s, link!.section)), f.href).toBe(true);
    }
  });

  it("they are surfaces — on Everything and in Ctrl K — with their own words", () => {
    for (const f of FEATURE_ROWS) {
      const s = surfaceFor(f.href);
      expect(s, f.id).toBeTruthy();
      expect(s!.label).toBe(f.title);
      expect(s!.group).not.toBe("primary");
      expect(s!.aliases.length).toBeGreaterThan(2);
    }
    expect(SURFACES.filter((s) => FEATURE_ROWS.some((f) => f.href === s.href))).toHaveLength(3);
  });

  it("Help draws them, and a press while Help is open opens the guide in place", () => {
    const help = read("dashboard/app/help/page.tsx");
    expect(help).toContain('data-testid="help-features"');
    expect(help).toContain("if (announceHelpLink(f.href)) e.preventDefault();");
    expect(help).toContain("window.addEventListener(HELP_GUIDE_EVENT, on);");
    const palette = read("dashboard/components/CommandPalette.tsx");
    expect(palette).toContain("announceHelpLink(row.href);");
  });
});

// ---------------------------------------------------------------- item 26
describe("the team offer carries the request into the objective composer", () => {
  const MSGS = [
    { role: "user", content: "Research the new thresholds, compare them, then draft the memo." },
    { role: "assistant", content: "Here is a start." },
  ];

  it("its address is the daemon's, and a press stages the request it answers", () => {
    expect(MISSION_OFFER_HREF).toBe("/agents?from=chat");
    expect(isMissionOffer(MISSION_OFFER_HREF)).toBe(true);
    // The press is never "handled": the door's link then opens the page.
    expect(startMissionFromDoor(MSGS, 1, MISSION_OFFER_HREF)).toBe(false);
    expect(takeMissionStart()).toBe(MSGS[0].content);
    // Taken once.
    expect(takeMissionStart()).toBe("");
  });

  it("a Local only chat stages nothing, another door stages nothing, an old one is ignored", () => {
    startMissionFromDoor(MSGS, 1, MISSION_OFFER_HREF, true);
    expect(window.sessionStorage.getItem(MISSION_START_KEY)).toBeNull();
    startMissionFromDoor(MSGS, 1, "/chat?view=code");
    expect(window.sessionStorage.getItem(MISSION_START_KEY)).toBeNull();
    stageMissionStart("old", Date.now() - 10 * 60_000);
    expect(takeMissionStart()).toBe("");
  });

  it("the composer opens with the request in the box and starts nothing", async () => {
    stageMissionStart("Collect the statements, reconcile them, then draft the questions.");
    render(<MissionComposer onStarted={() => {}} onOpen={() => {}} />);
    const box = (await screen.findByDisplayValue(/Collect the statements/)) as HTMLTextAreaElement;
    expect(box.tagName).toBe("TEXTAREA");
    expect(H.posts.filter((p) => p.path === "/missions")).toEqual([]);
  });

  it("the chat page sends the press to the team door after the coding door", () => {
    const page = read("dashboard/app/chat/page.tsx");
    expect(page).toContain("startMissionFromDoor(messagesRef.current, index, door.href, localOnlyRef.current)");
  });
});
