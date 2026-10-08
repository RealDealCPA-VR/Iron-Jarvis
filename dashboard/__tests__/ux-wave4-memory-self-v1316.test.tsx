/**
 * v1.316.0 — UX wave 4, track T4: memory & self (/memory, /ltm, /lessons,
 * /you, /train, /skills).
 *
 * Each test pins ONE user-visible behaviour. Most carry an anti-vacuity half:
 * every control and consent step is still there (two-press forgets, the
 * vendor named before the Share press, Save/Discard in the header), raw values
 * stay reachable, and a real flow still works end to end.
 *
 * Interface decisions the implementer follows:
 *  - DEEP LINKS. The Memory scope row (the div holding the scope tablist /
 *    graph chip AND the "Memory view" group) gets id="memory-scopes" and
 *    `scroll-mt-14`. ScopedMemory calls `scrollIntoView({block: "start",
 *    behavior})` on THAT element when the route is /ltm or /lessons
 *    (initialScope !== "working") or the URL carries an explicit ?scope= —
 *    never for a bare /memory. At least one scroll lands AFTER the
 *    /memory/overview answer settles (KnowsAboutYou grows above the tabs);
 *    scrolling once at mount and once more after settle is fine. Once per
 *    mount: a tab press (switchTo writes ?scope=) never scrolls again. Under
 *    prefers-reduced-motion the behavior is "auto" (never "smooth").
 *  - Train: step 3's doorway goes to /memory?scope=longterm and counts the
 *    bases the USER added (memory_bases - 1, the built-in base named as
 *    always on); its done rule is unchanged (memory_bases > 1). Step 4's
 *    doorway goes to /memory?scope=longterm (where Import lives) and does not
 *    say "N items" — memory_items counts WORKING-memory rows, while an import
 *    lands as its own base (learning.py memory_import_commit), so a count
 *    there must be labelled for what it is, or dropped.
 *  - Search hierarchy: Recall stays the one prominent search. The Working
 *    and Long-term scoped searches are Cards titled "Search only …" (Working
 *    keeps a short pointer to Recall). No "session · project · user",
 *    "key-values" or "mid-run" on the page. Recall with no query shows one
 *    quiet line, not the <Empty> icon block. The Import door
 *    (data-testid="memory-import-link", href /memory?scope=longterm, list
 *    view, scope !== longterm) moves INTO the scope row next to "Memory view".
 *  - /you: when dirty, a bar data-testid="you-unsaved-bar" (class `sticky`,
 *    theme-token surfaces, no bg-black) holds Save + Discard wired to the
 *    SAME save() / discard as the header (header buttons stay). Under "What
 *    the model sees", a line "Plus N preference(s) you told Jarvis — see them
 *    in Memory" links to /memory (N = /memory/overview preferences.length);
 *    a failed overview hides the line.
 *  - Lessons Forget: TWO presses. At rest: aria-label "Forget this lesson",
 *    title unchanged, `focus-visible:opacity-100`, and visible on touch
 *    (`[@media(hover:none)]:opacity-…`). First press arms (visible "Sure?",
 *    aria-label mentions pressing again); second press DELETEs.
 *  - Preference Edit/Forget: at least a 24px hit area (py-1 or more, never
 *    py-px) and the action span is `[@media(hover:none)]:opacity-100`.
 *  - KnowsAboutYou: the profile row is `flex items-start` (no flex-wrap) and
 *    its <p> is `flex-1 min-w-0`. ProfileShareRow says the shared part
 *    ("Writes your profile and the preferences you said or kept — nothing
 *    else Jarvis remembers …") ONCE (data-testid="profile-share-explain"),
 *    while each `profile-share-sentence-<cli>` still names its file path AND
 *    "<Vendor> sees it when <Label> runs" (privacy disclosure per switch).
 *  - Skills: an input aria-label="Search skills" filters by name and
 *    description, combined with the source chips. The detail column wrapper
 *    gets id="skill-instructions"; selecting a skill when
 *    matchMedia("(max-width: 1023px)") matches scrolls it into view (never on
 *    desktop). curatorHeadline reads "Tidy-up: …" in plain words — no
 *    "Curator —", "candidate" or "swept"; "nothing to archive" when there are
 *    no candidates, "never run" / "last run …"; the counts stay.
 *  - LongTerm's folder picker renders through <Modal label="Pick a markdown
 *    folder"> (portal, focus in/trap/back); Cancel, backdrop and "Use this
 *    folder" behave as before.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/* ------------------------------------------------------------------ mocks */

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    /** path -> value | Error | () => Promise. Unmocked = 404. */
    get: {} as Record<string, unknown>,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    dels: [] as string[],
    putReply: null as null | ((path: string, body: unknown) => unknown),
    search: "",
    pathname: "/memory",
    replaced: [] as string[],
    // Typed wide: tests swap in `(q) => q.includes(...)` (boolean), and a
    // literal `=> false` infers `false` and fails the type check.
    mq: ((_q: string) => false) as (q: string) => boolean,
    scrolls: [] as { el: Element; arg: unknown }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  wsUrl: (p: string) => `ws://api.test${p}`,
  sseUrl: (p: string) => `http://api.test${p}`,
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.get[path];
    if (typeof r === "function") return (r as () => Promise<unknown>)();
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    return {};
  },
  put: async (path: string, body?: unknown) => {
    H.puts.push({ path, body });
    return H.putReply ? H.putReply(path, body) : {};
  },
  patch: async () => ({}),
  del: async (path: string) => {
    H.dels.push(path);
    return {};
  },
  api: async () => ({}),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: () => {},
    replace: (href: string) => {
      H.replaced.push(href);
      H.search = href.includes("?") ? href.slice(href.indexOf("?") + 1) : "";
    },
    prefetch: () => {},
    back: () => {},
    refresh: () => {},
  }),
  usePathname: () => H.pathname,
  useSearchParams: () => new URLSearchParams(H.search),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "layoutId",
    "whileHover",
    "whileTap",
    "whileFocus",
    "whileInView",
    "viewport",
    "drag",
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
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    LazyMotion: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    domAnimation: {},
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));
// Heavy siblings that are not under test.
vi.mock("@/components/memory/MemoryGraph", () => ({ default: () => null }));
vi.mock("@/components/memory/ImportFromAI", () => ({ ImportFromAI: () => null }));
vi.mock("@/components/terminal/DirectoryTree", async () => {
  const { createElement } = await import("react");
  return {
    DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) =>
      createElement("button", { type: "button", onClick: () => onSelect("C:\\notes") }, "pick-notes"),
  };
});

