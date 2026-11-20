/**
 * R0.2 (build-out plan, Stage 0) — "Copy as a fix request" leaves out Local
 * only work. The daemon (`agents/fix_request`) never puts a Local only coding
 * session into the report and says what it left out
 * (`local_only_left_out`). When that leaves nothing to copy, the button says
 * the daemon's own line in the quiet tone — never a bare "Nothing to report"
 * over work that was left out on purpose, and never the warning tone for the
 * user's own choice.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { ApiError, gets: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    if (!(p in api.gets)) return Promise.reject(new api.ApiError("Not Found", 404));
    return Promise.resolve(api.gets[p]);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

import { FixRequestButton, nothingSaid } from "@/components/team/FixRequestButton";

const ALL = "/team/fix-request?days=7";
const ONE = "/team/fix-request?days=7&session=chat_10ca10ca10ca";
const SCOPE_LINE = "This coding session is Local only, so nothing from it goes into a fix request.";
let written: string[] = [];

beforeEach(() => {
  api.gets = {};
  written = [];
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
    clipboardWriteText: (text: string) => {
      written.push(text);
      return Promise.resolve();
    },
  };
});

afterEach(() => {
  cleanup();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

async function press() {
  fireEvent.click(screen.getByTestId("fix-request-copy"));
  await waitFor(() => expect(screen.getByTestId("fix-request-copy").hasAttribute("disabled")).toBe(false));
}

describe("Local only work left out is said, quietly", () => {
  it("a Local only coding session's own button says the daemon's line and copies nothing", async () => {
    api.gets[ONE] = {
      text: SCOPE_LINE,
      problems: 0,
      listed: 0,
      complete: true,
      sessions: 0,
      local_only_left_out: 1,
    };
    render(<FixRequestButton session="chat_10ca10ca10ca" />);
    await press();
    await waitFor(() => expect(screen.getByTestId("fix-request-status").textContent).toBe(SCOPE_LINE));
    expect(written).toEqual([]);
    expect(screen.getByTestId("fix-request-status").className).not.toContain("text-tone-warn");
  });

  it("the whole report with only Local only work says what was left out", async () => {
    const line =
      "Nothing to report: Iron Jarvis ran into no problems in the coding sessions and their model team in " +
      "the last 7 days (1 Local only coding session was left out: Local only work never goes into a fix request).";
    api.gets[ALL] = { text: line, problems: 0, listed: 0, complete: true, local_only_left_out: 1 };
    render(<FixRequestButton />);
    await press();
    await waitFor(() => expect(screen.getByTestId("fix-request-status").textContent).toBe(line));
    expect(written).toEqual([]);
  });

  it("problems beside Local only work are copied as before (the text says what was left out)", async () => {
    api.gets[ALL] = {
      text: "# Fix request …\n- Left out: 1 Local only coding session",
      problems: 3,
      listed: 3,
      complete: true,
      local_only_left_out: 1,
    };
    render(<FixRequestButton />);
    await press();
    expect(written).toEqual(["# Fix request …\n- Left out: 1 Local only coding session"]);
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe("Copied: 3 problems from the last 7 days"),
    );
  });
});

describe("nothingSaid", () => {
  it("keeps the short words when nothing was left out, and an older daemon's answer reads as none", () => {
    expect(nothingSaid({ text: "x", complete: true, localOnly: 0 })).toEqual({
      tone: "quiet",
      text: "Nothing to report from the last 7 days",
    });
    expect(nothingSaid({ text: "x", complete: false, localOnly: 2 })).toEqual({ tone: "warn", text: "x" });
    expect(nothingSaid({ text: "x", complete: true, localOnly: 2 })).toEqual({ tone: "quiet", text: "x" });
  });
});
