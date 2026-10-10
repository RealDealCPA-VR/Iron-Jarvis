/**
 * v1.329.0 — calm chat wave 9, K1: the chat never claims a mock on a refusal,
 * and its last notices are plain and calm.
 *
 * 1. The live step line (components/chat/stepLabel) said "Model not connected
 *    — using offline mock" for EVERY provider.downgraded event. Since wave 8
 *    (J1) the router publishes used:"none" when nothing answered (a refusal)
 *    and used:"mock" only when the mock really answered. The line now reads
 *    `used` and says the truth for each, and says nothing false when `used`
 *    is missing (an older daemon).
 * 2. The PreflightNote's cooldown sentence (and its siblings) are plain
 *    sentences.
 * 3. The chat page's notices that only show in certain states (the grant-cap
 *    note, the steer notes, the empty chat list, the edit note, setError copy
 *    and the rest) lost their dash asides.
 * 4. Retry beside the calm refusal line is a calm ghost: no border, a fill
 *    on hover, a focus ring for the keyboard and a faint fill on touch.
 *
 * The guard at the bottom reads every STRING LITERAL and JSX TEXT node of the
 * four files through the TypeScript parser (comments never count). Since
 * v1.330.0 the reader is ONE shared helper (__tests__/helpers/dashGuard.ts)
 * that mission-copy-v1329 imports too. Three strings in the page are the
 * MODEL's words, not the user's (an agent's reply label that the daemon spells
 * the same way, and two task texts handed to an agent run); they are listed by
 * name below, and the list is checked so it cannot rot.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { stepLabel } from "@/components/chat/stepLabel";
import { PreflightNote } from "@/components/chat/PreflightNote";
import { CALM_GHOST_BTN, RetryTurnButton } from "@/components/chat/RetryTurnButton";
import type { IJEvent } from "@/lib/types";
// v1.330.0: the walker is shared with mission-copy-v1329 (one reader).
import { asides, copyPieces, readSrc } from "./helpers/dashGuard";

afterEach(() => cleanup());

function ev(type: string, payload: Record<string, unknown>): IJEvent {
  return { id: "e1", type, ts: "2026-10-10T00:00:00Z", session_id: "s1", payload } as unknown as IJEvent;
}

/* ------------------------------------------- 1. the step line's truth --- */

describe("the step line for provider.downgraded reads `used`", () => {
  it("used:none (a refusal) says nothing answered, names the endpoint by its label, and never says mock", () => {
    const line = stepLabel(
      ev("provider.downgraded", {
        requested: "fleet-abc123",
        used: "none",
        reason: "not connected",
        label: "Lab vLLM box",
      }),
    );
    expect(line).toBe("Nothing answered. Lab vLLM box: not connected.");
    expect(line).not.toMatch(/mock/i);
    expect(line).not.toContain("fleet-abc123");
  });

  it("used:none with no label falls back to the requested id, and keeps the router's own reason", () => {
    const line = stepLabel(
      ev("provider.downgraded", {
        requested: "fleet-dead",
        used: "none",
        reason: "in cooldown, retry in 30 s, after failing repeatedly, so this turn was not sent to it",
      }),
    );
    expect(line).toBe(
      "Nothing answered. fleet-dead: in cooldown, retry in 30 s, after failing repeatedly, so this turn was not sent to it.",
    );
  });

  it("used:none with no reason, and with nothing at all, still never claims a mock", () => {
    expect(stepLabel(ev("provider.downgraded", { used: "none", label: "Lab box" }))).toBe(
      "Nothing answered. Lab box did not answer.",
    );
    expect(stepLabel(ev("provider.downgraded", { used: "none" }))).toBe("Nothing answered.");
  });

  it("used:mock (the mock really answered) says so", () => {
    const line = stepLabel(
      ev("provider.downgraded", {
        requested: "mock (default)",
        used: "mock",
        reason: "your default provider is 'mock' but a real provider is connected.",
      }),
    );
    expect(line).toBe("The offline mock answered, not a real model.");
  });

  it("an older daemon (no `used`) is told neither: only that the chosen model was not used", () => {
    const named = stepLabel(ev("provider.downgraded", { requested: "fleet-custom", reason: "not connected" }));
    expect(named).toBe("fleet-custom was not used.");
    const bare = stepLabel(ev("provider.downgraded", {}));
    expect(bare).toBe("The chosen model was not used.");
    for (const l of [named, bare]) {
      expect(l).not.toMatch(/mock/i);
      expect(l).not.toMatch(/nothing answered/i);
    }
  });

  it("another provider's name in `used` says that one answered", () => {
    expect(stepLabel(ev("provider.downgraded", { requested: "fleet-a", used: "openai", label: "Box A" }))).toBe(
      "openai answered instead of Box A.",
    );
  });

  it("a hostile label stays one short line", () => {
    const line = stepLabel(
      ev("provider.downgraded", { used: "none", label: "A\n\nvery " + "long ".repeat(40), reason: "not connected" }),
    )!;
    expect(line).not.toMatch(/\n/);
    expect(line.length).toBeLessThan(80);
  });

  it("provider.failed has no dash aside", () => {
    expect(stepLabel(ev("provider.failed", { provider: "openai", error: "boom" }))).toBe("Provider openai failed: boom");
    expect(stepLabel(ev("provider.failed", { provider: "openai" }))).toBe("Provider openai failed");
    expect(stepLabel(ev("provider.failed", {}))).toBe("A model failed");
  });
});

