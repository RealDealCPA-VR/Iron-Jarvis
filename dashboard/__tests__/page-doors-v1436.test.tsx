/**
 * v1.436.0 (chat-first plan Wave 5, group J, B3) — a door to the exact place
 * a page-only step is done, and the Help page opening on a guide's section.
 *
 * The daemon (daemon/page_doors) puts ONE door under a reply that asks for a
 * step only a page can do. These tests read what the REAL daemon sends — the
 * fixture `fixtures/page-doors-v1436.json`, written by
 * tests/test_page_doors_v14360.py — and pin:
 *  - a sign-in door is drawn through the ONE Sign in press
 *    (components/connections/CliSignInPress, variant "door"): a button, never
 *    a link; pressing it opens the same terminal Settings › Connections does;
 *  - every other door is a plain link to the daemon's own address;
 *  - the decoder keeps `press` only for a press this dashboard has;
 *  - every Help address resolves: the guide is one the daemon serves and the
 *    section names a real Handbook heading through `lib/helpLinks` — the TS
 *    twin of the daemon's slug rule, held equal on the fixture's cases;
 *  - the Help page opens that guide and brings the section into view.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const hooks = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    docs: {} as Record<string, unknown>,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    MockApiError,
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: hooks.MockApiError,
  ijToken: () => "",
  get: (path: string) => {
    hooks.gets.push(path);
    const body = hooks.docs[path];
    if (body !== undefined) return Promise.resolve(body);
    return Promise.reject(new hooks.MockApiError("missing", 404));
  },
  post: (path: string, body: unknown) => {
    hooks.posts.push({ path, body });
    return Promise.resolve({ id: "pane-7" });
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

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
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { DoorsStrip, decodeDoors, type Door } from "@/components/chat/DoorsStrip";
import { SIGN_IN_CLIS } from "@/lib/cliSignIn";
import { headingSlug, helpGuideHref, readGuideLink, sectionMatches } from "@/lib/helpLinks";
import { RELOCATED, relocatedHref } from "@/lib/relocated";
import HelpPage from "@/app/help/page";

const ROOT = join(__dirname, "..", "..");
const lf = (p: string) => readFileSync(p, "utf-8").replace(/\r\n/g, "\n");
const FIXTURE = JSON.parse(lf(join(__dirname, "fixtures", "page-doors-v1436.json"))) as {
  stream_done: { doors: Door[]; reply: string };
  table: {
    steps: { id: string; door: Door }[];
    presses: string[];
    guides: string[];
    handbook_section: string;
    slug_cases: Record<string, string>;
  };
};
const HANDBOOK_SLUGS = lf(join(ROOT, "docs", "HANDBOOK.md"))
  .split("\n")
  .filter((ln) => /^#{1,4} /.test(ln))
  .map((ln) => headingSlug(ln.replace(/^#+/, "").trim()));

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  hooks.responses = {};
  hooks.docs = {};
  hooks.gets = [];
  hooks.posts = [];
  window.history.replaceState(null, "", "/");
});

describe("the daemon's doors resolve on this dashboard", () => {
  it("the section slug rule is the daemon's, case for case", () => {
    for (const [text, slug] of Object.entries(FIXTURE.table.slug_cases)) {
      expect(headingSlug(text)).toBe(slug);
    }
  });

  it("every press is one the dashboard has, named the way the press says it", () => {
    expect(new Set(FIXTURE.table.presses)).toEqual(new Set(SIGN_IN_CLIS.map((c) => c.provider)));
    for (const { door } of FIXTURE.table.steps) {
      if (!door.press) continue;
      const cli = SIGN_IN_CLIS.find((c) => c.provider === door.press);
      expect(cli, door.press).toBeTruthy();
      expect(door.label).toBe(`Sign in to ${cli!.name}`);
    }
  });

  it("every Help address names a guide the daemon serves and a real Handbook heading", () => {
    let helpDoors = 0;
    for (const { door } of FIXTURE.table.steps) {
      if (!door.href.startsWith("/help")) continue;
      helpDoors += 1;
      const link = readGuideLink(door.href.slice(door.href.indexOf("?")));
      expect(link, door.href).not.toBeNull();
      expect(FIXTURE.table.guides).toContain(link!.guide);
      if (link!.section) {
        expect(link!.guide).toBe("handbook");
        expect(HANDBOOK_SLUGS.some((s) => sectionMatches(s, link!.section)), door.href).toBe(true);
        // The address is the helper's own shape.
        expect(door.href).toBe(helpGuideHref(link!.guide, link!.section));
      }
    }
    expect(helpDoors).toBe(2);
    // Anti-vacuity: a section no heading has is not "found".
    expect(HANDBOOK_SLUGS.some((s) => sectionMatches(s, "no-such-section-anywhere"))).toBe(false);
    // A shorter slug never matches the middle of another word.
    expect(sectionMatches("microsoft-365-locked-down", "microsoft-3")).toBe(false);
  });

  it("every Settings address is an alias or the place an alias leads to", () => {
    for (const { door } of FIXTURE.table.steps) {
      const path = door.href.split("?")[0];
      if (path === "/settings") {
        const targets = Object.values(RELOCATED).map((r) => relocatedHref(r.base, {}, r.extra));
        expect(targets, door.href).toContain(door.href);
      } else if (path in RELOCATED) {
        expect(door.href).toBe(path);
      }
    }
  });
});

describe("decodeDoors keeps a press only when this dashboard has it", () => {
  it("keeps the real stream lane's press", () => {
    expect(decodeDoors(FIXTURE.stream_done.doors)).toEqual([
      { href: "/connections", label: "Sign in to Claude Code", press: "claude-cli" },
    ]);
  });

  it("drops an unknown press, a bad entry, and keeps a plain door plain", () => {
    expect(
      decodeDoors([
        { href: "/connections", label: "Sign in", press: "evil-cli" },
        { href: "", label: "nothing" },
        null,
        { href: "/workflows", label: "Workflows" },
      ]),
    ).toEqual([
      { href: "/connections", label: "Sign in" },
      { href: "/workflows", label: "Workflows" },
    ]);
    expect(decodeDoors("nope")).toBeNull();
  });
});

describe("DoorsStrip draws a sign-in door as the ONE Sign in press", () => {
  it("is a button that opens the same terminal Settings › Connections opens", async () => {
    render(<DoorsStrip doors={decodeDoors(FIXTURE.stream_done.doors)} />);
    expect(screen.queryByRole("link")).toBeNull();
    const press = screen.getByRole("button", { name: "Sign in to Claude Code" });
    fireEvent.click(press);
    await waitFor(() => expect(hooks.posts).toEqual([{ path: "/terminals/launch", body: { cli: "claude", account: "default" } }]));
    const finish = await screen.findByTestId("door-pane-hint-claude-cli");
    expect(finish).toHaveAttribute("href", "/terminals?focus=pane-7");
    expect(finish).toHaveTextContent("Finish signing in to Claude Code in Build");
    // The open is tallied like any door's.
    expect(window.localStorage.getItem("ironjarvis.doors.usage") ?? "").toContain("/connections");
  });

  it("every other door is a plain link to the daemon's address", () => {
    // v1.455.0 (Wave G item 23, moved on purpose): an install door is the
    // install press (pinned in chat-finds-the-right-thing-v1455).
    const links = FIXTURE.table.steps.map((s) => s.door).filter((d) => !d.press && !d.install);
    render(<DoorsStrip doors={decodeDoors(links)} />);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(links.map((d) => d.href));
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("the Help page opens a door's guide on its section", () => {
  const CATALOG = {
    docs: [
      { slug: "handbook", title: "The Handbook", description: "Every surface." },
      { slug: "local-models", title: "Local Models by RAM Tier", description: "What to run." },
    ],
  };
  const MARKDOWN = [
    "# Iron Jarvis — The Handbook",
    "",
    "## Something else (v1.1.0)",
    "",
    "Other words.",
    "",
    "## Microsoft 365, locked down for client data (v1.428.0)",
    "",
    "Set up your firm's app.",
  ].join("\n");
  let scrolled: Element[] = [];
  const original = Element.prototype.scrollIntoView;

  beforeEach(() => {
    scrolled = [];
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    } as typeof Element.prototype.scrollIntoView;
    hooks.responses["/helpdocs"] = CATALOG;
    hooks.docs["/helpdocs/handbook"] = { slug: "handbook", title: "The Handbook", markdown: MARKDOWN };
  });
  afterEach(() => {
    Element.prototype.scrollIntoView = original;
  });

  it("opens the guide and brings the section's heading into view", async () => {
    window.history.replaceState(null, "", `/help?guide=handbook&section=${FIXTURE.table.handbook_section}`);
    render(<HelpPage />);
    await waitFor(() => expect(hooks.gets).toContain("/helpdocs/handbook"));
    await waitFor(() => expect(scrolled.length).toBe(1));
    expect(scrolled[0].tagName).toBe("H2");
    expect(scrolled[0]).toHaveTextContent("Microsoft 365, locked down for client data");
    expect(await screen.findByTestId("help-guide-viewer")).toHaveTextContent("Set up your firm's app.");
  });

  it("with no guide in the address nothing opens", async () => {
    window.history.replaceState(null, "", "/help");
    render(<HelpPage />);
    await act(async () => {});
    expect(hooks.gets).not.toContain("/helpdocs/handbook");
    expect(scrolled).toEqual([]);
  });

  it("a guide the daemon does not list opens nothing", async () => {
    window.history.replaceState(null, "", "/help?guide=nope");
    render(<HelpPage />);
    await act(async () => {});
    expect(hooks.gets.filter((g) => g.startsWith("/helpdocs/"))).toEqual([]);
  });
});
