/**
 * v1.422.0 (audit gap B6): a NEW terminal no longer defaults to the Microsoft
 * Store copy of PowerShell 7 (its start went through package activation and
 * hung in the v1.407 / v1.409 / v1.419 freezes). The daemon lists its shells
 * default first and names the default; the menu shows each shell's words and
 * says what the Store copy is. An older daemon (names only, no default) is
 * shown exactly as before.
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
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions, center }: { title: string; actions?: React.ReactNode; center?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <div data-testid="page-header-center">{center}</div>
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

import { STORE_SHELL_TITLE, defaultShellName, shellLabel } from "@/lib/shells";

const STORE_ONLY = {
  shells: [
    { name: "powershell", argv: ["powershell", "-NoLogo"], label: "Windows PowerShell" },
    { name: "pwsh", argv: ["C:/Users/x/AppData/Local/Microsoft/WindowsApps/pwsh.exe", "-NoLogo"], label: "PowerShell 7 (Microsoft Store copy)", store: true },
    { name: "cmd", argv: ["cmd"], label: "Command Prompt" },
  ],
  default: "powershell",
};

beforeEach(() => {
  localStorage.clear();
  api.posts = [];
  api.responses = {
    "/terminals": { terminals: [] },
    "/terminals/shells": STORE_ONLY,
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [] },
  };
});
afterEach(cleanup);

async function shellSelect(): Promise<HTMLSelectElement> {
  render(<TerminalsPage />);
  const sel = (await screen.findByTestId("header-shell")) as HTMLSelectElement;
  await waitFor(() => expect(sel.options.length).toBeGreaterThan(1));
  return sel;
}

describe("the New terminal shell", () => {
  it("defaults to the daemon's default, shows each shell's words, and says what the Store copy is", async () => {
    const sel = await shellSelect();
    await waitFor(() => expect(sel.value).toBe("powershell"));
    expect(Array.from(sel.options).map((o) => o.textContent)).toEqual([
      "Windows PowerShell",
      "PowerShell 7 (Microsoft Store copy)",
      "Command Prompt",
    ]);
    const store = Array.from(sel.options).find((o) => o.value === "pwsh")!;
    expect(store.getAttribute("title")).toBe(STORE_SHELL_TITLE);
    fireEvent.click(screen.getByTestId("header-new-terminal"));
    await waitFor(() => {
      const create = api.posts.find(([p]) => p === "/terminals");
      expect(create).toBeTruthy();
      expect((create![1] as { shell?: string }).shell).toBe("powershell");
    });
  });

  it("the Store copy can still be picked", async () => {
    const sel = await shellSelect();
    fireEvent.change(sel, { target: { value: "pwsh" } });
    fireEvent.click(screen.getByTestId("header-new-terminal"));
    await waitFor(() => {
      const create = api.posts.find(([p]) => p === "/terminals");
      expect((create![1] as { shell?: string }).shell).toBe("pwsh");
    });
  });

  it("the NAMED default wins even when it is not the first row", async () => {
    api.responses["/terminals/shells"] = { shells: [STORE_ONLY.shells[1], STORE_ONLY.shells[0]], default: "powershell" };
    const sel = await shellSelect();
    await waitFor(() => expect(sel.value).toBe("powershell"));
  });

  it("an older daemon (names only, no default) is shown exactly as before", async () => {
    api.responses["/terminals/shells"] = { shells: [{ name: "pwsh" }, { name: "cmd" }] };
    const sel = await shellSelect();
    await waitFor(() => expect(sel.value).toBe("pwsh"));
    expect(Array.from(sel.options).map((o) => o.textContent)).toEqual(["pwsh", "cmd"]);
  });

  it("the helpers alone", () => {
    expect(shellLabel({ name: "cmd", argv: ["cmd"], label: "  " })).toBe("cmd");
    expect(defaultShellName([], "powershell")).toBe("");
    expect(defaultShellName([{ name: "cmd", argv: [] }], "gone")).toBe("cmd");
  });
});
