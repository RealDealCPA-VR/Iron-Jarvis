/**
 * v1.455.0 (readiness Wave F, item 17, the dashboard part).
 *
 *  - The page-error screens (app/error.tsx inside the layout, and
 *    app/global-error.tsx when the layout itself broke) fold the raw text
 *    under Details instead of forcing it on the user, and say "go home"
 *    (never "the dashboard").
 *  - (Review: an account-switch line was dropped — the daemon never sends
 *    `route.account.from`; see the last block.)
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
import { readFileSync } from "node:fs";
import { join } from "node:path";

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

// Review (v1.455.0): the first cut drew "moved from the X account" off
// `route.account.from` / `why`, but NO daemon path sends those keys: a chat
// turn's `route.account` exists only on a turn PINNED to an account
// (`iron_proxy/choice.receipt`: id + name), and a pinned turn never moves; an
// unpinned turn that rotates accounts inside its adapter carries no
// `route.account` at all. A receipt line for a field nobody sends is a claim
// with nothing behind it, so it is not drawn.
describe("the receipt claims no account switch the daemon never reports", () => {
  it("TurnReceipt reads no `account.from` / `account.why`", () => {
    const src = readFileSync(join(__dirname, "..", "components", "chat", "TurnReceipt.tsx"), "utf8");
    expect(src).not.toMatch(/turn-account-moved/);
    expect(src).not.toMatch(/moved from the/);
  });

  it("the daemon's account receipt is still id + name only", () => {
    const py = readFileSync(
      join(__dirname, "..", "..", "src", "iron_jarvis", "iron_proxy", "choice.py"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    const body = py.slice(py.indexOf("def receipt("), py.indexOf("__all__"));
    expect(body).not.toMatch(/"from"/);
    expect(body).not.toMatch(/"why"/);
  });
});
