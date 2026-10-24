/**
 * v1.381.0 — shell steps from a project's or a plugin's slash command ask
 * every time (the user's decision, 2026-10-23). The screen's half:
 *
 *  - the daemon's approval frame carries once_reason "command"; the stream
 *    hook keeps it (lib/onceReason, the ONE whitelist) and the card says the
 *    rule in words — never the local-only line it used to fall back to;
 *  - a HELPER's ask on the same rule arrives on its row with once_only and
 *    once_reason: the card offers Allow once and Deny only (the daemon reads
 *    a wider answer as once, so offering one was a promise it did not keep).
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { LiveHelperRow } from "@/components/harness/HarnessHelpers";
import { applyHelperFrame } from "@/lib/buildHelpers";
import { onceReasonOf } from "@/lib/onceReason";
import { useChatStream } from "@/lib/useChatStream";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// v1.381.0 review: the daemon's words now (command_shell.turn_words).
const WORDS =
  "This turn runs the project's /deploy command, which has shell lines, so every command in it asks you first.";

describe("a shell step a project's or a plugin's slash command asked for", () => {
  it("draws its own rule on the card, Allow once and Deny only", () => {
    render(
      <ApprovalCard
        approval={{
          id: "a1", callId: "c1", tool: "shell", args: { command: "echo hello" },
          onceOnly: true, onceReason: "command", costNote: WORDS,
        }}
        docked
      />,
    );
    expect(screen.getByTestId("approval-command-once")).toHaveTextContent(
      "Shell steps from a project's or a plugin's slash command ask you every time, whatever this " +
        "session's approval setting. Your own commands follow the setting.",
    );
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
    expect(screen.queryByTestId("approval-git-once")).toBeNull();
    expect(screen.getByTestId("approval-cost")).toHaveTextContent(WORDS);
    expect(screen.getByRole("button", { name: "Allow once" })).toBeInTheDocument();
    expect(screen.queryByTestId("approval-scope")).toBeNull();
  });

  it("is one of the reasons the card knows, and nothing else is", () => {
    expect(onceReasonOf("command")).toBe("command");
    expect(onceReasonOf("git")).toBe("git");
    expect(onceReasonOf("page")).toBe("page");
    expect(onceReasonOf("commands")).toBeUndefined();
    expect(onceReasonOf(1)).toBeUndefined();
  });

  it("keeps once_reason command from the stream", async () => {
    let ctl!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })));
    let hook!: ReturnType<typeof useChatStream>;
    function Probe() {
      hook = useChatStream();
      return null;
    }
    render(<Probe />);
    let done!: Promise<unknown>;
    act(() => {
      done = hook.run({ messages: [{ role: "user", content: "/deploy" }] } as never, () => {}, () => {});
    });
    const frame = {
      id: "ap1", call_id: "c1", tool: "shell", args: { command: "echo hello" },
      once_only: true, once_reason: "command", cost_note: WORDS,
    };
    await act(async () => {
      ctl.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(frame)}\n\n`));
    });
    await waitFor(() => expect(hook.approval?.onceReason).toBe("command"), { timeout: 5000 });
    expect(hook.approval?.onceOnly).toBe(true);
    await act(async () => {
      ctl.close();
      await done.catch(() => {});
    });
  });

  it("on a helper's row, the ask offers Allow once and Deny only and says why", () => {
    const [helper] = applyHelperFrame([], {
      id: "h1", agent: "builder", task: "Run the status.", state: "waiting", steps: 0, since: 1,
      session: "session_k1",
      ask: {
        id: "apr_1", tool: "shell", args: { command: "echo IJ-HELPER" },
        cost_note: WORDS, once_only: true, once_reason: "command",
      },
    });
    expect(helper.ask?.onceOnly).toBe(true);
    expect(helper.ask?.onceReason).toBe("command");
    render(<LiveHelperRow helper={helper} />);
    const ask = screen.getByTestId("helper-ask");
    expect(within(ask).getByTestId("approval-command-once")).toBeInTheDocument();
    expect(within(ask).getByTestId("approval-cost")).toHaveTextContent(WORDS);
    expect(within(ask).getByRole("button", { name: "Allow once" })).toBeInTheDocument();
    expect(within(ask).queryByText(/for this run/i)).toBeNull();
    expect(within(ask).queryByTestId("approval-local-only")).toBeNull();
  });

  it("an ordinary helper ask keeps its wider answer", () => {
    const [helper] = applyHelperFrame([], {
      id: "h2", agent: "builder", task: "Run echo.", state: "waiting", steps: 0, since: 1,
      session: "session_k2",
      ask: { id: "apr_2", tool: "shell", args: { command: "echo hi" }, once_reason: "command" },
    });
    expect(helper.ask?.onceOnly).toBeUndefined();
    expect(helper.ask?.onceReason).toBeUndefined();
    render(<LiveHelperRow helper={helper} />);
    const ask = screen.getByTestId("helper-ask");
    expect(within(ask).queryByTestId("approval-command-once")).toBeNull();
    expect(within(ask).getByText(/for this run/i)).toBeInTheDocument();
  });
});
