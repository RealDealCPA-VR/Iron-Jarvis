/**
 * Calm chat J1 (v1.329.0): the downgrade banner states only what happened.
 *
 * The router publishes `provider.downgraded` with `used: "none"` on EVERY
 * refused turn (nothing answered, nothing stood in), and the banner never read
 * `used`: it said "Output came from the mock model" over a turn no mock ever
 * answered (the closing audit's fm__refusal shots). Now:
 *  - `used: "mock"` (the mock really answered): the old truth, calmly.
 *  - `used: "none"` from a chat turn (no session id): nothing. The chat shows
 *    the refusal itself, above the composer, with Retry.
 *  - `used: "none"` from a run (it carries a session id): one plain line that
 *    NOTHING answered, naming the endpoint by its label.
 * Tone tokens only, no em-dash, a quiet link to Connections.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { IJEvent } from "@/lib/types";

const H = vi.hoisted(() => ({ events: [] as IJEvent[] }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events, connected: true }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import {
  ProviderDowngradeBanner,
  downgradeNotice,
} from "@/components/ProviderDowngradeBanner";

const DASH = "—";

function ev(
  id: string,
  payload: Record<string, unknown>,
  session_id: string | null = null,
): IJEvent {
  return { id, type: "provider.downgraded", session_id, ts: `2026-10-10T00:00:0${id}Z`, payload };
}

const REFUSED_CHAT = ev("3", {
  requested: "fleet-fa2-anth",
  used: "none",
  reason: "not connected",
  label: "Office Spark",
});
const REFUSED_RUN = ev(
  "2",
  { requested: "fleet-fa2-anth", used: "none", reason: "not connected", label: "Office Spark" },
  "sess_42",
);
const MOCK = ev("1", {
  requested: "mock (default)",
  used: "mock",
  reason:
    "your default provider is 'mock' but a real provider is connected. Make it your default on the Connections page.",
});

beforeEach(() => {
  H.events = [];
});
afterEach(() => cleanup());

describe("downgradeNotice — what each event says", () => {
  it("a refused CHAT turn (no session) says nothing: the chat already shows it", () => {
    expect(downgradeNotice(REFUSED_CHAT)).toBeNull();
    expect(downgradeNotice({ ...REFUSED_CHAT, session_id: "chat" })).toBeNull();
  });

  it("a refused RUN says plainly that nothing answered, by the endpoint's label", () => {
    const n = downgradeNotice(REFUSED_RUN)!;
    expect(n.kind).toBe("refused");
    expect(n.title).toBe("Nothing answered a background job.");
    expect(n.detail).toBe("Office Spark: not connected.");
    expect(`${n.title} ${n.detail}`).not.toMatch(/mock/i);
  });

  it("a run without a label falls back to the provider id", () => {
    const n = downgradeNotice(ev("4", { requested: "ollama", used: "none", reason: "no answer in time" }, "s1"))!;
    expect(n.detail).toBe("ollama: no answer in time.");
  });

  it("a mock that REALLY answered keeps the old truth", () => {
    const n = downgradeNotice(MOCK)!;
    expect(n.kind).toBe("mock");
    expect(n.title).toBe("Output came from the mock model.");
    expect(n.detail).toMatch(/^Your default provider is 'mock'/);
    expect(n.detail).not.toContain(DASH);
  });

  it("ignores any other event type", () => {
    expect(downgradeNotice({ ...MOCK, type: "provider.failed" })).toBeNull();
    expect(downgradeNotice(null)).toBeNull();
  });
});

describe("ProviderDowngradeBanner — rendered", () => {
  it("renders NOTHING for a refused chat turn (the false mock claim is gone)", () => {
    H.events = [REFUSED_CHAT];
    const { container } = render(<ProviderDowngradeBanner />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/mock model/i)).toBeNull();
  });

  it("a refused run: one calm danger-tone line with a quiet link to Connections", () => {
    H.events = [REFUSED_RUN];
    render(<ProviderDowngradeBanner />);
    const line = screen.getByTestId("provider-downgrade");
    expect(line).toHaveAttribute("data-kind", "refused");
    expect(line).toHaveTextContent("Nothing answered a background job. Office Spark: not connected.");
    expect(line.textContent).not.toMatch(/mock/i);
    expect(screen.getByRole("link", { name: "Open Connections" })).toHaveAttribute("href", "/connections");
    expect(line.innerHTML).toContain("text-tone-danger");
  });

  it("a mock answer: the warn tone, no literal amber, no em-dash, no arrow glyph", () => {
    H.events = [MOCK];
    render(<ProviderDowngradeBanner />);
    const line = screen.getByTestId("provider-downgrade");
    expect(line).toHaveAttribute("data-kind", "mock");
    expect(line).toHaveTextContent("Output came from the mock model.");
    expect(line.innerHTML).toContain("text-tone-warn");
    expect(line.innerHTML).not.toMatch(/amber-|rose-/);
    expect(line.textContent).not.toContain(DASH);
    expect(line.textContent).not.toContain("→");
  });

  it("a newer chat refusal neither hides nor replaces a notice that is still up", () => {
    H.events = [REFUSED_CHAT, MOCK]; // newest first
    render(<ProviderDowngradeBanner />);
    expect(screen.getByTestId("provider-downgrade")).toHaveAttribute("data-kind", "mock");
  });

  it("Dismiss hides it until the next event that needs saying", () => {
    H.events = [MOCK];
    const { rerender } = render(<ProviderDowngradeBanner />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("provider-downgrade")).toBeNull();
    H.events = [REFUSED_RUN, MOCK];
    rerender(<ProviderDowngradeBanner />);
    expect(screen.getByTestId("provider-downgrade")).toHaveAttribute("data-kind", "refused");
  });
});

describe("source", () => {
  const src = readFileSync(join(__dirname, "..", "components", "ProviderDowngradeBanner.tsx"), "utf8");
  it("uses tone tokens only and whole-pixel sizes", () => {
    expect(src).not.toMatch(/\b(amber|rose|red|yellow|orange)-\d/);
    expect(src).not.toMatch(/text-\[\d+\.\d+px\]/);
    expect(src).not.toContain(` ${DASH} `);
  });
});
