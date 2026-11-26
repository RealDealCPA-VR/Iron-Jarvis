/**
 * Next leaps 46-a (R2.19), review 2: the By switch while the next answer is
 * still on its way.
 *
 * The REAL useApi keeps the last path's answer while a new path loads. The
 * card must not read that answer as this choice's: pressing Client while its
 * answer is pending never says "Nothing used in this window." (there was use)
 * and never shows the folders' rows under Client; a new window never shows
 * the old window's figures as its own. Only the transport (`@/lib/api` get)
 * is mocked, and it holds the answers this suite chooses.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

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
    /** Answered at once. */
    answers: {} as Record<string, unknown>,
    /** Held until the test releases them. */
    held: new Map<string, (v: unknown) => void>(),
    asked: [] as string[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: S.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => null,
  get: (path: string) => {
    S.asked.push(path);
    if (path in S.answers) return Promise.resolve(S.answers[path]);
    return new Promise((resolve) => S.held.set(path, resolve));
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
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

import { UsageBy } from "@/components/usage/UsageBy";
import { EMPTY_WORDS } from "@/lib/usageBy";

const zero = { usd: 0, tokens: 0, measured: false, price_unknown_tokens: 0 };
function row(label: string, usd: number) {
  return {
    key: label.toLowerCase(),
    label,
    kind: "",
    folder: "",
    pay_per_use: { ...zero, usd, tokens: 100 },
    plan: { tokens: 0, window_share: null, window: "" },
    free: { tokens: 0 },
    asks: 0,
    runs: 1,
  };
}
function view(group: string, days: number, rows: unknown[]) {
  return { group, days, rows, rows_total: rows.length, rest: null, truncated: false, error: "", totals: row("t", 0) };
}

const FOLDER_30 = "/usage/by?group=folder&days=30";
const CLIENT_30 = "/usage/by?group=client&days=30";
const FOLDER_7 = "/usage/by?group=folder&days=7";

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  S.answers = { [FOLDER_30]: view("folder", 30, [row("Price list", 4.1)]) };
  S.held.clear();
  S.asked.length = 0;
});
afterEach(() => cleanup());

describe("the By switch while the next answer is pending", () => {
  it("never says nothing was used, nor shows the folders under Client, while Client loads", async () => {
    render(<UsageBy days={30} />);
    await settle();
    expect(screen.getByTestId("usage-by-row").textContent).toContain("Price list");

    fireEvent.click(screen.getByTestId("usage-by-client"));
    await settle();
    expect(S.asked).toContain(CLIENT_30);
    expect(S.held.has(CLIENT_30)).toBe(true); // still pending
    const panel = screen.getByRole("tabpanel");
    expect(panel.textContent ?? "").not.toContain(EMPTY_WORDS);
    expect(screen.queryAllByTestId("usage-by-row")).toHaveLength(0); // the folders are not the clients
    expect(screen.queryByTestId("usage-by-failed")).toBeNull();

    await act(async () => {
      S.held.get(CLIENT_30)?.(view("client", 30, [row("Hartleys", 1.5)]));
    });
    await settle();
    const rows = screen.getAllByTestId("usage-by-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("Hartleys");
    expect(rows[0].textContent).toContain("$1.50");
  });

  it("never shows the old window's figures as a new window's while it loads", async () => {
    const { rerender } = render(<UsageBy days={30} />);
    await settle();
    expect(screen.getByTestId("usage-by-row").textContent).toContain("$4.10");

    rerender(<UsageBy days={7} />);
    await settle();
    expect(S.held.has(FOLDER_7)).toBe(true);
    expect(screen.queryAllByTestId("usage-by-row")).toHaveLength(0);
    expect(screen.getByRole("tabpanel").textContent ?? "").not.toContain(EMPTY_WORDS);

    await act(async () => {
      S.held.get(FOLDER_7)?.(view("folder", 7, []));
    });
    await settle();
    // Answered, and empty: now it may say so.
    expect(screen.getByRole("tabpanel").textContent ?? "").toContain(EMPTY_WORDS);
  });

  it("says it could not read an answer that is not a view, never that nothing was used", async () => {
    S.answers[FOLDER_30] = { nope: true };
    render(<UsageBy days={30} />);
    await settle();
    const panel = screen.getByRole("tabpanel");
    expect(panel.textContent ?? "").not.toContain(EMPTY_WORDS);
    expect(screen.getByTestId("usage-by-failed").textContent).toContain("What it cost couldn't load.");
  });
});
