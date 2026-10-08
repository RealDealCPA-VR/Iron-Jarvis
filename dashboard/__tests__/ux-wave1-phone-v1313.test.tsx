/**
 * UX wave 1, track T4 — the phone layout (v1.313.0).
 *
 * WHAT THE USER SAW at 390px (scratch screenshots, verified):
 *  - /schedules, /sessions, /skills ran past the right edge: form fields cut
 *    mid-word, the sessions table and its filters sliced off, the skill list's
 *    pin/archive buttons out of reach. CAUSE: a page grid written
 *    `grid gap-6 lg:grid-cols-3` has NO column template below lg, so its one
 *    implicit track is sized to the widest child's min-content (a table, a
 *    `truncate` line) and the `overflow-x-auto` wrapper inside never gets the
 *    chance to scroll. The fix is ONE primitive, `<PageGrid cols={2|3}>`
 *    (`grid gap-6 grid-cols-[minmax(0,1fr)] lg:grid-cols-N [&>*]:min-w-0`).
 *  - The email draft card's Copy button (the main way to use a drafted email)
 *    was clipped by the card on a phone.
 *  - Search fields (projects, marketplace, tools, …) showed an empty 36px gap
 *    where the magnifier should be: `.field` has `backdrop-filter`, which makes
 *    the LATER input its own stacking context, painted over the earlier
 *    absolutely-positioned icon. Fix per the verifier: `isolate` on the wrapper
 *    and `z-[1]` on the icon (NOT z-10, so it can never paint over popovers).
 *
 * What must NOT change (anti-vacuity controls below): the desktop layout (same
 * column count, same lg:col-span children), every button on the draft card and
 * its test ids, every search field's label and placeholder.
 *
 * Browser acceptance (not jsdom — layout needs a real engine): the scratch
 * probe `scratchpad/ux/probe_t4_phone.cjs 390 844` must print no FAIL.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ComponentType, ReactNode } from "react";
import { DraftCard } from "@/components/chat/DraftCard";
import { EMPTY_FILTERS, FilterBar } from "@/components/tools/FilterBar";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Source text, CRLF-normalised (this checkout is autocrlf). */
const src = (rel: string): string =>
  readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

/** Every static className string in a source file (`"..."` and {`...`}). */
function classStrings(text: string): { cls: string; index: number }[] {
  const out: { cls: string; index: number }[] = [];
  const re = /className=(?:"([^"]*)"|\{`([^`]*)`\})/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) out.push({ cls: m[1] ?? m[2] ?? "", index: m.index });
  return out;
}

const tokens = (cls: string): string[] => cls.split(/\s+/).filter(Boolean);

/**
 * The blowout pattern: a `grid` with a `gap-*` and an `lg:grid-cols-N`, but no
 * base column template (`grid-cols-1` / `grid-cols-[minmax(0,1fr)]` — both are
 * minmax(0,1fr) tracks) and no `[&>*]:min-w-0` on its children.
 */
function isBlowoutGrid(cls: string): boolean {
  const t = tokens(cls);
  if (!t.includes("grid")) return false;
  if (!t.some((x) => /^gap-/.test(x))) return false;
  if (!t.some((x) => /^lg:grid-cols-\d+$/.test(x))) return false;
  const hasBase = t.some((x) => /^grid-cols-(\d+|\[minmax\(0,1fr\)\])$/.test(x));
  const childGuard = t.includes("[&>*]:min-w-0");
  return !hasBase && !childGuard;
}

/** Every file T4 owns this wave (pages + the two shared components). */
const T4_FILES = [
  "app/schedules/page.tsx",
  "app/sessions/page.tsx",
  "app/skills/page.tsx",
  "app/settings/page.tsx",
  "app/documents/page.tsx",
  "app/secrets/page.tsx",
  "app/workflows/page.tsx",
  "app/usage/page.tsx",
  "app/projects/page.tsx",
  "app/marketplace/page.tsx",
  "app/tools/page.tsx",
  "components/chat/DraftCard.tsx",
  "components/memory/LongTerm.tsx",
];

/* -------------------------------------------------------------------------- */
/*  U1-4 — the PageGrid primitive                                              */
/* -------------------------------------------------------------------------- */

