/**
 * v1.431.0 — a Pi folder's OWN extensions, skills and settings in Build › Code.
 *
 * Pi skips them here (its terminal would ask; RPC mode cannot), so the Code
 * view says so in one quiet line with ONE press that loads them for this
 * session only (the daemon starts Pi with `--approve`). Nothing loads
 * without the press; a session that loads them can go back.
 *
 * THE CONTRACT: `pi_folder` is what the REAL daemon sends
 * (tests/test_build_app_pi_folder_trust_v14310.py,
 * IJ_REGEN_BUILD_APP_PI_FOLDER=1). Only the network (lib/api), the stream
 * reader and the folder tree are stand-ins.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  patches: [] as { path: string; body: unknown }[],
  patchAnswer: {} as unknown,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      const key = path.replace(/\?limit=1$/, "");
      const r = H.gets[key];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    patch: (path: string, body: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve(H.patchAnswer);
    },
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => null,
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { LAST_TOOL_KEY } from "@/components/buildCode/StartComposer";
import { PI_FOLDER_LOAD_LABEL, PI_FOLDER_SKIP_LABEL, decodeCodeSessionView, piFolderOf } from "@/lib/buildApp";

const REAL = join(process.cwd(), "__tests__", "fixtures", "build-app-pi-folder-v1431.json");
const DOC = JSON.parse(readFileSync(REAL, "utf8")) as {
  offered: Record<string, unknown>;
  loaded: { pi_folder: Record<string, unknown>; applied: Record<string, unknown> };
  back: { pi_folder: Record<string, unknown>; applied: Record<string, unknown> };
};

const sess = (over: Record<string, unknown> = {}) => ({
  id: "p1",
  harness: "pi",
  folder: "C:\\work\\shop",
  title: "Session p1",
  account: "",
  model: "",
  effort: "",
  mode: "own",
  harness_session_id: "01a1b2c3-1111-7222-8333-444444444444",
  created_at: "2026-11-04T09:00:00Z",
  updated_at: "2026-11-04T09:00:00Z",
  archived_at: null,
  last_error: "",
  load_folder_resources: false,
  ...over,
});

function seed(view: Record<string, unknown>) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [{ id: "pi", label: "Pi", installed: true, models: [], modes: [] }] },
    "/build/app/sessions": { sessions: [sess()] },
    "/editor/options": { editors: [], preferred: "" },
    "/build/app/sessions/p1": { session: sess(), events: [], running: null, pending: [], commands: [], ...view },
  };
}

beforeEach(() => {
  H.patches = [];
  H.patchAnswer = {};
  window.localStorage.clear();
  window.localStorage.setItem(LAST_TOOL_KEY, "pi");
});

afterEach(() => cleanup());

describe("pi_folder from the real daemon", () => {
  it("decodes the offer, the loaded state and an answer without the key", () => {
    const offered = piFolderOf({ pi_folder: DOC.offered });
    expect(offered).toEqual({
      line: DOC.offered.line,
      title: DOC.offered.title,
      offer: true,
      loaded: false,
    });
    expect(offered!.line).toMatch(/extensions and skills/);
    const loaded = piFolderOf(DOC.loaded);
    expect(loaded?.loaded).toBe(true);
    expect(loaded?.offer).toBe(false);
    expect(piFolderOf({ session: {} })).toBeUndefined();
    expect(piFolderOf({ pi_folder: null })).toBeNull();
    expect(piFolderOf({ pi_folder: { line: "  ", offer: true } })).toBeNull();
    // Only the daemon's own `true` offers the press (never a missing key).
    expect(piFolderOf({ pi_folder: { line: "x" } })).toMatchObject({ offer: false, loaded: false });
    expect(piFolderOf({ pi_folder: { line: "x", offer: "true" } })?.offer).toBe(false);
    expect(decodeCodeSessionView({ session: sess(), events: [] })?.piFolder).toBeNull();
  });

  it("turning it off answers the session back on Pi's own rule (the offer again)", () => {
    expect(piFolderOf(DOC.back)).toMatchObject({ offer: true, loaded: false });
    expect(DOC.loaded.applied).toEqual({ load_folder_resources: "next" });
  });
});

describe("in a Pi session", () => {
  it("says the folder's resources are skipped and loads them with ONE press", async () => {
    seed({ pi_folder: DOC.offered });
    H.patchAnswer = { session: sess({ load_folder_resources: true }), ...DOC.loaded };
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    const line = await screen.findByTestId("code-pi-folder");
    expect(line.textContent).toContain(String(DOC.offered.line));
    const load = screen.getByTestId("code-pi-folder-load");
    expect(load.textContent).toBe(PI_FOLDER_LOAD_LABEL);
    expect(load.getAttribute("title")).toBe(DOC.offered.title);
    expect(H.patches).toEqual([]);
    fireEvent.click(load);
    await waitFor(() => expect(screen.getByTestId("code-pi-folder-skip").textContent).toBe(PI_FOLDER_SKIP_LABEL));
    expect(H.patches).toEqual([{ path: "/build/app/sessions/p1", body: { load_folder_resources: true } }]);
    expect(screen.getByTestId("code-pi-folder").textContent).toContain(String(DOC.loaded.pi_folder.line));
  });

  it("a session that loads them can go back to Pi's own rule", async () => {
    seed({ session: sess({ load_folder_resources: true }), pi_folder: DOC.loaded.pi_folder });
    H.patchAnswer = { session: sess(), ...DOC.back };
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-pi-folder-skip"));
    await waitFor(() => expect(screen.getByTestId("code-pi-folder-load")).toBeTruthy());
    expect(H.patches).toEqual([{ path: "/build/app/sessions/p1", body: { load_folder_resources: false } }]);
  });

  it("says nothing when the daemon says nothing", async () => {
    seed({});
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-box", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("code-pi-folder")).toBeNull();
  });
});
