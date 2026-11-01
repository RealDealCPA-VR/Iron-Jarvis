/**
 * v1.422.0 (Wave 2 "Never lose work", B1) — a coding reply that FAILED keeps
 * its progress, and a round waits out a passing provider error.
 *
 *  - The failed reply's line offers "Continue from where it stopped" FIRST
 *    (only when the daemon kept its rounds: `canContinue`), Retry second —
 *    never on a refusal, beside a limit card, while a reply runs, on an
 *    archived session or on an older reply.
 *  - A `reset` with `drop` takes back only that many characters (the retried
 *    round's own words): the earlier rounds' words stay. A plain reset still
 *    clears everything (the chat's language rewrite).
 *  - The `provider_wait` frame decodes to the live line's words; "trying"
 *    ends it.
 *
 * `useChatStream` is NOT mocked here — the reset pins drive the real hook
 * over a fake fetch body, one frame per act() (the S-02 lesson).
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "" };
});

import { decodeSSE, useChatStream, useLiveText, type UseChatStream } from "@/lib/useChatStream";
import { CONTINUE_WORDS, RETRY_FROM_START_TITLE, continueOffered, retryOffered } from "@/lib/buildRetry";
import { decodeMessage, type BuildMessage } from "@/lib/buildSessions";
import { CONTINUED_LINE, continuedFrom, continuedTurns, cutByStop } from "@/lib/buildResume";
import { PROVIDER_WAIT_FRAME, decodeProviderWait } from "@/lib/buildPatience";
import { HarnessFailureLine } from "@/components/harness/HarnessFailure";

const LIVE = { newest: true, busy: false, archived: false };

function failed(extra: Partial<BuildMessage> = {}): BuildMessage {
  return {
    role: "assistant",
    content: "",
    turnId: "bt-f",
    error: "Claude Code is busy. Iron Jarvis tried 5 times over 1 min 50 s. Try again shortly.",
    canContinue: true,
    ...extra,
  } as BuildMessage;
}

// ------------------------------------------------------------- the presses

describe("Continue from where it stopped is offered only where it can work", () => {
  it("on the newest failed reply whose rounds were kept", () => {
    expect(continueOffered(failed(), LIVE)).toBe(true);
  });

  it("never without kept rounds, on a refusal, beside a limit card, or an attempts card", () => {
    expect(continueOffered(failed({ canContinue: undefined }), LIVE)).toBe(false);
    expect(continueOffered(failed({ errorKind: "refusal" }), LIVE)).toBe(false);
    expect(continueOffered(failed({ accountLimit: { account: "a" } }), LIVE)).toBe(false);
    expect(continueOffered(failed({ attempts: { rows: [] } }), LIVE)).toBe(false);
  });

  it("never on an older reply, while a reply runs, or on an archived session", () => {
    expect(continueOffered(failed(), { ...LIVE, newest: false })).toBe(false);
    expect(continueOffered(failed(), { ...LIVE, busy: true })).toBe(false);
    expect(continueOffered(failed(), { ...LIVE, archived: true })).toBe(false);
  });

  it("never on a reply that did not fail (a cut-off one keeps the restart's way)", () => {
    expect(continueOffered(failed({ error: undefined, interrupted: true }), LIVE)).toBe(false);
    // ...while Retry stays for it.
    expect(retryOffered(failed({ error: undefined, interrupted: true }), LIVE)).toBe(true);
  });

  it("the line's keys survive the decoder (the daemon's LINE_KEYS)", () => {
    const m = decodeMessage({ role: "assistant", content: "", canContinue: true, continuedFrom: "bt-f" });
    expect(m?.canContinue).toBe(true);
    expect(m?.continuedFrom).toBe("bt-f");
  });
});

describe("the failed reply's line: Continue first, Retry second", () => {
  function line(m: BuildMessage, onContinue = vi.fn(), onRetry = vi.fn(), continuedBelow = false) {
    render(
      <HarnessFailureLine
        m={m}
        newest
        busy={false}
        health={null}
        turnProvider="claude-cli"
        retry={retryOffered(m, LIVE)}
        onRetry={onRetry}
        canRewind={false}
        onRewind={() => {}}
        canContinue={continueOffered(m, LIVE)}
        onContinue={onContinue}
        continuedBelow={continuedBelow}
      />,
    );
    return { onContinue, onRetry };
  }

  it("shows both presses, Continue before Retry, and each does its own thing", () => {
    const { onContinue, onRetry } = line(failed());
    const box = screen.getByTestId("harness-reply-failure");
    const presses = within(box).getAllByRole("button").map((b) => b.getAttribute("data-testid"));
    expect(presses.indexOf("harness-continue")).toBeGreaterThanOrEqual(0);
    expect(presses.indexOf("harness-continue")).toBeLessThan(presses.indexOf("harness-retry"));
    expect(screen.getByTestId("harness-continue").textContent).toContain(CONTINUE_WORDS);
    expect(screen.getByTestId("harness-retry").getAttribute("title")).toBe(RETRY_FROM_START_TITLE);
    fireEvent.click(screen.getByTestId("harness-continue"));
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("with no kept rounds there is only Retry (the old line)", () => {
    line(failed({ canContinue: undefined }));
    expect(screen.queryByTestId("harness-continue")).toBeNull();
    expect(screen.getByTestId("harness-retry")).toBeTruthy();
  });

  it("a failed reply the user continued says so", () => {
    line(failed(), vi.fn(), vi.fn(), true);
    expect(screen.getByTestId("harness-continued-below")).toBeTruthy();
  });

  it("the continue's own line is never read as a restart's carry-on", () => {
    const msgs = [failed(), { role: "assistant", content: "done", continuedFrom: "bt-f" } as BuildMessage];
    expect(continuedTurns(msgs).has("bt-f")).toBe(true);
    expect(continuedFrom(msgs[1])).toBe("bt-f");
    expect(CONTINUED_LINE).toMatch(/Continued from where/);
    // A failed reply with words is "cut by stop" in the restart's reading,
    // but nothing names it in `resumedFrom`, so it is never "carried on by
    // itself".
    expect(cutByStop({ ...failed(), interrupted: true })).toBe(true);
  });
});

// --------------------------------------------------------- the wait line

describe("the provider_wait frame", () => {
  it("decodes the daemon's words, and 'trying' ends the line", () => {
    expect(PROVIDER_WAIT_FRAME).toBe("provider_wait");
    expect(
      decodeProviderWait({
        state: "waiting",
        words: "Claude Code is busy (overloaded). Trying again in 30 s, attempt 2 of 5.",
        attempt: 2,
        of: 5,
        wait_s: 30,
      }),
    ).toEqual({ words: "Claude Code is busy (overloaded). Trying again in 30 s, attempt 2 of 5.", attempt: 2, of: 5 });
    expect(decodeProviderWait({ state: "trying", attempt: 2, of: 5 })).toBe("trying");
    expect(decodeProviderWait({ state: "waiting", words: "" })).toBeNull();
    expect(decodeProviderWait(null)).toBeNull();
  });
});

// ------------------------------------------------------ reset with `drop`

class Wire {
  private chunks: Uint8Array[] = [];
  private waiting: ((r: { value?: Uint8Array; done: boolean }) => void) | null = null;
  private ended = false;
  private enc = new TextEncoder();

  frame(event: string, data: unknown): void {
    const c = this.enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ value: c, done: false });
    } else this.chunks.push(c);
  }

  end(): void {
    this.ended = true;
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ done: true });
    }
  }

  response(): Response {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      body: {
        getReader: () => ({
          read: () =>
            new Promise<{ value?: Uint8Array; done: boolean }>((res) => {
              const c = this.chunks.shift();
              if (c) res({ value: c, done: false });
              else if (this.ended) res({ done: true });
              else this.waiting = res;
            }),
          cancel: async () => undefined,
        }),
      },
    } as unknown as Response;
  }
}

let wire: Wire;
let hook: UseChatStream | null = null;
const queued = new Map<number, () => void>();
let nextFrameId = 1;

async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}
async function send(event: string, data: unknown): Promise<void> {
  await act(async () => {
    wire.frame(event, data);
    await pump();
  });
}
async function frame(): Promise<void> {
  await act(async () => {
    const entries = [...queued.values()];
    queued.clear();
    for (const cb of entries) cb();
  });
}

function Owner() {
  const s = useChatStream({ textInState: false });
  hook = s;
  return <div data-testid="live">{useLiveText(s)}</div>;
}

beforeEach(() => {
  hook = null;
  queued.clear();
  nextFrameId = 1;
  wire = new Wire();
  vi.stubGlobal("fetch", vi.fn(async () => wire.response()));
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = nextFrameId++;
    queued.set(id, () => cb(0));
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    queued.delete(id);
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a reset with drop takes back only the retried round's words", () => {
  it("decodes drop (a positive whole number only)", () => {
    expect(decodeSSE("reset", JSON.stringify({ reason: "retry", drop: 7 }))).toEqual({ type: "reset", drop: 7 });
    expect(decodeSSE("reset", JSON.stringify({ reason: "retry", drop: -1 }))).toEqual({ type: "reset" });
    expect(decodeSSE("reset", JSON.stringify({ drop: 2.5 }))).toEqual({ type: "reset" });
  });

  it("keeps the earlier rounds' words, drops the failed try's, then takes the new ones", async () => {
    render(<Owner />);
    let p!: Promise<unknown>;
    act(() => {
      p = hook!.run({ message: "go" });
    });
    p.catch(() => {});
    await send("token", { text: "Reading the files. " });
    await send("token", { text: "Half an answ" });
    await frame();
    await send("reset", { reason: "retry", drop: "Half an answ".length });
    await frame();
    expect(hook!.textStore!.get()).toBe("Reading the files. ");
    await send("token", { text: "The whole answer." });
    await frame();
    expect(screen.getByTestId("live").textContent).toBe("Reading the files. The whole answer.");
    await act(async () => {
      wire.end();
      await pump();
    });
  });

  it("a reset with no drop still clears everything (the chat's rewrite)", async () => {
    render(<Owner />);
    let p!: Promise<unknown>;
    act(() => {
      p = hook!.run({ message: "go" });
    });
    p.catch(() => {});
    await send("token", { text: "Hola" });
    await send("reset", {});
    await frame();
    expect(hook!.textStore!.get()).toBe("");
    await act(async () => {
      wire.end();
      await pump();
    });
  });
});
