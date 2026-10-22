/**
 * v1.369.0 review — the folder tree never starts its first load DURING RENDER.
 *
 * Every cold load of Build logged React's "Can't perform a React state update
 * on a component that hasn't mounted yet" (3 of 10 cold loads of /terminals,
 * measured; 0 of 22 after the fix, and 0 of 10 on /everything and /projects,
 * which draw no tree). The cause dates from v1.0: DirectoryTree's root node
 * called `load()` from its render body, so a render React threw away (a
 * sibling suspending, StrictMode's second pass, a restarted transition)
 * still fetched, and its answer then set state on a fiber that never
 * mounted — and the root was fetched twice. Master has it too; the IJ
 * harness's New session form draws the same tree.
 *
 * Reproduced here deterministically: the tree renders beside a sibling that
 * suspends ONCE, so the first render is thrown away. The old shape logs the
 * warning; the load now runs in an effect, after a commit.
 */

import React, { Suspense } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ list: 0 }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 0;
  }
  return {
    ApiError,
    get: (p: string) => {
      if (p.startsWith("/fs/list")) {
        calls.list += 1;
        return Promise.resolve({ entries: [{ name: "work", path: "C:\\work", is_project: null }] });
      }
      return Promise.reject(new ApiError(`unmocked ${p}`));
    },
  };
});
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path === "/fs/drives" ? { drives: [{ path: "C:\\", label: "C:" }] } : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

import { DirectoryTree } from "@/components/terminal/DirectoryTree";

afterEach(() => {
  cleanup();
  calls.list = 0;
  vi.restoreAllMocks();
});

/** A sibling that suspends on its first render only. */
function suspendOnce() {
  let done = false;
  let resolve!: () => void;
  const gate = new Promise<void>((r) => (resolve = () => {
    done = true;
    r();
  }));
  function Gate() {
    if (!done) throw gate;
    return null;
  }
  return { Gate, release: resolve };
}

describe("the folder tree's first load", () => {
  for (const variant of ["calm", "card"] as const) {
    it(`(${variant}) a render React throws away starts no load, and nothing updates a component that never mounted`, async () => {
      const errors: string[] = [];
      vi.spyOn(console, "error").mockImplementation((...a: unknown[]) => {
        errors.push(a.map(String).join(" "));
      });
      const { Gate, release } = suspendOnce();
      render(
        <Suspense fallback={<p>waiting</p>}>
          <Gate />
          <DirectoryTree variant={variant} selectedPath={null} onSelect={() => {}} hideAction />
        </Suspense>,
      );
      // The suspended render's answers land while nothing is mounted.
      await act(async () => {
        await new Promise((r) => setTimeout(r, 30));
      });
      expect(calls.list).toBe(0);
      await act(async () => {
        release();
        await new Promise((r) => setTimeout(r, 30));
      });
      // The committed tree loads its root once and shows it.
      expect(await screen.findByText("work")).toBeInTheDocument();
      expect(calls.list).toBe(1);
      expect(errors.filter((e) => /hasn't mounted yet/.test(e))).toEqual([]);
    });
  }
});
