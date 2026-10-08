/**
 * UX wave 1, track T4 — phone layout, the two page-level behaviours that need
 * a real render (v1.313.0). Harness = docs-copy-v1232's (useApi mocked to read
 * `hooks.responses`, framer-motion mocked with `m`).
 *
 *  1. /documents: on a phone the FILE PATH input was squeezed to ~70 px
 *     ("C:\U") beside Browse… and Extract text. The row now wraps and the
 *     input's box takes a full line below sm (verifier: `min-w-[12rem]
 *     basis-full sm:basis-auto` on the existing input wrapper; the buttons keep
 *     shrink-0). Same for the Redact PII document row.
 *  2. /usage: the 12-month heatmap opened scrolled to the OLDEST week, so on a
 *     phone it showed Oct–Mar, all empty — "you've done nothing". It now opens
 *     on the latest weeks, ONCE (on first mount — never fighting a user who
 *     scrolled back, when the data refreshes).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const hooks = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { responses: {} as Record<string, unknown>, MockApiError };
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
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  wsUrl: (p: string) => `ws://test${p}`,
  sseUrl: (p: string) => `http://test${p}`,
  get: () => Promise.resolve({}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  api: () => Promise.resolve({}),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/documents",
}));

vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "whileHover",
    "whileTap",
    "whileInView",
    "viewport",
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
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

const DocumentsPage = (await import("@/app/documents/page")).default;
const UsagePage = (await import("@/app/usage/page")).default;

const tokens = (el: Element): string[] =>
  String((el as HTMLElement).className ?? "").split(/\s+/).filter(Boolean);

/** The child of `row` that holds `el` — the box that flexes inside the row. */
function rowChild(el: HTMLElement, sibling: HTMLElement): { box: HTMLElement; row: HTMLElement } {
  let box = el;
  while (box.parentElement && !box.parentElement.contains(sibling)) box = box.parentElement;
  return { box, row: box.parentElement as HTMLElement };
}

afterEach(() => {
  cleanup();
});

/* -------------------------------------------------------------------------- */
/*  /documents — the path rows wrap instead of crushing the input              */
/* -------------------------------------------------------------------------- */

describe("/documents path rows on a phone", () => {
  beforeEach(() => {
    hooks.responses = { "/documents/live": { docs: [] } };
  });

  it("Read: the row wraps and the path box takes a full line below sm", () => {
    render(<DocumentsPage />);
    const input = screen.getByPlaceholderText("C:\\Users\\you\\report.pdf");
    // v1.314.0: "Browse…" became "Pick from folders…" (Read is the first).
    const browse = screen.getAllByRole("button", { name: /Pick from folders…/ })[0];
    const { box, row } = rowChild(input, browse);
    expect(tokens(row)).toContain("flex-wrap");
    for (const need of ["min-w-[12rem]", "basis-full", "sm:basis-auto"]) {
      expect(tokens(box), `path box needs ${need}`).toContain(need);
    }
    // Anti-vacuity: both buttons are still in that row, unshrunk.
    const extract = screen.getByRole("button", { name: /Extract text/ });
    expect(row.contains(extract)).toBe(true);
    expect(tokens(browse)).toContain("shrink-0");
    expect(tokens(extract)).toContain("shrink-0");
  });

  it("Redact PII: the document row wraps the same way", () => {
    render(<DocumentsPage />);
    const input = screen.getByLabelText("Document to redact");
    // v1.314.0: Redact's "Browse" became "Pick from folders…" (the second).
    const browse = screen.getAllByRole("button", { name: /Pick from folders…/ })[1];
    const { box, row } = rowChild(input, browse);
    expect(row.contains(browse)).toBe(true);
    expect(tokens(row)).toContain("flex-wrap");
    for (const need of ["min-w-[12rem]", "basis-full", "sm:basis-auto"]) {
      expect(tokens(box), `redact box needs ${need}`).toContain(need);
    }
    // Anti-vacuity: the field keeps its placeholder.
    // v1.314.0: an example reads as one ("e.g."), not as a client's data.
    expect(input).toHaveAttribute("placeholder", "e.g. C:\\Users\\you\\Documents\\organizer.docx");
  });
});

