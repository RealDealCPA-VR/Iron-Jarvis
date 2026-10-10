/**
 * v1.314.0 — the coordinator's follow-ups to UX wave 2.
 *  - The demo strip publishes its height (`--ij-strip-h`) and the full-height
 *    modules subtract it: before, the strip pushed the chat composer's footer
 *    (Share, Approvals, the model button) below the window at 1440x900.
 *  - An empty state's press can mirror the page's busy state (Build's
 *    "New terminal" could start two terminals on a quick double-press).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const D = vi.hoisted(() => ({
  state: {
    online: true,
    checking: false,
    health: { default_provider: "mock", providers: [{ name: "claude-cli", available: true }] } as unknown,
  },
}));
vi.mock("@/lib/daemon", () => ({ useDaemon: () => D.state }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { SimulatedBanner } from "@/components/SimulatedBanner";
import { Empty } from "@/components/ui";

const read = (rel: string) =>
  readFileSync(join(process.cwd(), rel), "utf-8").replace(/\r\n/g, "\n");
const stripVar = () => document.documentElement.style.getPropertyValue("--ij-strip-h");

describe("the demo strip publishes its height", () => {
  let spy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    spy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ height: 41, width: 1000, top: 0, left: 0, right: 1000, bottom: 41, x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
    document.documentElement.style.removeProperty("--ij-strip-h");
  });
  afterEach(() => spy.mockRestore());

  it("sets --ij-strip-h to the strip's height while it shows, and 0px when it unmounts", () => {
    const r = render(<SimulatedBanner />);
    expect(screen.getByTestId("simulated-banner")).toBeInTheDocument();
    expect(stripVar()).toBe("41px");
    r.unmount();
    expect(stripVar()).toBe("0px");
  });

  it("is 0px when a model has been chosen (no strip)", () => {
    D.state.health = { default_provider: "claude-cli", providers: [{ name: "claude-cli", available: true }] };
    try {
      render(<SimulatedBanner />);
      expect(screen.queryByTestId("simulated-banner")).toBeNull();
      expect(stripVar()).toBe("0px");
    } finally {
      D.state.health = { default_provider: "mock", providers: [{ name: "claude-cli", available: true }] };
    }
  });

  it.each([
    ["app/chat/page.tsx", "md:h-[calc(100vh-4.5rem-var(--ij-strip-h,0px))]"],
    // v1.329.0 (calm chat wave 8, J4): the mission page's two text tabs now
    // sit ABOVE Your team's panel (the old left rail sat beside it), so the
    // panel takes 2rem more off the window; it still subtracts the strip.
    ["components/agents/mission/TeamScreen.tsx", "h-[calc(100vh-9rem-var(--ij-strip-h,0px))]"],
    ["app/agents/page.tsx", "h-[calc(100vh-7rem-var(--ij-strip-h,0px))]"],
    // v1.316.0 (UX wave 4, T3): the workflow canvas is no longer viewport-tall
    // (a capped `h-[min(64vh,680px)]`, so the builder and starters stay within
    // reach), so it has no strip to subtract; the bare-calc ban below still
    // runs on it.
    ["components/workflow/WorkflowCanvas.tsx", "h-[min(64vh,680px)]"],
    ["app/terminals/page.tsx", "lg:h-[calc(100vh-9rem-var(--ij-strip-h,0px))]"],
  ])("%s subtracts the strip from its full height", (rel, cls) => {
    const s = read(rel);
    expect(s).toContain(cls);
    // No full-height calc in these files that forgets the strip.
    // (Comments may quote the old value in backticks; class strings may not.)
    expect(s).not.toMatch(/["\s](md:|lg:)?h-\[calc\(100vh-[\d.]+rem\)\]/);
  });
});

describe("Empty — a press can mirror the page's busy state", () => {
  it("a disabled press is a disabled button that does nothing", () => {
    const go = vi.fn();
    render(<Empty action={{ label: "New terminal", onClick: go, disabled: true }}>No terminals yet.</Empty>);
    const b = screen.getByRole("button", { name: /New terminal/ }) as HTMLButtonElement;
    expect(b.disabled).toBe(true);
    act(() => {
      fireEvent.click(b);
    });
    expect(go).not.toHaveBeenCalled();
  });

  it("Build's empty stage passes the page's busy flag", () => {
    expect(read("app/terminals/page.tsx")).toContain(
      'action={{ label: "New terminal", onClick: () => void addTerminal(selectedPath), disabled: busy }}',
    );
  });
});
