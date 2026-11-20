/**
 * 22-a (R1.9): Iron Jarvis checks every Build › Code reply (hosted/proof.py).
 * The daemon writes ONE `proof` record per reply it checked into the
 * session's log ("checking", then the outcome); the conversation draws it
 * under that reply from the daemon's own line, a newer record of the same
 * reply replacing the older one IN PLACE. The first time in a folder the
 * record is a card that asks once with the exact command, and its Yes sends
 * exactly that command back. The session reads again while it is checking.
 */
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: vi.fn(async () => ({})),
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: null, refresh: () => {}, epoch: 0 }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/terminals",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { CodeConversation } from "@/components/buildCode/CodeConversation";
import type { ProofActions } from "@/components/buildCode/ProofLine";
import {
  CODE_BACKGROUND_REREAD_MS,
  backgroundRereadMs,
  decodeCodeEvent,
  decodeCodeSession,
  foldConversation,
  withNewerNotices,
  type CodeEvent,
} from "@/lib/buildApp";
import { CHECK_NO, CHECK_REWIND, CHECK_UNFINISHED, CHECK_YES, checkAnswerPath, proofShape } from "@/lib/buildAppProof";

afterEach(cleanup);

function events(raw: Record<string, unknown>[]): CodeEvent[] {
  return raw.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => !!e);
}

const CMD = "npm test";
const PASSED = "Checked by Iron Jarvis: 14 tests ran, 14 passed. 2 new files: `api/limits.py`, `tests/test_limits.py`.";

function proof(over: Record<string, unknown>): Record<string, unknown> {
  return {
    type: "proof",
    at: "2026-11-20T10:00:05Z",
    for_turn: "t1",
    state: "done",
    tests: "passed",
    why: "ran",
    command: CMD,
    ran: 14,
    passed: 14,
    failed: 0,
    new_files: ["api/limits.py", "tests/test_limits.py"],
    new_source: "git",
    by: "iron_jarvis",
    line: PASSED,
    ...over,
  };
}

const TURN = [
  { type: "user", at: "2026-11-20T10:00:00Z", text: "add a rate limit", files: [], turn: "t1" },
  { type: "text_done", at: "2026-11-20T10:00:03Z", item: "a1", text: "Added the limit.", turn: "t1" },
  { type: "done", at: "2026-11-20T10:00:04Z", stop: "completed", turn: "t1" },
];

function actions(over: Partial<ProofActions> = {}): ProofActions {
  return { onAnswer: vi.fn(async () => null), onStop: vi.fn(), ...over };
}