/* -------------------------------------------- 2. the preflight sentences --- */

describe("the PreflightNote speaks in plain sentences", () => {
  it("cooldown: the daemon's words first, then plain sentences", () => {
    render(<PreflightNote provider="fleet-custom" available={true} cooldownS={23} />);
    expect(screen.getByTestId("ij-preflight-note").textContent).toBe(
      "fleet-custom is in cooldown, retry in 23 s. It failed repeatedly, so a turn sent to it now is refused. Pick another model or wait.",
    );
  });

  it("no kind carries a dash aside", () => {
    const cases = [
      <PreflightNote key="a" provider="fleet-custom" available={false} />,
      <PreflightNote key="b" provider="fleet-custom" available={false} stale />,
      <PreflightNote key="c" provider="claude-cli" available={false} signedOut />,
      <PreflightNote key="d" provider="fleet-custom" available={true} cooldownS={5} />,
    ];
    for (const c of cases) {
      render(c);
      const text = screen.getByTestId("ij-preflight-note").textContent ?? "";
      expect(text.length).toBeGreaterThan(20);
      expect(text).not.toMatch(/\s[—–]\s/);
      cleanup();
    }
  });

  it("signed out keeps its remedy", () => {
    render(<PreflightNote provider="claude-cli" available={false} signedOut />);
    expect(screen.getByTestId("ij-preflight-note").textContent).toBe(
      "claude-cli is installed but not signed in. This turn will fail. Run `claude` in a terminal, then /login, then Test on Connections.",
    );
  });
});

/* ----------------------------------------------- 4. Retry is a calm ghost --- */

