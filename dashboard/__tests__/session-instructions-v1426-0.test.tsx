/**
 * v1.426.0 (Wave 6, E1) — WHAT IRON JARVIS TELLS A CODING SESSION'S MODEL is
 * visible on the Session tab (components/harness/HarnessSessionRail
 * `Instructions`, lib/sessionRail).
 *
 * The audit: "the harness's own instructions are invisible; an invalid folder
 * setup is ignored silently". Every coding reply carries `route.harness`
 * (v1.366.0) on its saved line; nothing on screen read it.
 *
 *  - the newest reply's instructions in words: whose tool names the model was
 *    shown, for which model family, what they were built from (Iron Jarvis's
 *    defaults, the tool words, this folder's own instructions, kept lessons),
 *    "planning only" for a planning turn;
 *  - a folder whose own instruction files were NOT used says so, in the
 *    daemon's own sentence, in the warning tone;
 *  - "Show the instructions" reads GET /build/sessions/{id}/harness each time
 *    it opens (put together afresh, never kept) and shows the text read-only;
 *    a failure is said, never an empty box.
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
    }
  }
  return { ApiError, gets: {} as Record<string, unknown>, calls: [] as string[] };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(p);
    if (!(p in api.gets)) return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
    const v = api.gets[p];
    return v instanceof api.ApiError ? Promise.reject(v) : Promise.resolve(v);
  },
  post: (p: string) => Promise.resolve(p === "/chat/changes" ? { changes: [] } : {}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

import { SessionRail } from "@/components/harness/HarnessSessionRail";
import { decodeSessionView, type BuildSessionView } from "@/lib/buildSessions";
import {
  decodeReplyInstructions,
  decodeSessionInstructions,
  instructionLayerWords,
  instructionToolWords,
  newestInstructions,
} from "@/lib/sessionRail";

const HARNESS = "/build/sessions/s1/harness";
const VOIDED =
  "This folder's coding-session instructions (.ironjarvis/harness) were not used: notes.txt is not a harness file.";

function view(messages: unknown[]): BuildSessionView {
  const v = decodeSessionView({
    id: "s1",
    title: "Cart",
    folder: "C:\\work\\shop",
    folder_name: "shop",
    session_id: "build:s1",
    messages,
  });
  if (!v) throw new Error("bad fixture");
  return v;
}

function reply(harness: Record<string, unknown> | null, extra: Record<string, unknown> = {}) {
  return {
    role: "assistant",
    content: "Done.",
    at: "2026-11-02T10:00:01",
    ...(harness ? { route: { provider: "anthropic", model: "claude-sonnet-4-5", harness } } : {}),
    ...extra,
  };
}

const QUESTION = { role: "user", content: "Add a cart", at: "2026-11-02T10:00:00" };

function show(v: BuildSessionView) {
  return render(
    <SessionRail view={v} messages={v.messages} modelWords="Sonnet 4.5" refreshKey={0} onOpenChanges={() => {}} />,
  );
}

beforeEach(() => {
  api.gets = { "/build/sessions/s1/programs": { programs: [], others: 0 } };
  api.calls = [];
});
afterEach(cleanup);

describe("the newest reply's instructions, in words", () => {
  it("names the tool words, the model family and what they came from", () => {
    const v = view([
      QUESTION,
      reply({ family: "claude", dialect: "claude", layers: ["default", "family:claude", "project", "library"], fingerprint: "f" }),
    ]);
    show(v);
    expect(screen.getByTestId("session-rail-instructions-tools").textContent).toBe(
      "Instructions written for Claude models",
    );
    expect(screen.getByTestId("session-rail-instructions-layers").textContent).toBe(
      "The newest reply's instructions came from Iron Jarvis's defaults, Claude Code's tool words, this folder's own instructions, lessons kept for this folder.",
    );
    expect(screen.queryByTestId("session-rail-instructions-note")).toBeNull();
  });

  it("a folder whose own instruction files were NOT used says so, in the daemon's words, as a warning", () => {
    const v = view([QUESTION, reply({ family: "claude", dialect: "claude", layers: ["default", "family:claude"], note: VOIDED })]);
    show(v);
    const note = screen.getByTestId("session-rail-instructions-note");
    expect(note.textContent).toBe(VOIDED);
    expect(note.className).toContain("text-tone-warn");
    expect(note.getAttribute("role")).toBe("status");
  });

  it("reads the NEWEST reply that says (a failed line with no route is passed over)", () => {
    const v = view([
      QUESTION,
      reply({ family: "claude", dialect: "claude", layers: ["default", "family:claude"] }),
      { role: "user", content: "now on glm", at: "2026-11-02T10:00:30" },
      reply({ family: "glm", dialect: "generic", layers: ["default", "family:generic"], mode: "plan" }),
      { role: "user", content: "again", at: "2026-11-02T10:01:00" },
      { role: "assistant", content: "", error: "The model did not answer." },
    ]);
    show(v);
    expect(screen.getByTestId("session-rail-instructions-tools").textContent).toBe(
      "Instructions written for GLM models · planning only",
    );
  });

  it("before the first reply it says when they are put together", () => {
    show(view([QUESTION]));
    expect(screen.getByTestId("session-rail-instructions-none").textContent).toContain(
      "puts its instructions together for each reply",
    );
  });
});

describe("Show the instructions", () => {
  it("reads them afresh on every opening and shows the text read-only", async () => {
    api.gets[HARNESS] = {
      text: "# IJ harness\n\n## How you work\nWork in small steps.",
      cut: false,
      note: "",
      layers: ["default", "family:claude"],
      rule_files: ["AGENTS.md"],
      mode: "act",
    };
    show(view([QUESTION, reply({ family: "claude", dialect: "claude", layers: ["default", "family:claude"] })]));
    const press = screen.getByTestId("session-rail-instructions-show");
    expect(press.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(press);
    const text = await screen.findByTestId("session-rail-instructions-text");
    expect(text.textContent).toBe("# IJ harness\n\n## How you work\nWork in small steps.");
    expect(press.getAttribute("aria-expanded")).toBe("true");
    expect(press.textContent).toBe("Hide the instructions");
    expect(api.calls.filter((c) => c === HARNESS)).toHaveLength(1);
    fireEvent.click(press);
    expect(screen.queryByTestId("session-rail-instructions-panel")).toBeNull();
    fireEvent.click(press);
    await screen.findByTestId("session-rail-instructions-text");
    expect(api.calls.filter((c) => c === HARNESS)).toHaveLength(2);
  });

  it("with no reply yet, the view's own note is said", async () => {
    api.gets[HARNESS] = { text: "# IJ harness", cut: false, note: VOIDED, layers: ["default"], mode: "act" };
    show(view([QUESTION]));
    expect(screen.queryByTestId("session-rail-instructions-note")).toBeNull();
    fireEvent.click(screen.getByTestId("session-rail-instructions-show"));
    await waitFor(() => expect(screen.getByTestId("session-rail-instructions-note").textContent).toBe(VOIDED));
  });

  it("a text the daemon cut says so", async () => {
    api.gets[HARNESS] = { text: "# IJ harness", cut: true, note: "", layers: [], mode: "act" };
    show(view([QUESTION]));
    fireEvent.click(screen.getByTestId("session-rail-instructions-show"));
    expect((await screen.findByTestId("session-rail-instructions-cut")).textContent).toContain("Only the first part");
  });

  it("a failure is said, never an empty box", async () => {
    api.gets[HARNESS] = new api.ApiError("Iron Jarvis isn't responding", 0);
    show(view([QUESTION]));
    fireEvent.click(screen.getByTestId("session-rail-instructions-show"));
    const err = await screen.findByTestId("session-rail-instructions-error");
    expect(err.textContent).toContain("could not be read");
    expect(screen.queryByTestId("session-rail-instructions-text")).toBeNull();
    api.gets[HARNESS] = { error: "The instructions could not be put together just now. Try again in a moment.", text: "" };
    fireEvent.click(screen.getByTestId("session-rail-instructions-show"));
    fireEvent.click(screen.getByTestId("session-rail-instructions-show"));
    await waitFor(() =>
      expect(screen.getByTestId("session-rail-instructions-error").textContent).toContain("could not be put together"),
    );
  });
});

describe("the readings", () => {
  it("decodes only what the daemon sends; a junk route is no reading", () => {
    expect(decodeReplyInstructions({ harness: { dialect: "", layers: ["default"] } })).toBeNull();
    expect(decodeReplyInstructions({ provider: "x" })).toBeNull();
    expect(decodeReplyInstructions(null)).toBeNull();
    const got = decodeReplyInstructions({ harness: { dialect: "gpt", family: "gpt", layers: ["default", 3, ""], note: 7 } });
    expect(got).toEqual({ family: "gpt", dialect: "gpt", layers: ["default"], note: "", plan: false });
    expect(newestInstructions([{ role: "user", route: { harness: { dialect: "gpt" } } }])).toBeNull();
  });

  it("words every dialect and leaves an unknown part out", () => {
    expect(instructionToolWords({ family: "gpt", dialect: "gpt", layers: [], note: "", plan: false })).toBe(
      "Instructions written for GPT models",
    );
    expect(instructionToolWords({ family: "generic", dialect: "qwen", layers: [], note: "", plan: false })).toBe(
      "Instructions written the way Qwen Code writes them",
    );
    expect(instructionLayerWords(["default", "family:nope", "mystery", "project"])).toBe(
      "Iron Jarvis's defaults, this folder's own instructions",
    );
  });

  it("the view's answer is decoded with its limits", () => {
    const got = decodeSessionInstructions({ text: "x".repeat(130_000), cut: true, rule_files: ["AGENTS.md", 1], mode: "plan" });
    expect(got.text.length).toBe(120_000);
    expect(got.ruleFiles).toEqual(["AGENTS.md"]);
    expect(got.plan).toBe(true);
    expect(decodeSessionInstructions(null).text).toBe("");
  });
});
