/**
 * v1.423.0 (review of Wave 3 lane B) — "Copy as a fix request", the two
 * places the button said something that was not so:
 *
 *  - "Copied: 60 problems" over a report that LISTS 15 (the rest did not fit
 *    the 30,000-character cap): the daemon now says how many the text lists
 *    (`listed`) and the button says "Copied: 15 of 60 problems …";
 *  - "Nothing to report" when the daemon could NOT READ a source (or left
 *    calls out — `complete: false`): the module's rule is "never a silent
 *    nothing found", so the button says the daemon's line instead.
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

import { FixRequestButton, copiedWords } from "@/components/team/FixRequestButton";

const ALL = "/team/fix-request?days=7";
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

describe("the count it says is the count it copied", () => {
  it("says how many of the problems the copied text lists", async () => {
    api.gets[ALL] = { text: "# Fix request …", problems: 60, listed: 15 };
    render(<FixRequestButton />);
    await press();
    expect(written).toEqual(["# Fix request …"]);
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe(
        "Copied: 15 of 60 problems from the last 7 days",
      ),
    );
  });

  it("an answer without `listed` (or listing them all) keeps the plain words", async () => {
    expect(copiedWords(3)).toBe("Copied: 3 problems from the last 7 days");
    expect(copiedWords(3, 7, 3)).toBe("Copied: 3 problems from the last 7 days");
    expect(copiedWords(20, 7, 1)).toBe("Copied: 1 of 20 problems from the last 7 days");
    api.gets[ALL] = { text: "# Fix request …", problems: 4 };
    render(<FixRequestButton />);
    await press();
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe("Copied: 4 problems from the last 7 days"),
    );
  });
});

describe("never a silent nothing found", () => {
  it("says the daemon's line when a source could not be read", async () => {
    const line =
      "Nothing to report: Iron Jarvis ran into no problems in the coding sessions and their model team in " +
      "the last 7 days (could not read: the tool-call ledger).";
    api.gets[ALL] = { text: line, problems: 0, listed: 0, complete: false };
    render(<FixRequestButton />);
    await press();
    expect(written).toEqual([]);
    await waitFor(() => expect(screen.getByTestId("fix-request-status").textContent).toBe(line));
    expect(screen.getByTestId("fix-request-status").className).toContain("text-tone-warn");
  });

  it("says the daemon's line when calls were left out", async () => {
    const line =
      "Nothing to report: Iron Jarvis ran into no problems in the coding sessions and their model team in " +
      "the last 7 days (1 call from coding sessions whose replies could not be read (whether they ended " +
      "at low trust is not known) were left out).";
    api.gets[ALL] = { text: line, problems: 0, listed: 0, complete: false };
    render(<FixRequestButton />);
    await press();
    await waitFor(() => expect(screen.getByTestId("fix-request-status").textContent).toBe(line));
  });

  it("a plain nothing-to-report keeps its short words", async () => {
    api.gets[ALL] = {
      text: "Nothing to report: Iron Jarvis ran into no problems in the coding sessions and their model team in the last 7 days.",
      problems: 0,
      listed: 0,
      complete: true,
    };
    render(<FixRequestButton />);
    await press();
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe("Nothing to report from the last 7 days"),
    );
  });
});