/* -------------------------------------------------------------------------- */
/*  /usage — the heatmap opens on the latest weeks, once                       */
/* -------------------------------------------------------------------------- */

const SCROLLER = "usage-heatmap-scroller";
const FULL_WIDTH = 780; // 53+ week columns at the 14 px pitch
const VIEW_WIDTH = 330; // a phone's card

function usagePayload(): Record<string, unknown> {
  const today = new Date();
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const recent = new Date(today);
  recent.setDate(today.getDate() - 3);
  return {
    totals: { input_tokens: 1200, output_tokens: 300, cost_usd: 0.42, runs: 2 },
    by_day: [{ day: iso(recent), input_tokens: 1200, output_tokens: 300, cost_usd: 0.42, runs: 2 }],
    by_model: [],
  };
}

describe("/usage heatmap opens on the latest weeks", () => {
  const sw = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollWidth");
  const cw = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
  const st = (Element.prototype as unknown as { scrollTo?: unknown }).scrollTo;

  beforeEach(() => {
    const isScroller = (el: HTMLElement) => el.getAttribute("data-testid") === SCROLLER;
    Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
      configurable: true,
      get(this: HTMLElement) {
        return isScroller(this) ? FULL_WIDTH : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get(this: HTMLElement) {
        return isScroller(this) ? VIEW_WIDTH : 0;
      },
    });
    // jsdom has no scrollTo; accept either `scrollLeft = …` or `scrollTo(…)`.
    (Element.prototype as unknown as { scrollTo: unknown }).scrollTo = function (
      this: Element,
      a: number | ScrollToOptions,
      b?: number,
    ) {
      const left = typeof a === "number" ? a : (a?.left ?? this.scrollLeft);
      void b;
      this.scrollLeft = left;
    };
    const payload = usagePayload();
    hooks.responses = {
      "/usage?days=30": payload,
      "/usage?days=7": payload,
      "/usage?days=365": payload,
    };
  });

  afterEach(() => {
    if (sw) Object.defineProperty(HTMLElement.prototype, "scrollWidth", sw);
    if (cw) Object.defineProperty(HTMLElement.prototype, "clientWidth", cw);
    (Element.prototype as unknown as { scrollTo?: unknown }).scrollTo = st;
  });

  it("the scroller is scrolled to its end after it renders", async () => {
    render(<UsagePage />);
    const scroller = await screen.findByTestId(SCROLLER);
    await waitFor(() =>
      expect(scroller.scrollLeft).toBeGreaterThanOrEqual(FULL_WIDTH - VIEW_WIDTH),
    );
  });

  it("does not yank a user back when the data refreshes", async () => {
    render(<UsagePage />);
    const scroller = await screen.findByTestId(SCROLLER);
    await waitFor(() =>
      expect(scroller.scrollLeft).toBeGreaterThanOrEqual(FULL_WIDTH - VIEW_WIDTH),
    );
    // The user scrolls back to look at spring…
    scroller.scrollLeft = 40;
    // …and a refresh hands the page NEW objects for both series (a poll / a
    // reload returns fresh JSON), re-rendered through a range change.
    const fresh = usagePayload();
    hooks.responses = { "/usage?days=30": fresh, "/usage?days=7": fresh, "/usage?days=365": fresh };
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "7d" }).className).toContain("text-accent-soft"),
    );
    expect(screen.getByTestId(SCROLLER).scrollLeft).toBe(40);
  });

  it("the whole year, its tooltips and the legend are still there", async () => {
    // Located by its title (not the new test id) so this control is green on
    // the old code too: it proves the harness renders the heatmap at all.
    render(<UsagePage />);
    const card = screen.getByText("Daily activity — last 12 months").closest("section") as HTMLElement;
    expect(card).not.toBeNull();
    const cells = card.querySelectorAll("[title*='tokens']");
    expect(cells.length).toBe(365);
    expect(screen.getByText("Less")).toBeInTheDocument();
    expect(screen.getByText("More")).toBeInTheDocument();
    expect(screen.getByText("Daily activity — last 12 months")).toBeInTheDocument();
  });
});