const tokens = (el: Element) => (el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

function expectCalmGhost(el: HTMLElement) {
  const t = tokens(el);
  expect(t).not.toContain("btn-ghost");
  // No border of any kind (border, border-white/10, border-x …).
  expect(t.filter((c) => /^border(-|$)/.test(c))).toEqual([]);
  // A fill on hover, the keyboard ring, and a faint fill where there is no hover.
  expect(t).toContain("hover:bg-white/[0.06]");
  expect(t).toContain("focus-visible:ring-1");
  expect(t).toContain("focus-visible:ring-accent/50");
  expect(t).toContain("[@media(hover:none)]:bg-white/[0.04]");
  // Theme tokens only: no literal hue, whole pixels.
  expect(el.className).not.toMatch(/(?:text|bg|border|ring)-(?:red|rose|amber|cyan|sky|blue|green|emerald)-\d/);
  expect(el.className).not.toMatch(/text-\[\d+\.5px\]/);
}

describe("Retry beside the refusal line is a calm ghost, still clearly a button", () => {
  it("Retry, enabled: a real button with its words and icon, no border", () => {
    const onRetry = vi.fn();
    render(<RetryTurnButton cooldownS={0} onRetry={onRetry} provider="fleet-custom" down={false} onChooseModel={() => {}} />);
    const btn = screen.getByRole("button", { name: /^Retry$/ });
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.getAttribute("type")).toBe("button");
    expect(btn.querySelector("svg")).not.toBeNull();
    expectCalmGhost(btn);
    btn.click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("the cooldown countdown and 'Choose another model…' are calm ghosts too", () => {
    render(<RetryTurnButton cooldownS={9} onRetry={() => {}} provider="claude-cli" down={false} onChooseModel={() => {}} />);
    const wait = screen.getByRole("button", { name: /^Retry in \d+s$/ });
    expect(wait).toBeDisabled();
    expectCalmGhost(wait);
    const choose = screen.getByRole("button", { name: /^Choose another model…$/ });
    expectCalmGhost(choose);
    // Plain words in the tooltips too.
    expect(choose.getAttribute("title")).toBe(
      "claude-cli is cooling down. Pick a model for this chat, then press Retry.",
    );
    expect(wait.getAttribute("title")).not.toMatch(/\s[—–]\s/);
  });

  it("the exported class is the one every button uses", () => {
    const src = readSrc("components/chat/RetryTurnButton.tsx");
    expect(src.match(/className=\{CALM_GHOST_BTN\}/g)?.length).toBe(3);
    expect(CALM_GHOST_BTN).not.toMatch(/\bborder\b/);
  });
});

/* --------------------------------------------------- 3. the source guard --- */

/** Strings in the page that are the MODEL's words, never shown as a notice.
 *  Each is matched by a fragment and must still be found (see below). */
const MODEL_FACING: Record<string, string> = {
  // toRequestMessages labels an agent's reply for the model; the daemon's
  // agent_line_label spells the same sentence (chat-sticky-agent-v1284 pins it).
  agentReplyLabel: ", on the agent panel — not Iron Jarvis]",
  // The task text a skill-directed agent run is handed.
  skillTask: "skill for this — load it with skill_load first.",
  // The task text "Have <agent> do this" hands the agent run.
  doItTask: "above) — now DO it with your tools, and report the files you made.",
};

const FILES = [
  "components/chat/stepLabel.ts",
  "components/chat/PreflightNote.tsx",
  "components/chat/RetryTurnButton.tsx",
  "app/chat/page.tsx",
];

describe("the chat page and its notice components keep to plain sentences", () => {
  it("no spaced em or en dash in any user-visible string or JSX text", () => {
    const allow = Object.values(MODEL_FACING);
    expect(FILES.flatMap((rel) => asides(rel, undefined, rel === "app/chat/page.tsx" ? allow : []))).toEqual([]);
  });

  it("each model-facing exception is still in the page, once (the list cannot rot)", () => {
    const pieces = copyPieces("app/chat/page.tsx").map((p) => p.text);
    for (const [name, frag] of Object.entries(MODEL_FACING)) {
      expect(pieces.filter((t) => t.includes(frag)).length, name).toBe(1);
    }
  });

  it("the guard reads the plain sentences this wave wrote (anti-vacuity)", () => {
    const page = copyPieces("app/chat/page.tsx").map((p) => p.text).join("\n");
    for (const s of [
      "No saved chats yet. Conversations appear here after the first reply.",
      "Jarvis finished before reading this. Press Enter to send it.",
      "That turn has already finished. Send it as a new message.",
      "Nothing is running to steer. Send it as a message.",
      "Attachments can't be sent to a messaging thread yet. Remove them, or start a new chat.",
      "Plain chat, no project",
    ])
      expect(page, s).toContain(s);
    // JSX text spread over lines is read too.
    expect(page).toContain("Allowed for this conversation.");
    expect(page).toContain("Editing a sent message. The");
    const step = copyPieces("components/chat/stepLabel.ts").map((p) => p.text).join("\n");
    expect(step).toContain("The offline mock answered, not a real model.");
  });

  it("the guard catches a dash aside in a string, a template and JSX, and skips comments", () => {
    const probe = [
      "// a comment — fine",
      'const a = "one — two";',
      "const b = `x ${1} — y`;",
      "const c = <p>{/* jsx comment — fine */}left — right</p>;",
      'const d = <p>run <b>x</b>{" "}',
      "  —{\" \"}",
      '  {"the run is waiting."}</p>;',
      'const e = <span>{a ?? "—"}</span>;',
      "const f = <td>—</td>;",
    ].join("\n");
    expect(asides("probe.tsx", probe).map((s) => s.split(":")[1]).sort((x, y) => Number(x) - Number(y))).toEqual([
      "2", "3", "4", "6",
    ]);
    // An allowed fragment is skipped only where it appears.
    expect(asides("probe.tsx", 'const a = "one — two";\nconst b = "three — four";', ["one — two"]).length).toBe(1);
  });
});
