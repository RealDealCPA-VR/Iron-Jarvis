/**
 * v1.455.0 (readiness Wave F, item 17, the dashboard part).
 *
 *  - The page-error screens (app/error.tsx inside the layout, and
 *    app/global-error.tsx when the layout itself broke) fold the raw text
 *    under Details instead of forcing it on the user, and say "go home"
 *    (never "the dashboard").
 *  - An account switch in a chat turn is said on the receipt ("moved from
 *    the Personal account"), read defensively off `route.account.from`
 *    (lane F2 sends it); a route with no switch says nothing new.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import RouteError from "@/app/error";
import GlobalError from "@/app/global-error";
import { accountMoveWords } from "@/components/chat/TurnReceipt";

afterEach(cleanup);

const RAW = "TypeError: Cannot read properties of undefined (reading 'map')";

describe("the page-error screen folds the raw text", () => {
  it("app/error.tsx: plain words, Details closed, the raw text only inside it", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const err = Object.assign(new Error(RAW), { digest: "abc123" });
    render(<RouteError error={err} reset={() => {}} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/go home/i);
    expect(alert.textContent).not.toMatch(/dashboard/i);
    expect(alert.textContent).not.toContain("TypeError");
    const details = screen.getByTestId("page-error-details") as HTMLDetailsElement;
    expect(details.tagName).toBe("DETAILS");
    expect(details.open).toBe(false);
    expect(details.textContent).toContain(RAW);
    expect(details.textContent).toContain("digest: abc123");
    expect(screen.getByRole("link", { name: /Go home/ }).getAttribute("href")).toBe("/");
  });

  it("app/global-error.tsx: the same fold, self-contained", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const html = renderToStaticMarkup(<GlobalError error={new Error(RAW)} reset={() => {}} />);
    expect(html).toMatch(/<details[^>]*data-testid="page-error-details"/);
    // The raw text sits inside the fold, after its summary.
    const fold = html.slice(html.indexOf("<details"));
    expect(fold).toContain("<summary");
    expect(fold).toContain("Cannot read properties of undefined");
    expect(html.slice(0, html.indexOf("<details"))).not.toContain("Cannot read properties");
    expect(html).not.toMatch(/rendering the dashboard/);
    expect(html).toMatch(/go home/i);
  });
});

describe("an account switch in chat is said", () => {
  it("names the account it moved from, with the daemon's reason", () => {
    expect(
      accountMoveWords({ account: { id: "w", name: "Work", from: "Personal", why: "Personal hit its 5-hour limit." } }),
    ).toEqual({ text: "moved from the Personal account", why: "Personal hit its 5-hour limit." });
  });

  it("CONTROL: no switch, junk or an older daemon says nothing", () => {
    expect(accountMoveWords({ account: { id: "w", name: "Work" } })).toBeNull();
    expect(accountMoveWords({ account: { from: "   " } })).toBeNull();
    expect(accountMoveWords({ account: { from: 3 as unknown as string } })).toBeNull();
    expect(accountMoveWords({ account: null })).toBeNull();
    expect(accountMoveWords(null)).toBeNull();
  });
});
