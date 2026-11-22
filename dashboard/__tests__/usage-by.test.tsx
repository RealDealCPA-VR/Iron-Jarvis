/**
 * Next leaps 46-a (R2.19): the Usage page's By switch — what everything cost,
 * by folder, client, automation and agent.
 *
 *  - The card is ON the Usage page (no new page, no sidebar item) and reads
 *    `GET /usage/by?group=…&days=…` once per choice, never on a poll.
 *  - Each row shows three figures apart: Pay per use (money, marked
 *    measured or estimated), Your plan (tokens, NEVER money) and Free.
 *  - An automation row says how many times it asked you; other groups do not.
 *  - A price nobody knows reads "price not known", never $0.00.
 *  - A folder row reads its own name, the whole path in its title.
 *
 * The REAL UsageBy card and Usage page; only the transport is mocked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const S = vi.hoisted(() => {
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
    responses: {} as Record<string, unknown>,
    once: [] as (string | null)[],
    polled: [] as (string | null)[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: S.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => null,
  get: (path: string) => Promise.resolve(S.responses[path] ?? {}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => {
  const state = (path: string | null) => ({
    data: path ? (S.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  });
  return {
    useApi: (path: string | null) => {
      S.once.push(path);
      return state(path);
    },
    usePolledApi: (path: string | null) => {
      S.polled.push(path);
      return state(path);
    },
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, unauthorized: false, requestError: false, checking: false, health: null, refresh: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/usage",
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

import { UsageBy, FIRST_ROWS } from "@/components/usage/UsageBy";
import UsagePage from "@/app/usage/page";
import { decodeUsageBy, planWords } from "@/lib/usageBy";

const zero = { usd: 0, tokens: 0, measured: false, price_unknown_tokens: 0 };
function row(label: string, over: Record<string, unknown> = {}) {
  return {
    key: label.toLowerCase(),
    label,
    kind: "",
    folder: "",
    pay_per_use: { ...zero },
    plan: { tokens: 0, window_share: null, window: "" },
    free: { tokens: 0 },
    asks: 0,
    runs: 1,
    ...over,
  };
}
function view(group: string, rows: unknown[], over: Record<string, unknown> = {}) {
  return { group, days: 30, rows, rows_total: rows.length, rest: null, truncated: false, error: "",
           totals: row("t"), ...over };
}

const RULE = row("Rule “invoice rule”", {
  key: "reflex:invoice rule",
  kind: "Rule",
  pay_per_use: { usd: 4.1, tokens: 3400, measured: true, price_unknown_tokens: 0 },
  asks: 2,
});
const NIGHTLY = row("Schedule “nightly brief”", {
  key: "schedule:nightly brief",
  kind: "Schedule",
  pay_per_use: { usd: 1, tokens: 450000, measured: false, price_unknown_tokens: 0 },
  free: { tokens: 8000 },
});
// A plan row, with a stray money field the page must never show.
const PLAN = row("Not an automation", {
  key: "",
  plan: { tokens: 6000, window_share: null, window: "", usd: 2 },
});

beforeEach(() => {
  S.responses = {
    "/usage/by?group=folder&days=30": view("folder", [
      row("C:\\Work\\Price list", { key: "c:/work/price list", folder: "C:\\Work\\Price list",
                                     pay_per_use: { usd: 4.1, tokens: 3400, measured: true, price_unknown_tokens: 0 },
                                     asks: 2 }),
      row("No folder", { key: "", pay_per_use: { usd: 0, tokens: 320, measured: false, price_unknown_tokens: 320 } }),
    ]),
    "/usage/by?group=automation&days=30": view("automation", [RULE, NIGHTLY, PLAN]),
    "/usage/by?group=automation&days=7": view("automation", [], { days: 7 }),
  };
  S.once.length = 0;
  S.polled.length = 0;
});
afterEach(() => cleanup());

describe("the By switch on the Usage page", () => {
  it("is a card on the Usage page that reads /usage/by once, never on a poll", () => {
    render(<UsagePage />);
    const tabs = screen.getByRole("tablist", { name: "What it cost, by" });
    expect(within(tabs).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Folder", "Client", "Automation", "Agent",
    ]);
    expect(S.once).toContain("/usage/by?group=folder&days=30");
    expect(S.polled.some((p) => (p ?? "").startsWith("/usage/by"))).toBe(false);
  });

  it("shows an automation's money, its mark and how many times it asked", () => {
    render(<UsageBy days={30} />);
    fireEvent.click(screen.getByTestId("usage-by-automation"));
    expect(S.once).toContain("/usage/by?group=automation&days=30");
    const rows = screen.getAllByTestId("usage-by-row");
    const rule = rows[0];
    expect(rule.textContent).toContain("Rule “invoice rule”");
    expect(within(rule).getByTestId("usage-by-pay").textContent).toContain("$4.10");
    expect(within(rule).getByTestId("usage-by-mark").textContent).toBe("measured");
    expect(within(rule).getByTestId("usage-by-asked").textContent).toBe("Asked you twice");
    const nightly = rows[1];
    expect(within(nightly).getByTestId("usage-by-mark").textContent).toBe("estimated");
    expect(within(nightly).getByTestId("usage-by-free").textContent).toContain("8,000 tokens");
    expect(within(nightly).queryByTestId("usage-by-asked")).toBeNull();
  });

  it("shows plan work in tokens, never as money", () => {
    render(<UsageBy days={30} />);
    fireEvent.click(screen.getByTestId("usage-by-automation"));
    const plan = screen.getAllByTestId("usage-by-row")[2];
    const cell = within(plan).getByTestId("usage-by-plan").textContent ?? "";
    expect(cell).toContain("6,000 tokens");
    expect(cell).not.toContain("$");
    expect(within(plan).getByTestId("usage-by-pay").querySelector("dd")?.textContent).toBe("—");
    expect(screen.getByTestId("usage-by-note").textContent).toContain("never as money");
    expect(planWords({ tokens: 6000, window_share: 40, window: "5-hour" })).toBe(
      "6,000 tokens, about 40% of a 5-hour window",
    );
  });

  it("says a price nobody knows instead of $0.00, and a folder by its own name", () => {
    render(<UsageBy days={30} />);
    const rows = screen.getAllByTestId("usage-by-row");
    // The name is the folder's own; the whole path is its title and the muted line.
    expect(rows[0].querySelector("span")?.textContent).toBe("Price list");
    expect(rows[0].querySelector("[title='C:\\\\Work\\\\Price list']")).not.toBeNull();
    // Asks are said for automations only.
    expect(within(rows[0]).queryByTestId("usage-by-asked")).toBeNull();
    const unknown = within(rows[1]).getByTestId("usage-by-pay").textContent ?? "";
    expect(unknown).toContain("320 tokens");
    expect(unknown).toContain("price not known");
    expect(unknown).not.toContain("$0.00");
  });

  it("reads again for another window, and says when nothing was used", () => {
    const { rerender } = render(<UsageBy days={30} />);
    fireEvent.click(screen.getByTestId("usage-by-automation"));
    rerender(<UsageBy days={7} />);
    expect(S.once).toContain("/usage/by?group=automation&days=7");
    expect(screen.getByText("Nothing used in this window.")).toBeTruthy();
  });

  it("lists the first rows and shows the rest on a press", () => {
    const many = Array.from({ length: FIRST_ROWS + 3 }, (_, i) => row(`Agent ${i}`, { key: `a${i}` }));
    S.responses["/usage/by?group=agent&days=30"] = view("agent", many);
    render(<UsageBy days={30} />);
    fireEvent.click(screen.getByTestId("usage-by-agent"));
    expect(screen.getAllByTestId("usage-by-row")).toHaveLength(FIRST_ROWS);
    fireEvent.click(screen.getByTestId("usage-by-more"));
    expect(screen.getAllByTestId("usage-by-row")).toHaveLength(FIRST_ROWS + 3);
  });

  it("decodes defensively: a name is text, a junk answer is nothing", () => {
    expect(decodeUsageBy(null)).toBeNull();
    expect(decodeUsageBy({ rows: "no" })).toBeNull();
    const v = decodeUsageBy({ group: "planet", rows: [{ label: "<b>x</b>", pay_per_use: { usd: -3 } }, { label: "" }] });
    expect(v?.group).toBe("folder");
    expect(v?.rows).toHaveLength(1);
    expect(v?.rows[0].label).toBe("<b>x</b>");
    expect(v?.rows[0].pay_per_use.usd).toBe(0);
  });
});
