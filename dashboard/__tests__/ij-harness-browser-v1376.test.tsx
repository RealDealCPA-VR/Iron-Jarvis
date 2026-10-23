/**
 * v1.376.0 (IJ harness part 2, Phase 5) — the Browser pane shows what the
 * AGENT'S OWN browser last saw (components/harness/BrowserPreview
 * AgentBrowserView, lib/agentBrowser):
 *
 *  - "The agent is on localhost:5173/cart" and the picture taken after its
 *    last action (`GET /build/sessions/{id}/browser` + `…/browser/shot`);
 *  - read again when one of the session's page steps finishes (`pageSteps`)
 *    and when a turn ends (`refreshKey`) — never on a timer;
 *  - a closed browser says so, and why ("when you pressed Stop");
 *  - a navigation the address rules stopped is said in the pane;
 *  - the empty state, and nothing at all from an older daemon (404).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

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
  ijToken: () => "tok",
  get: (p: string) => {
    api.calls.push(p);
    return p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));

import { BrowserPreview } from "@/components/harness/BrowserPreview";
import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { agentShotSrc, closedWords, decodeAgentBrowserView, finishedPageSteps } from "@/lib/agentBrowser";
import { decodeSessionView } from "@/lib/buildSessions";
import { fireEvent } from "@testing-library/react";

const VIEW = "/build/sessions/s1/browser";
const AT = "2026-10-23T09:41:00+00:00";

function view(over: Record<string, unknown> = {}) {
  return {
    view: {
      open: true,
      where: "localhost:5173/cart",
      blank: false,
      title: "Cart",
      at: AT,
      shot: true,
      shot_at: AT,
      note: "",
      ...over,
    },
  };
}

beforeEach(() => {
  api.gets = { "/browser/status": { paired: false } };
  api.calls = [];
});
afterEach(() => cleanup());

function reads(): number {
  return api.calls.filter((p) => p === VIEW).length;
}

describe("the agent's browser in the Browser pane", () => {
  it("says where the agent is and shows its latest picture", async () => {
    api.gets[VIEW] = view();
    render(<BrowserPreview threadId="s1" seen={[]} />);
    const where = await screen.findByTestId("agent-browser-where");
    expect(where).toHaveTextContent("The agent is on localhost:5173/cart");
    const img = screen.getByTestId("agent-browser-shot") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      `http://d/build/sessions/s1/browser/shot?at=${encodeURIComponent(AT)}&token=tok`,
    );
    expect(img).toHaveAttribute("alt", "What the agent's browser showed on localhost:5173/cart");
    expect(screen.getByTestId("agent-browser")).toHaveAttribute("data-open", "true");
    expect(screen.getByTestId("agent-browser-when").textContent).toMatch(/^Updated at /);
  });

  it("reads again when a page step finishes and when a turn ends — not on a timer", async () => {
    api.gets[VIEW] = view();
    const { rerender } = render(<BrowserPreview threadId="s1" seen={[]} refreshKey={0} pageSteps={0} />);
    await screen.findByTestId("agent-browser-where");
    expect(reads()).toBe(1);
    const later = "2026-10-23T09:42:00+00:00";
    api.gets[VIEW] = view({ where: "localhost:5173/checkout", shot_at: later, at: later });
    rerender(<BrowserPreview threadId="s1" seen={[]} refreshKey={0} pageSteps={1} />);
    await waitFor(() =>
      expect(screen.getByTestId("agent-browser-where")).toHaveTextContent("localhost:5173/checkout"),
    );
    expect((screen.getByTestId("agent-browser-shot") as HTMLImageElement).getAttribute("src")).toContain(
      encodeURIComponent(later),
    );
    expect(reads()).toBe(2);
    rerender(<BrowserPreview threadId="s1" seen={[]} refreshKey={1} pageSteps={1} />);
    await waitFor(() => expect(reads()).toBe(3));
    // No timer: nothing else is read while nothing happens.
    await new Promise((r) => setTimeout(r, 300));
    expect(reads()).toBe(3);
  });

  it("says when the browser closed, and why", async () => {
    api.gets[VIEW] = view({ open: false, closed_reason: "stopped" });
    render(<BrowserPreview threadId="s1" seen={[]} />);
    expect(await screen.findByTestId("agent-browser-where")).toHaveTextContent(
      "The agent was on localhost:5173/cart",
    );
    expect(screen.getByTestId("agent-browser-when")).toHaveTextContent("Its browser closed when you pressed Stop.");
    expect(screen.getByTestId("agent-browser")).toHaveAttribute("data-open", "false");
  });

  it("says a stopped navigation, and a blank page in words", async () => {
    api.gets[VIEW] = view({
      where: "",
      blank: true,
      shot: false,
      note: "The page tried to go to example.com/docs, which is not on this PC or your network.",
    });
    render(<BrowserPreview threadId="s1" seen={[]} />);
    expect(await screen.findByTestId("agent-browser-where")).toHaveTextContent("The agent is on a blank page");
    expect(screen.getByTestId("agent-browser-note")).toHaveTextContent("example.com/docs");
    expect(screen.getByTestId("agent-browser-note").className).toContain("text-tone-warn");
    expect(screen.queryByTestId("agent-browser-shot")).toBeNull();
  });

  it("shows the empty state before the agent opened a page", async () => {
    api.gets[VIEW] = { view: null };
    render(<BrowserPreview threadId="s1" seen={[]} />);
    expect(await screen.findByTestId("agent-browser-empty")).toHaveTextContent(
      "The agent has not opened a page in its own browser in this session.",
    );
    expect(screen.queryByTestId("agent-browser")).toBeNull();
    // The user's own preview is still there.
    expect(screen.getByTestId("preview-address")).toBeInTheDocument();
  });

  it("shows nothing about it from a daemon that has no such route", async () => {
    render(<BrowserPreview threadId="s1" seen={[]} />);
    await waitFor(() => expect(reads()).toBe(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("agent-browser")).toBeNull();
    expect(screen.queryByTestId("agent-browser-empty")).toBeNull();
    expect(screen.getByTestId("preview-address")).toBeInTheDocument();
  });

  it("keeps to the pane's width (no sideways pan at 390 px)", async () => {
    api.gets[VIEW] = view({ where: "localhost:5173/" + "very-long-path/".repeat(10) });
    render(<BrowserPreview threadId="s1" seen={[]} />);
    const where = await screen.findByTestId("agent-browser-where");
    expect(where.className).toContain("min-w-0");
    expect(where.querySelector(".truncate")).not.toBeNull();
    expect(screen.getByTestId("agent-browser-shot").className).toContain("w-full");
    expect(screen.getByTestId("agent-browser").className).toContain("min-w-0");
  });
});

describe("the session's panes hand the browser pane its refresh signals", () => {
  it("a finished page step reads the agent's view again", async () => {
    api.gets[VIEW] = view();
    localStorage.clear();
    const v = decodeSessionView({ id: "s1", title: "Cart", folder: "C:\\w", folder_name: "w", session_id: "build:s1", messages: [] })!;
    const term: SessionTerminalApi = {
      find: () => null,
      open: async () => null,
      render: () => null,
      close: () => {},
    };
    const props = { view: v, livePlan: null, terminal: term, refreshKey: 0, undoFor: () => null, onUndo: async () => {} };
    const { rerender } = render(<HarnessPanes {...props} pageSteps={0} />);
    fireEvent.click(screen.getByTestId("harness-pane-tab-browser"));
    await screen.findByTestId("agent-browser-where");
    expect(reads()).toBe(1);
    rerender(<HarnessPanes {...props} pageSteps={1} />);
    await waitFor(() => expect(reads()).toBe(2));
  });
});

describe("the session screen counts its page steps for the pane", () => {
  it("passes the running turn's finished page steps to the panes", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(join(__dirname, "..", "components", "harness", "HarnessSession.tsx"), "utf-8").replace(
      /\r\n/g,
      "\n",
    );
    expect(src).toContain("pageSteps={finishedPageSteps(stream.tools)}");
  });
});

describe("lib/agentBrowser", () => {
  it("decodes only what it knows and never the raw address", () => {
    const v = decodeAgentBrowserView({ view: { open: true, where: "x:1", url: "http://x:1/?token=s", shot: 1 } });
    expect(v).not.toBeNull();
    expect(v).not.toHaveProperty("url");
    expect(v!.shot).toBe(false); // only a real true
    expect(decodeAgentBrowserView({ view: null })).toBeNull();
    expect(decodeAgentBrowserView("nonsense")).toBeNull();
  });

  it("counts the finished page steps of a turn", () => {
    expect(
      finishedPageSteps([
        { name: "page_open", status: "done" },
        { name: "page_click", status: "running" },
        { name: "read_file", status: "done" },
        { name: "page_read", status: "done" },
      ]),
    ).toBe(2);
  });

  it("words every closing reason", () => {
    const base = decodeAgentBrowserView(view({ open: false }))!;
    expect(closedWords({ ...base, closedReason: "idle" })).toBe("Its browser closed after a while without use.");
    expect(closedWords({ ...base, closedReason: "ended" })).toBe("Its browser is closed.");
    expect(closedWords({ ...base, open: true })).toBe("");
    expect(agentShotSrc("a b", "")).toBe("http://d/build/sessions/a%20b/browser/shot?token=tok");
  });
});