type PageGridProps = { cols: 2 | 3; className?: string; children?: ReactNode } & Record<
  string,
  unknown
>;

async function loadPageGrid(): Promise<ComponentType<PageGridProps> | null> {
  // A variable specifier so a missing module is a red ASSERTION, not a
  // transform error that hides every other test in this file.
  const spec = "@/components/PageGrid";
  try {
    const mod = (await import(/* @vite-ignore */ spec)) as {
      PageGrid?: ComponentType<PageGridProps>;
    };
    return mod.PageGrid ?? null;
  } catch {
    return null;
  }
}

describe("PageGrid — one phone-safe page grid (U1-4)", () => {
  it("cols={3}: a minmax(0,1fr) track below lg, three columns at lg, children may shrink", async () => {
    const PageGrid = await loadPageGrid();
    expect(PageGrid, "components/PageGrid.tsx must export a named PageGrid").not.toBeNull();
    if (!PageGrid) return;
    const { container } = render(
      <PageGrid cols={3}>
        <div className="lg:col-span-1">left</div>
        <div className="lg:col-span-2">right</div>
      </PageGrid>,
    );
    const grid = container.firstElementChild as HTMLElement;
    const t = tokens(grid.className);
    for (const need of ["grid", "gap-6", "grid-cols-[minmax(0,1fr)]", "lg:grid-cols-3", "[&>*]:min-w-0"]) {
      expect(t, `PageGrid cols=3 class must include ${need}`).toContain(need);
    }
    expect(t).not.toContain("lg:grid-cols-2");
    // Anti-vacuity: the children (and their desktop spans) arrive untouched.
    expect(screen.getByText("left").className).toBe("lg:col-span-1");
    expect(screen.getByText("right").className).toBe("lg:col-span-2");
  });

  it("cols={2}: two columns at lg; an extra className is merged, not replacing the guard", async () => {
    const PageGrid = await loadPageGrid();
    expect(PageGrid).not.toBeNull();
    if (!PageGrid) return;
    const { container } = render(
      <PageGrid cols={2} className="mt-2">
        <div>a</div>
      </PageGrid>,
    );
    const t = tokens((container.firstElementChild as HTMLElement).className);
    expect(t).toContain("lg:grid-cols-2");
    expect(t).not.toContain("lg:grid-cols-3");
    expect(t).toContain("grid-cols-[minmax(0,1fr)]");
    expect(t).toContain("[&>*]:min-w-0");
    expect(t).toContain("mt-2");
  });

  it("writes both lg column classes as LITERALS (Tailwind's JIT never sees `lg:grid-cols-${cols}`)", () => {
    let text = "";
    try {
      text = src("components/PageGrid.tsx");
    } catch {
      /* missing file -> red below */
    }
    expect(text, "components/PageGrid.tsx must exist").not.toBe("");
    expect(text).toContain("lg:grid-cols-2");
    expect(text).toContain("lg:grid-cols-3");
    expect(text).toContain("grid-cols-[minmax(0,1fr)]");
    expect(text).toContain("[&>*]:min-w-0");
    expect(text).not.toMatch(/lg:grid-cols-\$\{/);
  });
});

/* -------------------------------------------------------------------------- */
/*  The blowing-out page grids are gone from every T4 file                     */
/* -------------------------------------------------------------------------- */

describe("no page grid in a T4 file can widen the page on a phone", () => {
  it("the detector is real: it flags today's pattern and passes the fixed ones", () => {
    // Anti-vacuity for the pin below.
    expect(isBlowoutGrid("grid gap-6 lg:grid-cols-3")).toBe(true);
    expect(isBlowoutGrid("grid gap-3 sm:grid-cols-2 lg:grid-cols-3")).toBe(true);
    expect(isBlowoutGrid("grid gap-6 grid-cols-[minmax(0,1fr)] lg:grid-cols-3 [&>*]:min-w-0")).toBe(false);
    expect(isBlowoutGrid("grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3")).toBe(false);
    expect(isBlowoutGrid("grid gap-6 lg:grid-cols-2 [&>*]:min-w-0")).toBe(false);
    expect(isBlowoutGrid("flex gap-6")).toBe(false);
  });

  it.each(T4_FILES)("%s has no `grid gap-* lg:grid-cols-N` without a minmax(0,1fr) track or min-w-0 children", (rel) => {
    const offenders = classStrings(src(rel))
      .filter(({ cls }) => isBlowoutGrid(cls))
      .map(({ cls }) => cls);
    expect(offenders).toEqual([]);
  });

  // Desktop must not change: the same number of page columns, through PageGrid.
  const PAGE_GRIDS: [string, 2 | 3, number][] = [
    ["app/schedules/page.tsx", 3, 1],
    ["app/sessions/page.tsx", 3, 1],
    ["app/skills/page.tsx", 3, 1],
    ["app/settings/page.tsx", 3, 1],
    ["app/secrets/page.tsx", 3, 1],
    ["app/documents/page.tsx", 2, 1],
    ["components/memory/LongTerm.tsx", 3, 2],
  ];

  it.each(PAGE_GRIDS)("%s lays out with <PageGrid cols={%i}> (x%i) and keeps its desktop spans", (rel, cols, n) => {
    const text = src(rel);
    expect(text).toMatch(/import\s*\{\s*PageGrid\s*\}\s*from\s*"@\/components\/PageGrid"/);
    const uses = text.match(new RegExp(`<PageGrid\\s+cols=\\{${cols}\\}`, "g")) ?? [];
    expect(uses.length).toBe(n);
    expect(text).not.toMatch(/"grid gap-6 lg:grid-cols-\d/);
    // Anti-vacuity: the lg:col-span children that shape the desktop layout
    // are still there (counts as of v1.312.0; documents' halves have none).
    const spans = (text.match(/lg:col-span-\d/g) ?? []).length;
    const before: Record<string, number> = {
      "app/schedules/page.tsx": 2,
      "app/sessions/page.tsx": 2,
      "app/skills/page.tsx": 2,
      "app/settings/page.tsx": 2,
      "app/secrets/page.tsx": 2,
      "app/documents/page.tsx": 0,
      "components/memory/LongTerm.tsx": 4,
    };
    expect(spans).toBe(before[rel]);
  });
});

/* -------------------------------------------------------------------------- */
/*  Search icons paint ON TOP of their .field input                            */
/* -------------------------------------------------------------------------- */

/**
 * Every leading icon (`pointer-events-none absolute left-* top-1/2`) whose
 * sibling input is a `.field` (backdrop-filter => its own stacking context).
 */
function leadingFieldIcons(text: string): { icon: string; wrapper: string; at: number }[] {
  const all = classStrings(text);
  const out: { icon: string; wrapper: string; at: number }[] = [];
  all.forEach(({ cls, index }, i) => {
    const t = tokens(cls);
    if (!(t.includes("pointer-events-none") && t.includes("absolute") && t.includes("top-1/2"))) return;
    if (!t.some((x) => /^left-/.test(x))) return;
    // The input that follows (within the same wrapper, a few hundred chars on).
    const after = text.slice(index, index + 700);
    const input = /<input[\s\S]*?className="([^"]*)"/.exec(after);
    if (!input || !tokens(input[1]).includes("field")) return;
    // The wrapper = the className of the tag opened just before the icon's tag.
    const tagStart = text.lastIndexOf("<", index);
    const prev = all.filter((c) => c.index < tagStart).pop();
    out.push({ icon: cls, wrapper: prev?.cls ?? "", at: i });
  });
  return out;
}

