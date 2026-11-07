/**
 * v1.443.0 — Creative Studio's Autopilot starts OFF.
 *
 * Autopilot launches Claude Code / Codex with every approval prompt and the
 * sandbox switched off. It used to start ticked (`?? true`), and the page
 * saved the box's value on every visit, so the stored `autopilot: true` of an
 * existing install could not tell a choice from the old default. The box now
 * reads only `autopilotOn`, which is written from this release on.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const api = vi.hoisted(() => ({
  getResponses: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "http://api.test",
    ijToken: () => "tok",
    get: (path: string) => {
      const r = api.getResponses[path];
      return r === undefined
        ? Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404))
        : Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path === null ? null : (api.getResponses[path] ?? null),
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));

import CreativePage from "@/app/creative/page";

const STUDIO_KEY = "ironjarvis.creative.studio";
const VIEW_KEY = "ironjarvis.creative.view";

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem(VIEW_KEY, "create");
  api.getResponses = {
    "/creative/items?limit=500": { items: [], count: 0 },
    "/terminals/ai-clis": {
      clis: [{ id: "claude", label: "Claude Code", installed: true, command: "claude" }],
    },
    "/skills": { skills: [] },
  };
  window.HTMLElement.prototype.scrollTo = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function autopilotBox(): Promise<HTMLInputElement> {
  return (await screen.findByRole("checkbox", { name: /Autopilot/i })) as HTMLInputElement;
}

describe("Creative Studio's Autopilot", () => {
  it("starts off on a fresh install", async () => {
    render(<CreativePage />);
    expect((await autopilotBox()).checked).toBe(false);
  });

  it("stays off for an install that saved the OLD always-on value", async () => {
    window.localStorage.setItem(STUDIO_KEY, JSON.stringify({ cli: "claude", autopilot: true }));
    render(<CreativePage />);
    expect((await autopilotBox()).checked).toBe(false);
  });

  it("is on only when the user ticked it in this release", async () => {
    window.localStorage.setItem(STUDIO_KEY, JSON.stringify({ cli: "claude", autopilotOn: true }));
    render(<CreativePage />);
    expect((await autopilotBox()).checked).toBe(true);
  });
});
