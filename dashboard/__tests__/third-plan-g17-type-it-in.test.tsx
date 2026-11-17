/**
 * Third north-star fix plan, group 17 (TM14, decision G21): "Type it in" is
 * offered only for a ONE-LINE command.
 *
 * The terminal's AI bar types the model's suggested command into the shell
 * and leaves Enter to the user. A suggestion of several lines was typed with
 * its line breaks, and a bash-family shell runs every line before the last
 * the moment its line break arrives: the user's "last look" never happened.
 *
 * These tests render the REAL TerminalPane (only its xterm host is a stand-in
 * that records what is typed) and drive the AI bar through its own form:
 *  - a suggestion of two lines offers no "Type it in"; it says why and offers
 *    Copy, which copies the whole block;
 *  - a one-line suggestion with a trailing line break is typed WITHOUT it;
 *  - a plain one-line suggestion is typed as it is.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    aiAnswer: { reply: "", command: "" } as Record<string, unknown>,
    typed: [] as string[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => Promise.reject(new hooks.FakeApiError(`unmocked GET ${path}`, 404)),
  post: (path: string) =>
    path.endsWith("/ai") ? Promise.resolve(hooks.aiAnswer) : Promise.resolve({}),
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/terminals",
}));

vi.mock("@/lib/layout", () => ({ waitForStableSize: () => Promise.resolve() }));

// The xterm host: the one stand-in. Its send() records what the pane types.
vi.mock("@/components/terminal/paneHost", () => {
  const term = {
    focus: () => {},
    clear: () => {},
    refresh: () => {},
    scrollPages: () => {},
    rows: 24,
    cols: 80,
  };
  const host = {
    term,
    daemonOnline: true,
    flowEnabled: false,
    keyHandler: null as unknown,
    adopt: (
      _holder: unknown,
      _owner: unknown,
      cbs: { onConn: (s: string, lost: boolean) => void },
    ) => cbs.onConn("open", false),
    settle: () => {},
    start: () => {},
    park: () => {},
    release: () => {},
    reconnectNow: () => {},
    fit: () => {},
    send: (text: string) => {
      hooks.typed.push(text);
      return true;
    },
  };
  return {
    acquirePaneHost: () => Promise.resolve(host),
    flowWanted: () => false,
  };
});

import { TerminalPane } from "@/components/terminal/TerminalPane";
import type { TerminalInfo } from "@/lib/types";

const INFO = { id: "t1", shell: "bash", cwd: "/home/me/project", alive: true } as unknown as TerminalInfo;

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  hooks.typed.length = 0;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = NoopResizeObserver;
});

afterEach(() => {
  cleanup();
});

async function askFor(command: string) {
  hooks.aiAnswer = { reply: "Here you go.", command, provider: "p", model: "m" };
  render(<TerminalPane info={INFO} focused onFocus={() => {}} onClose={() => {}} />);
  fireEvent.click(screen.getByTitle("Ask AI about this terminal"));
  fireEvent.change(screen.getByLabelText("Ask AI about this terminal"), {
    target: { value: "how do I build it?" },
  });
  fireEvent.submit(screen.getByLabelText("Ask AI about this terminal").closest("form")!);
  await waitFor(() => expect(screen.getByText("Here you go.")).toBeInTheDocument());
}

describe("TM14: Type it in is offered only for a one-line command", () => {
  it("a two-line suggestion is not typed in: it says why and offers Copy of the whole block", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { clipboardWriteText: writeText };
    try {
      await askFor("rm -rf build\nmake all");
      expect(screen.queryByRole("button", { name: /Type it in/ })).toBeNull();
      expect(
        screen.getByText("This is more than one line: copy it and run it a line at a time."),
      ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /^\s*Copy\s*$/ }));
      await waitFor(() => expect(writeText).toHaveBeenCalledWith("rm -rf build\nmake all"));
      expect(hooks.typed).toEqual([]);
    } finally {
      delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
    }
  });

  it("blank lines around one command still count as one line, typed without any line break", async () => {
    await askFor("\nnpm test\n\n");
    const btn = await screen.findByRole("button", { name: /Type it in/ });
    await waitFor(() => expect(btn).not.toBeDisabled());
    fireEvent.click(btn);
    expect(hooks.typed).toEqual(["npm test"]);
  });

  it("a one-line command with a trailing line break is typed WITHOUT it (the user presses Enter)", async () => {
    await askFor("npm test\r\n");
    const btn = await screen.findByRole("button", { name: /Type it in/ });
    await waitFor(() => expect(btn).not.toBeDisabled());
    fireEvent.click(btn);
    expect(hooks.typed).toEqual(["npm test"]);
    expect(hooks.typed.join("")).not.toMatch(/[\r\n]/);
  });

  it("a plain one-line command is typed as it is", async () => {
    await askFor("git status");
    const btn = await screen.findByRole("button", { name: /Type it in/ });
    await waitFor(() => expect(btn).not.toBeDisabled());
    fireEvent.click(btn);
    expect(hooks.typed).toEqual(["git status"]);
  });
});
