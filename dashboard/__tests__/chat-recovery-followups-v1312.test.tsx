/**
 * v1.312.0 review follow-ups (track B, chat page).
 *
 * 1. "Choose another model…" is decided by the SAME local countdown as the
 *    Retry button. Read from the polled cooldown instead, it stayed on screen
 *    for up to one health poll after Retry had come back.
 * 2. A "conversation" grant made at the arming cap lives only in the grant
 *    list. It must ride an escalation's `allow_tools` too, or the agent run
 *    re-asks for what the user just allowed.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RetryTurnButton } from "@/components/chat/RetryTurnButton";

describe("RetryTurnButton — the picker follows the local countdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("drops 'Choose another model…' the moment Retry comes back, even if the poll still says cooling", () => {
    const onChoose = vi.fn();
    const onRetry = vi.fn();
    // The prop stays at 2 the whole time: the health poll has not run again.
    render(
      <RetryTurnButton
        cooldownS={2}
        onRetry={onRetry}
        provider="claude-cli"
        down={false}
        onChooseModel={onChoose}
      />,
    );
    expect(screen.getByRole("button", { name: /^Retry in 2s$/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /^Choose another model…$/ }));
    expect(onChoose).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(2100);
    });
    expect(screen.getByRole("button", { name: /^Retry$/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Choose another model/ })).toBeNull();
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("keeps 'Choose another model…' while the provider is known down, with Retry enabled", () => {
    render(
      <RetryTurnButton
        cooldownS={0}
        onRetry={() => {}}
        provider="custom"
        down
        onChooseModel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /^Retry$/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^Choose another model…$/ })).toBeInTheDocument();
  });

  it("offers no picker when nothing is wrong", () => {
    render(
      <RetryTurnButton
        cooldownS={0}
        onRetry={() => {}}
        provider="claude-cli"
        down={false}
        onChooseModel={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /Choose another model/ })).toBeNull();
  });
});

// Source pin: CRLF normalised at the reader (Windows CI checks out with CRLF).
const page = readFileSync(join(process.cwd(), "app", "chat", "page.tsx"), "utf8").replace(
  /\r\n/g,
  "\n",
);

function bodyOf(name: string): string {
  const decl = new RegExp(`function ${name}\\b`).exec(page);
  if (!decl) throw new Error(`function ${name}() not found in app/chat/page.tsx`);
  let paren = 0;
  let afterParams = -1;
  for (let i = page.indexOf("(", decl.index); i < page.length; i++) {
    if (page[i] === "(") paren += 1;
    else if (page[i] === ")") {
      paren -= 1;
      if (paren === 0) {
        afterParams = i;
        break;
      }
    }
  }
  const open = page.indexOf("{", afterParams);
  let depth = 0;
  for (let i = open; i < page.length; i++) {
    if (page[i] === "{") depth += 1;
    else if (page[i] === "}") {
      depth -= 1;
      if (depth === 0) return page.slice(open, i + 1);
    }
  }
  throw new Error(`unbalanced body for ${name}()`);
}

describe("a conversation grant rides the escalation", () => {
  it("builds every escalation's allow_tools from the armed set PLUS the grants beyond it", () => {
    const body = bodyOf("sendAgent");
    expect(body).toMatch(
      /const extraGrants = grantedToolsRef\.current\.filter\(\(t\) => !armedNow\.includes\(t\)\);/,
    );
    const spreads = body.match(/allow_tools: armedNow\.slice\(0, MAX_TOOLS\)\.concat\(extraGrants\)/g) ?? [];
    // /continue, /agents/{slug}/spawn and POST /sessions.
    expect(spreads).toHaveLength(3);
    // A grant alone (nothing armed) still sends allow_tools.
    expect(body.match(/armedNow\.length \|\| extraGrants\.length/g) ?? []).toHaveLength(3);
  });
});
