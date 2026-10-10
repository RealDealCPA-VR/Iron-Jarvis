/**
 * v1.329.0, calm chat wave 8 (J5): the Build header's two last boxed controls
 * go quiet.
 *
 * The closing audit (fa2__build__desk.png) found the header still holding an
 * uppercase "SHELL" caption beside a hairline-boxed select, and a hairline-
 * boxed "+ New terminal", while the rest of the page (the rail's own footer,
 * its rows) had moved to borderless ghosts that fill on hover. These pin the
 * quiet shapes AND that both controls still work: the shell picked in the
 * select is the shell the New terminal press asks the daemon for.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    posts: [] as [string, unknown][],
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push([path, body]);
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/dynamic", () => ({
  default: () =>
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
      const info = props.info as { id: string; shell: string };
      return <div data-testid={`terminal-pane-${info.id}`}>{info.shell}</div>;
    },
}));
vi.mock("react-rnd", () => ({
  Rnd: ({ children }: { children?: React.ReactNode }) => <div data-testid="rnd">{children}</div>,
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <div data-testid="header-actions">{actions}</div>
    </div>
  ),
}));
vi.mock("@/components/ui", () => ({
  Card: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  OfflineHint: () => <div />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
  Empty: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));

import TerminalsPage from "@/app/terminals/page";

const tokens = (cls: string) => cls.split(/\s+/).filter(Boolean);

/** A class that draws a box edge or a glow: any border-*, ring-* or shadow-*
 *  outside the keyboard focus ring, plus the old `field` / `btn-ghost`
 *  components (both carry a hairline border). `border-0` says "no border". */
function boxed(cls: string): string[] {
  return tokens(cls).filter(
    (c) =>
      !c.startsWith("focus-visible:") &&
      c !== "border-0" &&
      (/^(?:hover:)?(?:border|ring|shadow)(?:-|$)/.test(c) || c === "field" || c === "btn-ghost"),
  );
}

beforeEach(() => {
  localStorage.clear();
  api.posts = [];
  api.responses = {
    "/terminals": {
      terminals: [
        {
          id: "t1",
          cwd: "C:\\Sample\\alpha",
          shell: "pwsh",
          argv: [],
          cols: 120,
          rows: 30,
          alive: true,
          exit_code: null,
          created_at: "2026-10-09T12:00:00",
        },
      ],
    },
    "/terminals/shells": { shells: [{ name: "pwsh" }, { name: "cmd" }] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [] },
  };
});
afterEach(cleanup);

describe("the Build header is quiet like the rest of the page", () => {
  it("the shell caption is a sentence-case word, not an uppercase tracked label", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const actions = screen.getByTestId("header-actions");
    const label = Array.from(actions.querySelectorAll("label")).find((l) => l.textContent?.trim() === "Shell");
    expect(label, "the Shell caption is still there").toBeTruthy();
    const cls = tokens(label!.className);
    expect(cls).not.toContain("uppercase");
    expect(cls.filter((c) => c.startsWith("tracking-"))).toEqual([]);
    // Whole-pixel text, and no half-pixel or 11px caption shrink.
    expect(cls).toContain("text-[13px]");
  });

  it("the shell select is a borderless ghost that fills on hover", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const sel = screen.getByLabelText("Shell") as HTMLSelectElement;
    expect(sel).toBe(screen.getByTestId("header-shell"));
    expect(boxed(sel.className)).toEqual([]);
    expect(tokens(sel.className)).toContain("bg-transparent");
    expect(tokens(sel.className)).toContain("hover:bg-white/[0.06]");
  });

  it("New terminal is a borderless ghost that fills on hover", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const b = screen.getByTestId("header-new-terminal");
    expect(boxed(b.className)).toEqual([]);
    expect(tokens(b.className)).toContain("hover:bg-white/[0.06]");
    expect(tokens(b.className).filter((c) => /accent/.test(c) && !c.startsWith("focus-visible:"))).toEqual([]);
    expect(b.textContent?.trim()).toBe("New terminal");
  });

  it("both still work: the picked shell is the one New terminal asks for", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const sel = screen.getByLabelText("Shell") as HTMLSelectElement;
    await waitFor(() => expect(Array.from(sel.options).map((o) => o.value)).toEqual(["pwsh", "cmd"]));
    fireEvent.change(sel, { target: { value: "cmd" } });
    expect(sel.value).toBe("cmd");
    fireEvent.click(screen.getByTestId("header-new-terminal"));
    await waitFor(() => {
      const create = api.posts.find(([p]) => p === "/terminals");
      expect(create).toBeTruthy();
      expect((create![1] as { shell?: string }).shell).toBe("cmd");
    });
  });

  it("control: the boxed-class check catches the old shapes", () => {
    expect(boxed("field w-auto py-1.5 text-[13px]")).toEqual(["field"]);
    expect(boxed("btn-ghost flex items-center gap-1.5 py-1.5 text-[13px]")).toEqual(["btn-ghost"]);
    expect(boxed("rounded-lg border border-white/10 px-2")).toEqual(["border", "border-white/10"]);
    expect(boxed("border-0 focus-visible:ring-1 hover:bg-white/[0.06]")).toEqual([]);
  });
});