describe("search-field icons are visible (the verifier's isolate + z-[1])", () => {
  const FILES: [string, number][] = [
    ["app/projects/page.tsx", 1],
    ["app/marketplace/page.tsx", 1],
    ["app/documents/page.tsx", 2],
    ["components/memory/LongTerm.tsx", 2],
    // NOT T4-owned — see the report: the coordinator must assign it.
    ["components/tools/FilterBar.tsx", 1],
  ];

  it.each(FILES)("%s: every leading icon over a .field is z-[1] in an isolated relative wrapper", (rel, n) => {
    const sites = leadingFieldIcons(src(rel));
    // Anti-vacuity: the detector still finds the sites (none silently dropped).
    expect(sites.length).toBe(n);
    for (const s of sites) {
      expect(tokens(s.icon), `icon "${s.icon}"`).toContain("z-[1]");
      expect(tokens(s.icon)).not.toContain("z-10");
      expect(tokens(s.wrapper), `wrapper "${s.wrapper}"`).toContain("relative");
      expect(tokens(s.wrapper), `wrapper "${s.wrapper}"`).toContain("isolate");
    }
  });

  it("the sessions search (no .field, icon already visible) is not swept in", () => {
    // Control: a plain input is not a stacking context; the detector must not
    // demand a change there.
    expect(leadingFieldIcons(src("app/sessions/page.tsx"))).toEqual([]);
  });

  it("each field keeps its label and placeholder", () => {
    expect(src("app/projects/page.tsx")).toContain('aria-label="Filter projects by name"');
    expect(src("app/projects/page.tsx")).toContain('placeholder="Filter projects by name…"');
    expect(src("app/marketplace/page.tsx")).toContain('aria-label="Search connectors"');
    expect(src("app/marketplace/page.tsx")).toContain('placeholder="Search connectors…"');
  });

  it("FilterBar renders the magnifier above its input, and the input is unchanged", () => {
    const { container } = render(
      <FilterBar value={EMPTY_FILTERS} onChange={() => {}} counts={{ builtin: 3, extension: 2 }} />,
    );
    const input = screen.getByTestId("tool-search");
    expect(input).toHaveAttribute("aria-label", "Filter tools and extensions");
    expect(input).toHaveAttribute("placeholder", "Filter tools and extensions…");
    const icon = container.querySelector("svg.lucide-search") as SVGElement | null;
    expect(icon).not.toBeNull();
    const iconCls = tokens(icon!.getAttribute("class") ?? "");
    expect(iconCls).toContain("z-[1]");
    expect(tokens((icon!.parentElement as HTMLElement).className)).toContain("isolate");
  });
});