describe("the check's record", () => {
  it("decodes by whitelist and folds one row per reply, the newest in place", () => {
    const evs = events([
      ...TURN,
      proof({ state: "checking", tests: "not_run", line: "Iron Jarvis is checking this reply: running `npm test`." }),
      proof({}),
    ]);
    const rows = foldConversation(evs).items;
    const checks = rows.filter((r) => r.kind === "proof");
    expect(checks).toHaveLength(1);
    expect(rows[rows.length - 1].kind).toBe("proof");
    const only = checks[0];
    expect(only.kind === "proof" && only.proof.tests).toBe("passed");
    expect(only.kind === "proof" && only.proof.newFiles).toEqual(["api/limits.py", "tests/test_limits.py"]);
    // A record that names no reply, or an outcome Iron Jarvis does not write, is not drawn.
    expect(decodeCodeEvent(proof({ for_turn: "" }))).toBeNull();
    expect(decodeCodeEvent(proof({ tests: "verified" }))).toBeNull();
  });

  it("draws the daemon's line under the reply, with its command and files as code", () => {
    render(<CodeConversation items={foldConversation(events([...TURN, proof({})])).items} folder="D:\\shop" />);
    const line = screen.getByTestId("code-proof-line");
    expect(line.textContent).toBe(PASSED.replace(/`/g, ""));
    expect(line.querySelectorAll("code")).toHaveLength(2);
    expect(screen.getByTestId("code-proof").getAttribute("data-tests")).toBe("passed");
  });

  it("while checking: says so and its Stop reaches the page", () => {
    const a = actions();
    const evs = events([
      ...TURN,
      proof({ state: "checking", tests: "not_run", line: "Iron Jarvis is checking this reply: running `npm test`." }),
    ]);
    render(<CodeConversation items={foldConversation(evs).items} folder="D:\\shop" proofActions={a} />);
    expect(screen.getByTestId("code-proof").getAttribute("data-state")).toBe("checking");
    fireEvent.click(screen.getByTestId("code-proof-stop"));
    expect(a.onStop).toHaveBeenCalledTimes(1);
  });

  it("a checking record the session no longer stands behind is never drawn as running", () => {
    const evs = events([
      ...TURN,
      proof({ state: "checking", tests: "not_run", line: "Iron Jarvis is checking this reply: running `npm test`." }),
    ]);
    render(<CodeConversation items={foldConversation(evs).items} folder="D:\\shop" proofActions={actions()} checkingNow={false} />);
    expect(screen.getByTestId("code-proof").getAttribute("data-state")).toBe("unfinished");
    expect(screen.getByTestId("code-proof").textContent).toBe(CHECK_UNFINISHED);
    expect(screen.queryByTestId("code-proof-stop")).toBeNull();
  });
});

describe("the first-time card", () => {
  const ASK = proof({
    tests: "ask",
    why: "not_confirmed",
    ran: null,
    passed: null,
    failed: null,
    line: "Run `npm test` after each reply in this folder?",
    detail: "Iron Jarvis runs it itself after Codex changes files and writes what happened under the reply. If the tests fail, Codex is sent back to fix them, up to twice per message, on your plan.",
  });

  it("asks once with the exact command and Yes sends exactly that command for that reply", async () => {
    const a = actions();
    render(<CodeConversation items={foldConversation(events([...TURN, ASK])).items} folder="D:\\shop" proofActions={a} />);
    expect(screen.getByTestId("code-proof-ask").textContent).toBe("Run npm test after each reply in this folder?");
    expect(screen.getByText(/up to twice per message, on your plan/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: CHECK_YES }));
    await waitFor(() => expect(a.onAnswer).toHaveBeenCalledWith(CMD, true, "t1"));
  });

  it("No is a quiet answer, and a refusal is said on the card", async () => {
    const a = actions({ onAnswer: vi.fn(async () => "This folder now proposes another check command.") });
    render(<CodeConversation items={foldConversation(events([...TURN, ASK])).items} folder="D:\\shop" proofActions={a} />);
    fireEvent.click(screen.getByRole("button", { name: CHECK_NO }));
    await waitFor(() => expect(screen.getByTestId("code-proof-refused").textContent).toContain("another check command"));
    expect(a.onAnswer).toHaveBeenCalledWith(CMD, false, "t1");
  });

  it("only the newest card is drawn; an older one goes once a newer check exists", () => {
    const later = [
      { type: "user", at: "2026-11-20T10:01:00Z", text: "and log it", files: [], turn: "t2" },
      { type: "done", at: "2026-11-20T10:01:04Z", stop: "completed", turn: "t2" },
      proof({ for_turn: "t2" }),
    ];
    render(<CodeConversation items={foldConversation(events([...TURN, ASK, ...later])).items} folder="D:\\shop" />);
    expect(screen.queryByTestId("code-proof-card")).toBeNull();
    expect(screen.getAllByTestId("code-proof")).toHaveLength(1);
  });

  it("an answered No is said once; a folder with no test command draws nothing", () => {
    const off = decodeCodeEvent(proof({ tests: "off", why: "you", answered: true, line: "Not checked: you turned off tests after replies in this folder." }));
    const none = decodeCodeEvent(proof({ tests: "none", why: "no_command", line: "Not checked: Iron Jarvis found no test command for this folder." }));
    const quiet = decodeCodeEvent(proof({ tests: "off", why: "you", line: "Not checked: you turned off tests after replies in this folder." }));
    expect(off && off.type === "proof" && proofShape(off.proof, true)).toBe("line");
    expect(none && none.type === "proof" && proofShape(none.proof, true)).toBe("none");
    expect(quiet && quiet.type === "proof" && proofShape(quiet.proof, true)).toBe("none");
  });

  it("the answer goes to the session's own check route", () => {
    expect(checkAnswerPath("app_1/x")).toBe("/build/app/sessions/app_1%2Fx/checks");
  });
});

describe("tests that still fail", () => {
  it("say it is not finished and offer Rewind to before the message (its preview)", () => {
    const onRewindAsk = vi.fn();
    const a = actions({ onRewindAsk, rewindAt: new Set(["2026-11-20T10:00:00Z"]) });
    const failed = proof({
      tests: "failed",
      ran: 1,
      passed: 0,
      failed: 1,
      ended: "stuck",
      origin_at: "2026-11-20T10:00:00Z",
      line: "Checked by Iron Jarvis: 1 test ran, 0 passed. This is not finished. It was sent back once and stopped there: the last try changed nothing. Tell it to keep going and it will carry on.",
    });
    render(<CodeConversation items={foldConversation(events([...TURN, failed])).items} folder="D:\\shop" proofActions={a} />);
    expect(screen.getByTestId("code-proof").getAttribute("data-tests")).toBe("failed");
    fireEvent.click(screen.getByRole("button", { name: CHECK_REWIND }));
    expect(onRewindAsk).toHaveBeenCalledWith("2026-11-20T10:00:00Z");
  });

  it("no Rewind when the message's checkpoint is not kept", () => {
    const a = actions({ onRewindAsk: vi.fn(), rewindAt: new Set() });
    const failed = proof({ tests: "failed", ended: "limit", origin_at: "2026-11-20T10:00:00Z", line: "Checked by Iron Jarvis: 1 test ran, 0 passed." });
    render(<CodeConversation items={foldConversation(events([...TURN, failed])).items} folder="D:\\shop" proofActions={a} />);
    expect(screen.queryByTestId("code-proof-rewind")).toBeNull();
  });
});

describe("the session while it is checking", () => {
  it("is read again while checking, and only then", () => {
    const s = decodeCodeSession({ id: "app_1", harness: "codex", checking: true });
    expect(s?.checking).toBe(true);
    expect(backgroundRereadMs(s, false)).toBe(CODE_BACKGROUND_REREAD_MS);
    expect(backgroundRereadMs(s, true)).toBe(0);
    const idle = decodeCodeSession({ id: "app_1", harness: "codex" });
    expect(idle?.checking).toBeUndefined();
    expect(backgroundRereadMs(idle, false)).toBe(0);
  });

  it("a record written after the reply survives a re-read that keeps what is drawn", () => {
    const drawn = events([...TURN, { type: "text", at: "2026-11-20T10:00:02Z", item: "a1", delta: "Added", turn: "t1" }]);
    const saved = events([...TURN, proof({})]);
    const kept = withNewerNotices(drawn, saved);
    expect(kept.some((e) => e.type === "proof")).toBe(true);
    expect(withNewerNotices(kept, saved).filter((e) => e.type === "proof")).toHaveLength(1);
  });
});