import { MemorySurface } from "@/components/memory/MemorySurface";
import { WorkingMemory } from "@/components/memory/WorkingMemory";
import { RecallSearch } from "@/components/memory/RecallSearch";
import { Lessons } from "@/components/memory/Lessons";
import { LongTerm } from "@/components/memory/LongTerm";
import { KnowsAboutYou } from "@/components/memory/KnowsAboutYou";
import { PreferenceSections } from "@/components/memory/PreferenceSections";
import { curatorHeadline } from "@/components/skills/SkillCurator";
import YouPage from "@/app/you/page";
import TrainPage from "@/app/train/page";
import SkillsPage from "@/app/skills/page";
import { NAV_ENTRIES, labelForPath } from "@/lib/nav";
import type { PreferencesView } from "@/lib/preferences";
import type { SkillCuratorView } from "@/lib/types";

/* ---------------------------------------------------------------- helpers */

const ROOT = process.cwd();
/** Source text, CRLF-normalised (this checkout is autocrlf). */
const src = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

const flush = (ms = 30) =>
  act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const scopeRowScrolls = () =>
  H.scrolls.filter(
    (s) =>
      s.el.id === "memory-scopes" &&
      !!s.el.querySelector('[role="tablist"][aria-label="Memory scope"]') &&
      !!s.el.querySelector('[role="group"][aria-label="Memory view"]'),
  );

const OVERVIEW = {
  profile: {
    filled: true,
    enabled: true,
    about_line: "Runs a small bookkeeping practice.",
    tone: "",
    writing_style: "",
  },
  preferences: [
    { id: "l1", text: "Keep answers short", source: "preference", weight: 5, created_at: "2026-09-18" },
    { id: "l2", text: "Use numbered steps", source: "feedback", weight: 3, created_at: "2026-09-19" },
  ],
  lessons: { total: 2, reflections: 0, by_source: { preference: 1, feedback: 1 } },
  bases: [{ name: "brain", kind: "builtin", notes: 0 }],
  working: {},
  history: { docs: 0, available: false },
  empty: false,
};

const NOW = "2026-10-07T10:00:00Z";
const LESSONS = {
  lessons: [
    { id: "p1", text: "Keep answers short", source: "preference", weight: 5, scope: "user", created_at: NOW },
    { id: "f1", text: "Use numbered steps", source: "feedback", weight: 3, scope: "user", created_at: NOW },
  ],
};
const IMPROVEMENT = { outcomes: { count: 0 }, agents: [], lessons: [] };

function seedMemory() {
  H.get["/memory/overview"] = OVERVIEW;
  H.get["/lessons?limit=50"] = LESSONS;
  H.get["/improvement"] = IMPROVEMENT;
  H.get["/ltm/sources"] = { sources: [], active: [] };
}

