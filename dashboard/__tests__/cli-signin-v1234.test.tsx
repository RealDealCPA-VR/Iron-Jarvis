/**
 * v1.234.0 — installed is not signed in, on the two surfaces the user sees.
 *
 * A logged-out `claude` CLI read "Detected — ready to use" on Connections and
 * the composer's preflight said "isn't reachable" (sending the user to debug
 * an endpoint). Both now name the state and the remedy.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PreflightNote } from "@/components/chat/PreflightNote";

const DASH = join(__dirname, "..");
// CRLF-normalised at the reader: the CI runner checks out with autocrlf.
const read = (p: string) => readFileSync(join(DASH, p), "utf-8").replace(/\r\n/g, "\n");

describe("PreflightNote (v1.234.0)", () => {
  it("names the sign-in remedy for a signed-out claude CLI", () => {
    render(<PreflightNote provider="claude-cli" available={false} signedOut />);
    const row = screen.getByTestId("ij-preflight-note");
    expect(row.getAttribute("data-kind")).toBe("signed-out");
    expect(row.textContent).toMatch(/installed but not signed in/);
    expect(row.textContent).toMatch(/run `claude` in a terminal, then \/login/i);
    expect(row.textContent).not.toMatch(/isn't reachable/);
  });

  it("says codex login for codex", () => {
    render(<PreflightNote provider="codex-cli" available={false} signedOut />);
    expect(screen.getByTestId("ij-preflight-note").textContent).toMatch(/codex login/);
  });

  it("keeps the unreachable wording when not signed-out", () => {
    render(<PreflightNote provider="ollama" available={false} />);
    const row = screen.getByTestId("ij-preflight-note");
    expect(row.getAttribute("data-kind")).toBe("unreachable");
    expect(row.textContent).toMatch(/isn't reachable/);
  });

  it("is wired from the chat composer with the signed-out map", () => {
    const page = read("app/chat/page.tsx");
    const at = page.indexOf("<PreflightNote");
    expect(at).toBeGreaterThan(-1);
    const block = page.slice(at, page.indexOf("/>", at));
    expect(block).toMatch(/signedOut=\{[\s\S]*signedOutByProvider\?\.\[/);
  });
});

describe("Connections CLI row (v1.234.0)", () => {
  const page = read("app/connections/page.tsx");

  it("renders the third state with the remedy, not 'Not detected'", () => {
    expect(page).toMatch(/Installed — not signed in/);
    expect(page).toMatch(/cli-signed-out-\$\{info\.provider\}/);
    expect(page).toMatch(/"claude-cli": "run `claude` in a terminal, then \/login, then Re-detect"/);
    expect(page).toMatch(/"codex-cli": "run `codex login` in a terminal, then Re-detect"/);
  });

  it("derives the state from the /health row's installed + signed_in", () => {
    expect(page).toMatch(/Boolean\(p\.installed\) && p\.signed_in === false/);
    expect(page).toMatch(/signedOut=\{isSignedOut\(info\.provider\)\}/);
  });
});
