import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * v1.531.0 — the owner: "in Chat › Code I can't minimize the other tools".
 * The "Other tools" heading folds the whole part to one line, says how many
 * sessions are working (or are new) while folded, and the choice is kept per
 * place (Chat › Code and the chat list each keep their own) across a reload.
 */

let answer: unknown = {};

vi.mock("@/lib/api", () => ({
  get: async () => answer,
  post: async () => ({}),
}));

import OtherToolChats from "@/components/chat/OtherToolChats";
import { FIRST_READ_MS, FOLDED_KEY } from "@/lib/otherTools";

const AT = "2026-11-17T10:00:00+00:00";

function row(id: string, title: string, over: Record<string, unknown> = {}) {
  return { id, title, folder: "C:\\Work\\Parser", started: AT, ended: AT, working: false, quiet_s: 7200, doing: "", ...over };
}

const ALL = {
  tools: [
    {
      tool: "claude-code",
      label: "Claude Code",
      openable: true,
      folders: [
        {
          folder: "C:\\Work\\Parser",
          name: "Parser",
          chats: [row("live", "Speed up the import", { working: true, quiet_s: 12, doing: "Ran: pytest -q" })],
        },
      ],
    },
    { tool: "codex", label: "Codex", openable: true, folders: [{ folder: "C:\\Work\\Parser", name: "Parser", chats: [row("x1", "Tidy the tests")] }] },
  ],
  apps: [],
};

async function shown(mode: "chat" | "code") {
  render(<OtherToolChats mode={mode} go={() => {}} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(FIRST_READ_MS + 10);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  answer = ALL;
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the Other tools part folds (v1.531.0)", () => {
  it("folds to its heading in Chat › Code, says what works, and opens again", async () => {
    await shown("code");
    const fold = await screen.findByTestId("other-tools-fold");
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("other-tool-claude-code")).toBeTruthy();

    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("other-tool-claude-code")).toBeNull();
    expect(screen.queryByTestId("other-tool-codex")).toBeNull();
    expect(screen.getByTestId("other-tools-folded-note").textContent).toBe("1 working");

    fireEvent.click(fold);
    expect(screen.getByTestId("other-tool-claude-code")).toBeTruthy();
    expect(screen.queryByTestId("other-tools-folded-note")).toBeNull();
  });

  it("stays folded after a reload, in its own place only", async () => {
    await shown("code");
    fireEvent.click(await screen.findByTestId("other-tools-fold"));
    expect(JSON.parse(window.localStorage.getItem(FOLDED_KEY) || "{}")).toEqual({ code: true });
    cleanup();

    await shown("code");
    const again = await screen.findByTestId("other-tools-fold");
    expect(again.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("other-tool-claude-code")).toBeNull();
    cleanup();

    // The chat list keeps its own choice (open).
    answer = { tools: [{ ...ALL.tools[0], tool: "claude-chat", label: "Claude Chat", openable: false }], apps: [] };
    await shown("chat");
    expect((await screen.findByTestId("other-tools-fold")).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("other-tool-claude-chat")).toBeTruthy();
  });
});