beforeEach(() => {
  H.get = {};
  H.gets.length = 0;
  H.posts.length = 0;
  H.puts.length = 0;
  H.dels.length = 0;
  H.putReply = null;
  H.search = "";
  H.pathname = "/memory";
  H.replaced.length = 0;
  H.mq = () => false;
  H.scrolls.length = 0;
  Element.prototype.scrollIntoView = vi.fn(function (this: Element, arg?: unknown) {
    H.scrolls.push({ el: this, arg });
  }) as unknown as Element["scrollIntoView"];
  window.matchMedia = vi.fn((q: string) => ({
    matches: H.mq(q),
    media: q,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  try {
    window.localStorage.clear();
  } catch {
    /* no storage */
  }
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ======================================================================== */
/*  HIGH — a memory deep link lands on the section it names                  */
/* ======================================================================== */

describe("memory deep links land on their section (memory-deeplinks-land-on-same-top)", () => {
  it("/lessons scrolls the scope row into view, and again after the profile card fills in", async () => {
    H.pathname = "/lessons";
    seedMemory();
    const ov = deferred<unknown>();
    H.get["/memory/overview"] = () => ov.promise;
    render(<MemorySurface initialScope="lessons" />);
    await flush(40);
    const before = scopeRowScrolls().length;

    ov.resolve(OVERVIEW);
    await screen.findByTestId("knows-about-you");
    // The card above the tabs just grew: a scroll at mount alone would now
    // sit too high, so at least one scroll must land AFTER the answer.
    await waitFor(() => expect(scopeRowScrolls().length).toBeGreaterThan(before), { timeout: 1500 });
    for (const s of scopeRowScrolls()) {
      expect((s.arg as ScrollIntoViewOptions | undefined)?.block).toBe("start");
    }
    // The target row is clear of the 40px title bar.
    expect(document.getElementById("memory-scopes")!.className).toMatch(/\bscroll-mt-/);
    // The scope chosen by the route is the one selected.
    expect(screen.getByRole("tab", { name: /What I've learned/ })).toHaveAttribute("aria-selected", "true");
  });

  it("an older daemon (overview fails) still lands on the section", async () => {
    H.pathname = "/lessons";
    seedMemory();
    H.get["/memory/overview"] = new H.FakeApiError("boom", 500);
    render(<MemorySurface initialScope="lessons" />);
    await waitFor(() => expect(scopeRowScrolls().length).toBeGreaterThan(0), { timeout: 1500 });
  });

  it("an explicit /memory?scope=longterm scrolls too", async () => {
    H.pathname = "/memory";
    H.search = "scope=longterm";
    seedMemory();
    render(<MemorySurface />);
    await screen.findByTestId("knows-about-you");
    await waitFor(() => expect(scopeRowScrolls().length).toBeGreaterThan(0), { timeout: 1500 });
    expect(screen.getByRole("tab", { name: /Long-term/ })).toHaveAttribute("aria-selected", "true");
  });

  it("control: a bare /memory never scrolls (it opens at the top)", async () => {
    H.pathname = "/memory";
    seedMemory();
    render(<MemorySurface />);
    await screen.findByTestId("knows-about-you");
    await flush(120);
    expect(H.scrolls).toHaveLength(0);
    expect(screen.getByRole("tab", { name: /Working/ })).toHaveAttribute("aria-selected", "true");
  });

  it("once per mount: pressing a tab (which writes ?scope=) does not scroll again", async () => {
    H.pathname = "/lessons";
    seedMemory();
    const view = render(<MemorySurface initialScope="lessons" />);
    await screen.findByTestId("knows-about-you");
    await waitFor(() => expect(scopeRowScrolls().length).toBeGreaterThan(0), { timeout: 1500 });
    await flush(120);
    const settled = H.scrolls.length;

    fireEvent.click(screen.getByRole("tab", { name: /Working/ }));
    expect(H.replaced.at(-1)).toMatch(/^\/memory\?scope=working/);
    view.rerender(<MemorySurface initialScope="lessons" />);
    await flush(120);
    expect(screen.getByRole("tab", { name: /Working/ })).toHaveAttribute("aria-selected", "true");
    expect(H.scrolls.length).toBe(settled);
  });

  it("reduced motion: the landing scroll is never smooth", async () => {
    H.mq = (q) => q.includes("prefers-reduced-motion");
    H.pathname = "/lessons";
    seedMemory();
    render(<MemorySurface initialScope="lessons" />);
    await screen.findByTestId("knows-about-you");
    await waitFor(() => expect(scopeRowScrolls().length).toBeGreaterThan(0), { timeout: 1500 });
    for (const s of scopeRowScrolls()) {
      expect((s.arg as ScrollIntoViewOptions | undefined)?.behavior).not.toBe("smooth");
    }
  });

  it("a ?focus=<card> link lands on that CARD: the scope row never scrolls over it", async () => {
    // v1.316.0 review (mutation M4): the palette's "Add a memory base" opens
    // /memory?scope=longterm&focus=add-base. useFocusRef scrolls the card one
    // frame later; a landing scroll on #memory-scopes (at mount, or again
    // when the overview settles) would move the person off the card they
    // asked for. useFocusRef reads window.location, MemorySurface reads
    // useSearchParams — both carry the same query here.
    H.pathname = "/memory";
    H.search = "scope=longterm&focus=add-base";
    const prev = window.location.href;
    window.history.replaceState(null, "", "/memory?scope=longterm&focus=add-base");
    try {
      seedMemory();
      render(<MemorySurface />);
      await screen.findByTestId("knows-about-you");
      expect(screen.getByRole("tab", { name: /Long-term/ })).toHaveAttribute("aria-selected", "true");
      // Anti-vacuity: the focus link really did its job — some element other
      // than the scope row was scrolled to (the add-base card).
      await waitFor(() => expect(H.scrolls.some((s) => s.el.id !== "memory-scopes")).toBe(true), {
        timeout: 1500,
      });
      await flush(120);
      expect(scopeRowScrolls()).toHaveLength(0);
      expect(H.scrolls.filter((s) => s.el.id === "memory-scopes")).toHaveLength(0);
    } finally {
      window.history.replaceState(null, "", prev);
    }
  });

  it("control: /ltm and /lessons keep their 'Memory' crumb; /train's row and title agree", () => {
    expect(labelForPath("/ltm")).toBe("Memory");
    expect(labelForPath("/lessons")).toBe("Memory");
    expect(NAV_ENTRIES.find((e) => e.href === "/train")?.label).toBe("Train Jarvis on me");
    expect(src("app/train/page.tsx")).toMatch(/title="Train Jarvis on me"/);
  });
});

/* ======================================================================== */
/*  Train — the doorways land on Long-term and the counts are honest         */
/* ======================================================================== */

const TRAINING = (over: Record<string, unknown> = {}) => ({
  about: true,
  voice_card: false,
  samples: 0,
  sample_chars: 0,
  memory_bases: 1,
  memory_items: 0,
  search_roots: 0,
  projects: 0,
  ...over,
});
const SAMPLES = { samples: [], total_chars: 0, max_samples: 20, min_chars_to_derive: 400 };

function stepCard(title: string) {
  const head = screen.getByText(title);
  return { badge: head.firstElementChild as HTMLElement, card: head.closest("section") as HTMLElement };
}

describe("Train Jarvis on me — doorways and counts (knows-about-me-model-mismatch)", () => {
  it("step 3 on a fresh install says 0 connected (built-in always on) and links to Long-term", async () => {
    H.get["/profile/training"] = TRAINING({ memory_bases: 1 });
    H.get["/profile/samples"] = SAMPLES;
    render(<TrainPage />);
    await waitFor(() => expect(H.gets).toContain("/profile/training"));
    await flush();
    const { badge, card } = stepCard("Connect your notes and wiki");
    // Not done: the number, never a check (done rule unchanged: > 1 base).
    expect(badge.textContent).toBe("3");
    const door = within(card).getAllByRole("link")[0];
    expect(door).toHaveAttribute("href", "/memory?scope=longterm");
    expect(door.textContent).toMatch(/\b0 connected\b/);
    expect(door.textContent).toMatch(/built-in/i);
    // The second doorway is untouched.
    expect(within(card).getByRole("link", { name: /Turn a document into a note/ })).toHaveAttribute(
      "href",
      "/documents",
    );
  });

  it("step 3 counts only what the user added (3 bases = 2 connected) and is done", async () => {
    H.get["/profile/training"] = TRAINING({ memory_bases: 3 });
    H.get["/profile/samples"] = SAMPLES;
    render(<TrainPage />);
    await waitFor(() => expect(H.gets).toContain("/profile/training"));
    await flush();
    const { badge, card } = stepCard("Connect your notes and wiki");
    expect(badge.textContent).not.toBe("3");
    expect(within(card).getAllByRole("link")[0].textContent).toMatch(/\b2 connected\b/);
  });

  it("step 4 opens Long-term (where Import lives) and does not call working rows 'items'", async () => {
    H.get["/profile/training"] = TRAINING({ memory_items: 0 });
    H.get["/profile/samples"] = SAMPLES;
    render(<TrainPage />);
    await waitFor(() => expect(H.gets).toContain("/profile/training"));
    await flush();
    const { card } = stepCard("Bring your past conversations");
    const door = within(card).getAllByRole("link")[0];
    expect(door).toHaveAttribute("href", "/memory?scope=longterm");
    expect(door.textContent).not.toMatch(/\d+ items?\b/);
    expect(door.textContent).toMatch(/import/i);
  });

  it("control: steps 1, 2 and 5 keep their doorways and the 'not fine-tuning' card", async () => {
    H.get["/profile/training"] = TRAINING();
    H.get["/profile/samples"] = SAMPLES;
    render(<TrainPage />);
    await waitFor(() => expect(H.gets).toContain("/profile/training"));
    await flush();
    expect(within(stepCard("Tell it who you are").card).getAllByRole("link")[0]).toHaveAttribute("href", "/you");
    expect(screen.getByRole("button", { name: /Add sample/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Read my voice/ })).toBeInTheDocument();
    const five = stepCard("Point it at your files").card;
    expect(within(five).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "/filesearch",
      "/projects",
    ]);
    expect(screen.getByText(/None of this trains or fine-tunes a model/)).toBeInTheDocument();
  });
});

/* ======================================================================== */
/*  /you — Save in reach, and the preview admits the preferences             */
/* ======================================================================== */

const PROFILE = {
  profile: {
    enabled: true,
    about: "I run a small CPA firm.",
    tone: "",
    writing_style: "",
    formatting: "",
    formatting_rules: "",
    reading_level: "",
    response_length: "",
    accessibility: "",
    language: "",
    enforce_language: true,
    voice_card: "",
    voice_source: "",
  },
  preview: "# Who you are working with\nI run a small CPA firm.",
  preview_chars: 52,
  preview_limit: 2400,
};
const OPTIONS = {
  tone: [],
  writing_style: [],
  formatting: [],
  reading_level: [],
  response_length: [],
  accessibility: [],
  language: [],
};

function seedYou(overview: unknown = OVERVIEW) {
  H.get["/profile"] = PROFILE;
  H.get["/profile/options"] = OPTIONS;
  if (overview !== undefined) H.get["/memory/overview"] = overview;
  H.putReply = (_p, body) => ({
    ...PROFILE,
    profile: { ...PROFILE.profile, ...((body as { values: object }).values ?? {}) },
  });
}

describe("/you — Save stays in reach (you-save-out-of-reach)", () => {
  it("no bar while nothing changed; a sticky bar with Save + Discard once something did", async () => {
    seedYou();
    render(<YouPage />);
    const rules = await screen.findByPlaceholderText(/No emoji\./);
    expect(screen.queryByTestId("you-unsaved-bar")).toBeNull();

    fireEvent.change(rules, { target: { value: "No emoji." } });
    const bar = await screen.findByTestId("you-unsaved-bar");
    expect(bar.className).toMatch(/(^|\s)sticky(\s|$)/);
    expect(bar.className).not.toMatch(/bg-black/);
    expect(within(bar).getByRole("button", { name: /^Save/ })).toBeEnabled();
    expect(within(bar).getByRole("button", { name: /Discard/ })).toBeInTheDocument();
    // The header keeps its own Save (and Discard) — the bar adds, never moves.
    expect(screen.getAllByRole("button", { name: /^Save/ }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByRole("button", { name: /Discard/ }).length).toBeGreaterThanOrEqual(2);
  });

  it("the bar's Save PUTs the same /profile body as the header and then goes away", async () => {
    seedYou();
    render(<YouPage />);
    const rules = await screen.findByPlaceholderText(/No emoji\./);
    fireEvent.change(rules, { target: { value: "No emoji." } });
    const bar = await screen.findByTestId("you-unsaved-bar");
    fireEvent.click(within(bar).getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(H.puts).toHaveLength(1));
    expect(H.puts[0].path).toBe("/profile");
    expect((H.puts[0].body as { values: Record<string, unknown> }).values).toMatchObject({
      formatting_rules: "No emoji.",
      about: "I run a small CPA firm.",
    });
    await waitFor(() => expect(screen.queryByTestId("you-unsaved-bar")).toBeNull());
    expect(screen.getByText(/Saved — it applies from your next message\./)).toBeInTheDocument();
  });

  it("the bar's Discard puts the saved value back and sends nothing", async () => {
    seedYou();
    render(<YouPage />);
    const rules = (await screen.findByPlaceholderText(/No emoji\./)) as HTMLTextAreaElement;
    fireEvent.change(rules, { target: { value: "No emoji." } });
    const bar = await screen.findByTestId("you-unsaved-bar");
    fireEvent.click(within(bar).getByRole("button", { name: /Discard/ }));
    await waitFor(() => expect(screen.queryByTestId("you-unsaved-bar")).toBeNull());
    expect(rules.value).toBe("");
    expect(H.puts).toHaveLength(0);
  });
});

describe("/you — the preview admits the preferences and links to Memory (knows-about-me-model-mismatch)", () => {
  // v1.316.0 review: N is lessons.total - lessons.reflections (confirmed,
  // non-reflection user-scope rows — the pool apply_to_prompt draws from),
  // NEVER preferences.length: overview.py caps that list at MAX_PREFERENCES
  // (12). This fixture holds a capped list of 12 and 31 counted rows, one of
  // them a reflection, so only the real count reads "30".
  const MANY = {
    ...OVERVIEW,
    preferences: Array.from({ length: 12 }, (_, i) => ({
      id: `m${i}`,
      text: `Preference ${i}`,
      source: i % 3 === 0 ? "feedback" : "preference",
      weight: 3,
      created_at: "2026-09-18",
    })),
    lessons: { total: 31, reflections: 1, by_source: { preference: 20, feedback: 6, distilled: 4, reflection: 1 } },
  };

  it("'Plus 30 preferences and lessons' counts past the 12-row cap and links to /memory", async () => {
    seedYou(MANY);
    render(<YouPage />);
    await screen.findByText(/# Who you are working with/);
    const link = await waitFor(() => {
      const a = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href^="/memory"]')).find((x) =>
        /\b30 preferences and lessons\b/.test(x.closest("p, div")?.textContent ?? x.textContent ?? ""),
      );
      expect(a).toBeTruthy();
      return a!;
    });
    expect(link.getAttribute("href")).toMatch(/^\/memory\b/);
    const line = link.closest("p")!.textContent ?? "";
    expect(line).not.toMatch(/\b12\b/);
    // Feedback and distilled rows are in the count, so the line never says
    // the user TOLD Jarvis all of them.
    expect(line).not.toMatch(/you told Jarvis/i);
    expect(line).toMatch(/keeps about you/);
    // The exact model text stays on screen above it.
    expect(screen.getByText(/I run a small CPA firm\./, { selector: "pre" })).toBeInTheDocument();
  });

  it("one counted row is singular", async () => {
    seedYou({ ...OVERVIEW, lessons: { total: 3, reflections: 2, by_source: { preference: 1, reflection: 2 } } });
    render(<YouPage />);
    await screen.findByText(/# Who you are working with/);
    await waitFor(() => expect(document.body.textContent).toMatch(/\b1 preference or lesson\b/));
    expect(document.body.textContent).not.toMatch(/\b1 preferences\b/);
  });

  it("only reflections (nothing about the user) shows no line", async () => {
    seedYou({ ...OVERVIEW, lessons: { total: 4, reflections: 4, by_source: { reflection: 4 } } });
    render(<YouPage />);
    await screen.findByText(/# Who you are working with/);
    await flush(60);
    expect(document.querySelectorAll('a[href^="/memory"]')).toHaveLength(0);
    expect(document.body.textContent).not.toMatch(/Plus \d+ preference/);
  });

  it("a failed overview hides the line quietly (no guess, no error)", async () => {
    seedYou(new H.FakeApiError("boom", 500));
    render(<YouPage />);
    await screen.findByText(/# Who you are working with/);
    await flush(60);
    expect(document.querySelectorAll('a[href^="/memory"]')).toHaveLength(0);
    expect(document.body.textContent).not.toMatch(/Plus \d+ preference/);
    // The page's own cross-link to /train is still there.
    expect(screen.getByRole("link", { name: /Paste a few things you wrote/ })).toHaveAttribute("href", "/train");
  });
});

/* ======================================================================== */
/*  One search box, not three competing ones (memory-three-search-boxes)     */
/* ======================================================================== */

describe("Memory search hierarchy — Recall leads, the scoped boxes say 'only this tab'", () => {
  it("Working's box is titled 'Search only …', points at Recall, and drops the jargon", () => {
    const { container } = render(<WorkingMemory />);
    expect(screen.getByRole("heading", { name: /^Search only/ })).toBeInTheDocument();
    expect(container.textContent).toMatch(/Recall/);
    expect(container.textContent).not.toMatch(/session · project · user/);
    // Control: the field, the count and the button are all still here.
    expect(screen.getByPlaceholderText(/Search memory/)).toBeInTheDocument();
    expect(screen.getByLabelText("How many results to show")).toHaveValue(5);
    expect(screen.getByRole("button", { name: "Search" })).toBeInTheDocument();
  });

  it("the scope blurbs speak plainly — no 'key-values' or 'mid-run'", async () => {
    seedMemory();
    const { container } = render(<MemorySurface />);
    await screen.findByTestId("knows-about-you");
    expect(container.textContent).not.toMatch(/key-values|mid-run/);
    const scopes = /const SCOPES[\s\S]*?\n\];/.exec(src("components/memory/MemorySurface.tsx"))![0];
    expect(scopes).not.toMatch(/key-values|mid-run/);
    // The honesty pin on the lessons blurb stays (reflections are not sent).
    expect(scopes).toMatch(/not sent to the model/);
  });

  it("Recall with no query is one quiet line, not the big empty-state block", () => {
    render(<RecallSearch />);
    const card = screen.getByRole("heading", { name: /Recall/ }).closest("section") as HTMLElement;
    expect(within(card).queryByTestId("empty-state")).toBeNull();
    expect(screen.getByLabelText("Recall search query")).toBeInTheDocument();
  });

  it("control: Recall still searches every store and says when nothing matched", async () => {
    H.get["/memory/recall?q=quarterly&k=12"] = { results: [], by_source: {}, count: 0, query: "quarterly" };
    render(<RecallSearch />);
    fireEvent.change(screen.getByLabelText("Recall search query"), { target: { value: "quarterly" } });
    fireEvent.click(screen.getByRole("button", { name: "Recall" }));
    await screen.findByText("Nothing in memory matches that yet.");
    expect(H.gets).toContain("/memory/recall?q=quarterly&k=12");
  });

  it("Long-term's search strip is titled 'Search only …' and keeps Base + Results", async () => {
    H.get["/ltm/sources"] = { sources: [], active: [] };
    render(<LongTerm />);
    expect(await screen.findByRole("heading", { name: /^Search only/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Base")).toBeInTheDocument();
    expect(screen.getAllByLabelText("How many results to show").length).toBeGreaterThan(0);
  });

  it("the Import door sits in the scope row beside 'Memory view' (Working and Lessons)", async () => {
    for (const scope of ["working", "lessons"]) {
      seedMemory();
      H.search = `scope=${scope}`;
      render(<MemorySurface />);
      const link = screen.getByTestId("memory-import-link");
      expect(link).toHaveAttribute("href", "/memory?scope=longterm");
      expect(link.textContent).toMatch(/Import/);
      const row = screen.getByRole("group", { name: "Memory view" }).parentElement as HTMLElement;
      expect(row.contains(link)).toBe(true);
      expect(row.contains(screen.getByRole("tablist", { name: "Memory scope" }))).toBe(true);
      cleanup();
    }
  });

  it("control: the Long-term tab itself does not repeat the door", () => {
    seedMemory();
    H.search = "scope=longterm";
    render(<MemorySurface />);
    expect(screen.queryByTestId("memory-import-link")).toBeNull();
  });
});

/* ======================================================================== */
/*  Forget and preference actions — visible, finger-sized, two-press lessons */
/* ======================================================================== */

describe("Lessons Forget — reachable by keyboard and touch, and two presses (hidden-forget-and-tiny-pref-actions)", () => {
  async function renderLessons() {
    H.get["/lessons?limit=50"] = LESSONS;
    H.get["/improvement"] = IMPROVEMENT;
    render(<Lessons />);
    await screen.findAllByText("Keep answers short");
  }
  const forgetFor = (text: string) => {
    const li = screen
      .getAllByText(text)
      .map((e) => e.closest("li"))
      .find(Boolean) as HTMLElement;
    return within(li).getByTitle(/Forget this lesson/);
  };

  it("at rest: an aria-label, a focus-visible reveal, and visible on touch", async () => {
    await renderLessons();
    const btn = forgetFor("Keep answers short");
    expect(btn.getAttribute("aria-label") ?? "(no aria-label)").toMatch(/^Forget this lesson/);
    expect(btn.className).toMatch(/focus-visible:opacity-100/);
    if (/(^|\s)opacity-0(\s|$)/.test(btn.className)) {
      expect(btn.className).toMatch(/\[@media\(hover:none\)\]:opacity-(60|70|80|90|100)\b/);
    }
  });

  it("the first press arms ('Sure?'), only the second DELETEs", async () => {
    await renderLessons();
    const btn = forgetFor("Keep answers short");
    fireEvent.click(btn);
    await flush();
    expect(H.dels).toHaveLength(0);
    expect(btn.textContent).toMatch(/Sure\?/);
    expect(btn.getAttribute("aria-label") ?? "").toMatch(/again|sure/i);
    fireEvent.click(btn);
    await waitFor(() => expect(H.dels).toEqual(["/lessons/p1"]));
  });

  it("control: every lesson still has its Forget, plus Distill now", async () => {
    await renderLessons();
    expect(screen.getAllByTitle(/Forget this lesson/)).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Distill now/ })).toBeInTheDocument();
  });
});

const prefRow = (id: string, text: string) => ({
  id,
  text,
  status: "confirmed" as const,
  origin: "said",
  source: "preference",
  evidence: [],
  created_at: NOW,
  decided_at: NOW,
});

describe("Preference Edit / Forget — a finger-sized target, visible on touch", () => {
  it("both actions have at least a 24px hit area and show at full strength on touch", () => {
    const view: PreferencesView = {
      kept: [prefRow("k1", "Keep answers short")],
      suggested: [],
      never: [],
      scanSources: [],
    };
    render(<PreferenceSections view={view} apply={() => {}} onChanged={() => {}} />);
    for (const id of ["prefs-edit", "prefs-forget"]) {
      const b = screen.getByTestId(id);
      expect(b.className).not.toMatch(/(^|\s)py-px(\s|$)/);
      expect(b.className).toMatch(/(^|\s)(py-1|py-1\.5|py-2|min-h-6|min-h-\[2[4-9]px\])(\s|$)/);
    }
    const holder = screen.getByTestId("prefs-forget").parentElement as HTMLElement;
    expect(holder.className).toMatch(/\[@media\(hover:none\)\]:opacity-100/);
  });

  it("control: Forget still DELETEs the preference by id", async () => {
    const view: PreferencesView = {
      kept: [prefRow("k1", "Keep answers short")],
      suggested: [],
      never: [],
      scanSources: [],
    };
    render(<PreferenceSections view={view} apply={() => {}} onChanged={() => {}} />);
    const forget = screen.getByTestId("prefs-forget");
    fireEvent.click(forget);
    // A confirm press, if the implementer adds one, is allowed here.
    if (H.dels.length === 0 && /sure/i.test(forget.textContent ?? "")) fireEvent.click(forget);
    await waitFor(() => expect(H.dels).toEqual(["/memory/preferences/k1"]));
  });
});

/* ======================================================================== */
/*  KnowsAboutYou / ProfileShareRow on a phone (knows-about-you-phone-wrap…)  */
/* ======================================================================== */

const HOME = "C:\\Users\\dana\\.claude\\CLAUDE.md";
const CODEX_HOME = "C:\\Users\\dana\\.codex\\AGENTS.md";
const shareCli = (over: Record<string, unknown>) => ({
  cli: "claude-code",
  label: "Claude Code",
  vendor: "Anthropic",
  available: true,
  on: false,
  file_name: "CLAUDE.md",
  files: [],
  targets: [{ path: HOME, account: null }],
  last_written: null,
  drift: false,
  accounts_known: true,
  chars: 0,
  omitted: 0,
  ...over,
});
const SHARE = {
  clis: [
    shareCli({}),
    shareCli({
      cli: "codex",
      label: "Codex",
      vendor: "OpenAI",
      file_name: "AGENTS.md",
      targets: [{ path: CODEX_HOME, account: null }],
    }),
  ],
  limit: 4000,
};

describe("What Jarvis knows about you — reads well on a phone", () => {
  it("the person icon and the profile line share one row (no wrap; the text takes the room)", async () => {
    H.get["/memory/overview"] = OVERVIEW;
    render(<KnowsAboutYou />);
    const p = (await screen.findByText("Runs a small bookkeeping practice.")).closest("p") as HTMLElement;
    const row = p.parentElement as HTMLElement;
    expect(row.className).not.toMatch(/flex-wrap/);
    expect(row.className).toMatch(/(^|\s)flex(\s|$)/);
    expect(p.className).toMatch(/(^|\s)flex-1(\s|$)/);
    expect(p.className).toMatch(/(^|\s)min-w-0(\s|$)/);
    // Control: Edit still goes to /you.
    expect(within(p).getByRole("link", { name: "Edit" })).toHaveAttribute("href", "/you");
  });

  it("Share with Build says the shared part ONCE; each switch still names its file and who sees it", async () => {
    H.get["/memory/overview"] = OVERVIEW;
    H.get["/profile/share"] = SHARE;
    render(<KnowsAboutYou />);
    const row = await screen.findByTestId("profile-share");
    const text = row.textContent ?? "";
    expect(text.match(/nothing else Jarvis remembers/g) ?? []).toHaveLength(1);
    expect(within(row).getByTestId("profile-share-explain").textContent).toMatch(
      /Writes your profile and the preferences you said or kept — nothing else Jarvis remembers/,
    );
    const cc = screen.getByTestId("profile-share-sentence-claude-code").textContent ?? "";
    expect(cc).toContain(HOME);
    expect(cc).toMatch(/Anthropic sees it when Claude Code runs/);
    const cx = screen.getByTestId("profile-share-sentence-codex").textContent ?? "";
    expect(cx).toContain(CODEX_HOME);
    expect(cx).toMatch(/OpenAI sees it when Codex runs/);
    // Control: both switches, off by default, still there before any press.
    for (const id of ["claude-code", "codex"]) {
      expect(screen.getByTestId(`profile-share-switch-${id}`)).toHaveAttribute("aria-checked", "false");
    }
    expect(H.puts).toHaveLength(0);
  });
});

/* ======================================================================== */
/*  Skills — findable and openable on a small screen                         */
/* ======================================================================== */

const SKILLS = {
  skills: [
    { name: "meeting-summary", description: "Turn a transcript into minutes", source: "claude" },
    { name: "invoice-generator", description: "Make a branded invoice", source: "claude" },
    { name: "ledger-sum", description: "Totals a ledger for the invoice run", source: "user" },
  ],
  counts: { claude: 2, user: 1 },
};

const skillRow = (name: string) => screen.queryByText(name, { selector: "span" });

describe("Skills — a search box, and a tap that shows the instructions (skills-find-and-open-on-small-screens)", () => {
  async function renderSkills() {
    H.get["/skills"] = SKILLS;
    H.get["/skills/meeting-summary"] = {
      ...SKILLS.skills[0],
      instructions: "# Minutes\nSummarise the transcript into decisions and actions.",
    };
    render(<SkillsPage />);
    await waitFor(() => expect(skillRow("ledger-sum")).not.toBeNull());
  }

  it("typing filters by name", async () => {
    await renderSkills();
    fireEvent.change(screen.getByLabelText("Search skills"), { target: { value: "meet" } });
    await waitFor(() => expect(skillRow("invoice-generator")).toBeNull());
    expect(skillRow("meeting-summary")).not.toBeNull();
    expect(skillRow("ledger-sum")).toBeNull();
  });

  it("typing matches descriptions too, and combines with the source chips", async () => {
    await renderSkills();
    fireEvent.change(screen.getByLabelText("Search skills"), { target: { value: "invoice" } });
    await waitFor(() => expect(skillRow("meeting-summary")).toBeNull());
    expect(skillRow("invoice-generator")).not.toBeNull();
    expect(skillRow("ledger-sum")).not.toBeNull(); // matched by its description
    fireEvent.click(screen.getByRole("button", { name: /^Claude\b/ }));
    await waitFor(() => expect(skillRow("ledger-sum")).toBeNull());
    expect(skillRow("invoice-generator")).not.toBeNull();
  });

  it("on a narrow screen, choosing a skill scrolls its instructions into view", async () => {
    H.mq = (q) => q.includes("max-width: 1023px");
    await renderSkills();
    fireEvent.click(skillRow("meeting-summary")!.closest("button")!);
    await waitFor(() => expect(H.scrolls.some((s) => s.el.id === "skill-instructions")).toBe(true));
    const target = document.getElementById("skill-instructions")!;
    await waitFor(() => expect(within(target).getByText(/Summarise the transcript/)).toBeInTheDocument());
  });

  it("control: on a desktop the same choice does not scroll, and the instructions load", async () => {
    H.mq = () => false;
    await renderSkills();
    fireEvent.click(skillRow("meeting-summary")!.closest("button")!);
    await screen.findByText(/Summarise the transcript/);
    await flush(60);
    expect(H.scrolls).toHaveLength(0);
    // Rescan and New skill are still in the header.
    expect(screen.getByRole("button", { name: /Rescan/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /New skill/ })).toBeInTheDocument();
  });
});

function curatorView(over: Partial<SkillCuratorView> = {}): SkillCuratorView {
  return {
    enabled: true,
    last_sweep_at: null,
    last_result: null,
    settings: { idle_days: 30, min_age_days: 7 },
    archive_dir: "C:\\ij\\skills\\.archive",
    candidates: [],
    archived: [],
    ...over,
  } as SkillCuratorView;
}
const candidate = (name: string) => ({
  name,
  created_by: "agent",
  use_count: 0,
  inject_count: 0,
  last_used_at: null,
  age_days: 40,
  idle_days: 33,
  reason: "unused for 33 days",
});

describe("Skill tidy-up line in plain words", () => {
  it("nothing to do, never run", () => {
    const line = curatorHeadline(curatorView());
    expect(line).toMatch(/^Tidy-up/);
    expect(line).toMatch(/nothing to archive/i);
    expect(line).toMatch(/never run/);
    expect(line).not.toMatch(/Curator —|candidate|swept/);
  });

  it("the counts stay: 2 that could go, 3 archived, last run …", () => {
    const line = curatorHeadline(
      curatorView({
        candidates: [candidate("a"), candidate("b")] as SkillCuratorView["candidates"],
        archived: [1, 2, 3].map((i) => ({
          name: `old-${i}`,
          archived_at: "2026-09-01T00:00:00Z",
          description: "",
        })) as SkillCuratorView["archived"],
        last_sweep_at: "2026-10-06T10:00:00Z",
      }),
    );
    expect(line).toMatch(/^Tidy-up/);
    expect(line).toMatch(/\b2\b/);
    expect(line).toMatch(/\b3 archived\b/);
    expect(line).toMatch(/last run /);
    expect(line).not.toMatch(/Curator —|candidate|swept/);
  });
});

/* ======================================================================== */
/*  LongTerm's folder picker on <Modal> (carry-modal-focus-longterm)         */
/* ======================================================================== */

describe("Long-term folder picker uses the shared <Modal>", () => {
  async function openPicker() {
    H.get["/ltm/sources"] = { sources: [], active: [] };
    const view = render(<LongTerm />);
    fireEvent.click(await screen.findByTitle("Browse for a folder on this machine"));
    const dialog = await screen.findByRole("dialog", { name: "Pick a markdown folder" });
    return { view, dialog };
  }

  it("renders through the portal and takes focus", async () => {
    const { view, dialog } = await openPicker();
    expect(view.container.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
  });

  it("source: no hand-rolled `fixed inset-0` scrim left in LongTerm", () => {
    const text = src("components/memory/LongTerm.tsx");
    expect(text).not.toMatch(/fixed inset-0/);
    expect(text).toMatch(/from "@\/components\/Modal"/);
  });

  it("control: pick + 'Use this folder' fills the path; Cancel and the backdrop still close", async () => {
    const { dialog } = await openPicker();
    const use = within(dialog).getByRole("button", { name: "Use this folder" });
    expect(use).toBeDisabled();
    fireEvent.click(within(dialog).getByText("pick-notes"));
    expect(use).toBeEnabled();
    fireEvent.click(use);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByPlaceholderText(String.raw`C:\Users\me\notes`)).toHaveValue("C:\\notes");

    fireEvent.click(screen.getByTitle("Browse for a folder on this machine"));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByTitle("Browse for a folder on this machine"));
    const again = await screen.findByRole("dialog");
    fireEvent.click(again.parentElement!);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