/* -------------------------------------------------------------------------- */
/*  The email draft card's actions wrap instead of being clipped               */
/* -------------------------------------------------------------------------- */

describe("DraftCard footer wraps on a phone, groups right on desktop", () => {
  function renderCard() {
    return render(
      <DraftCard subject="Missing bank statements for March" text={"Hi Dana,\n\nAll set."}>
        <p>Hi Dana,</p>
      </DraftCard>,
    );
  }

  it("the action row wraps and no longer spreads the buttons with justify-between", () => {
    renderCard();
    const footer = screen.getByTestId("draft-note").parentElement as HTMLElement;
    const t = tokens(footer.className);
    expect(t).toContain("flex");
    expect(t).toContain("flex-wrap");
    expect(t).not.toContain("justify-between");
  });

  it("the note takes its own line on a phone and pushes the buttons right on desktop", () => {
    renderCard();
    const t = tokens(screen.getByTestId("draft-note").className);
    expect(t).toContain("basis-full");
    expect(t).toContain("sm:basis-auto");
    expect(t).toContain("sm:mr-auto");
  });

  it("every action is still there, in the same order, with the same test ids", () => {
    renderCard();
    const footer = screen.getByTestId("draft-note").parentElement as HTMLElement;
    const save = screen.getByTestId("draft-save-draft");
    const send = screen.getByTestId("draft-send");
    const copy = screen.getByRole("button", { name: /^copy$/i });
    for (const b of [save, send, copy]) expect(footer.contains(b)).toBe(true);
    expect(save.textContent).toMatch(/Save to Drafts/);
    expect(send.textContent).toMatch(/Send…/);
    // Order: Save to Drafts, Send…, Copy.
    const order = [...footer.querySelectorAll("button")];
    expect(order.indexOf(save as HTMLButtonElement)).toBeLessThan(order.indexOf(send as HTMLButtonElement));
    expect(order.indexOf(send as HTMLButtonElement)).toBeLessThan(order.indexOf(copy as HTMLButtonElement));
    // The subject copy path is untouched.
    expect(screen.getByRole("button", { name: "Copy subject" })).toBeInTheDocument();
  });
});
