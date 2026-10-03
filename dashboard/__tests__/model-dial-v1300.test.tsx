/**
 * v1.300.0 review — the quality dial never picks a LEGACY Claude model.
 *
 * `resolveTiers` ranks a Claude provider's rows by family (haiku / sonnet /
 * opus) and version. The first cut read "the digits after the family word"
 * as the version, so a Claude 3 era id — `claude-3-haiku-20240307`,
 * `claude-3-7-sonnet-20250219` — had its 8-digit DATE read as the version and
 * outranked every 4.x/5.x model: an API-key anthropic's dial put "Fast" on
 * Claude 3 Haiku and "Balanced" on Claude 3.7 Sonnet.
 *
 * `familyVersion` now reads both shapes — `claude-<family>-<major>[-<minor>]`
 * and `claude-<major>[-<minor>]-<family>` — and never treats an 8-digit date
 * or `[1m]` as a version.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  models: [] as Record<string, unknown>[],
  provider: "anthropic",
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 500;
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    get: async (path: string) => {
      if (path === "/models") return { models: H.models };
      throw new ApiError(`unmocked GET ${path}`);
    },
    post: async () => ({}),
    put: async () => ({}),
    patch: async () => ({}),
    del: async () => ({}),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    refresh: () => {},
    health: {
      status: "ok",
      version: "test",
      default_provider: H.provider,
      default_model: "claude-opus-4-8",
      providers: [
        { provider: "anthropic", available: true, class: "api" },
        { provider: "claude-cli", available: true, class: "cli" },
      ],
    },
  }),
}));

import { familyVersion, resolveTiers } from "@/components/ModelSwitcher";

type Rows = Parameters<typeof resolveTiers>[1];
const row = (provider: string, model: string, label?: string) =>
  ({ provider, model, available: true, ...(label ? { label } : {}) }) as Rows[number];

afterEach(() => {
  cleanup();
  H.models = [];
  H.provider = "anthropic";
});

describe("familyVersion reads both id shapes", () => {
  it("new shape: claude-<family>-<major>[-<minor>], date never a version", () => {
    expect(familyVersion("claude-opus-5-5", "opus")).toEqual([5, 5]);
    expect(familyVersion("claude-sonnet-5", "sonnet")).toEqual([5]);
    expect(familyVersion("claude-haiku-4-5-20251001", "haiku")).toEqual([4, 5]);
    expect(familyVersion("claude-opus-4-20250514", "opus")).toEqual([4]);
    expect(familyVersion("claude-fable-5-1", "fable")).toEqual([5, 1]);
    expect(familyVersion("Opus 5.5", "opus")).toEqual([5, 5]);
    expect(familyVersion("Haiku 4.5", "haiku")).toEqual([4, 5]);
  });

  it("Claude 3 era shape: claude-<major>[-<minor>]-<family>", () => {
    expect(familyVersion("claude-3-haiku-20240307", "haiku")).toEqual([3]);
    expect(familyVersion("claude-3-7-sonnet-20250219", "sonnet")).toEqual([3, 7]);
    expect(familyVersion("claude-3-5-sonnet-20241022", "sonnet")).toEqual([3, 5]);
    expect(familyVersion("claude-3-opus-20240229", "opus")).toEqual([3]);
    expect(familyVersion("Claude 3.5 Sonnet", "sonnet")).toEqual([3, 5]);
  });

  it("[1m] is never a version; a family not named is null", () => {
    expect(familyVersion("claude-opus-5-5[1m]", "opus")).toEqual([5, 5]);
    expect(familyVersion("claude-sonnet-5[1m]", "sonnet")).toEqual([5]);
    expect(familyVersion("claude-sonnet-4-6[1M]", "sonnet")).toEqual([4, 6]);
    expect(familyVersion("claude-opus-5-5", "haiku")).toBeNull();
    expect(familyVersion("subscription", "opus")).toBeNull();
    // the family word with no version at all ranks below any version
    expect(familyVersion("sonnet", "sonnet")).toEqual([]);
  });

  it("a context size is not a version either ('Sonnet 1M', 'Opus (1M context)')", () => {
    expect(familyVersion("Sonnet 1M", "sonnet")).toEqual([]);
    expect(familyVersion("Opus 1m", "opus")).toEqual([]);
    expect(familyVersion("Opus (1M context)", "opus")).toEqual([]);
    // and a row whose label says only that is ranked by its id
    const rows = [
      row("claude-cli", "claude-sonnet-4-5", "Sonnet 4.5"),
      row("claude-cli", "claude-sonnet-4-6", "Sonnet 1M"),
    ];
    expect(resolveTiers("claude-cli", rows)?.balanced).toBe("claude-sonnet-4-6");
  });

  it("a label that names a version outranks the id's (an alias id names less)", () => {
    // The picker's label is what the alias resolves to TODAY: "claude-sonnet-5"
    // labelled "Sonnet 5.5" is newer than a row that really is Sonnet 5.2.
    const rows = [
      row("claude-cli", "claude-sonnet-5-2", "Sonnet 5.2"),
      row("claude-cli", "claude-sonnet-5", "Sonnet 5.5"),
    ];
    expect(resolveTiers("claude-cli", rows)?.balanced).toBe("claude-sonnet-5");
  });
});

describe("resolveTiers picks the newest of each family", () => {
  it("an API-key anthropic with Claude 3 ids never puts them on the dial", () => {
    const rows: Rows = [
      row("anthropic", "claude-3-haiku-20240307"),
      row("anthropic", "claude-3-7-sonnet-20250219"),
      row("anthropic", "claude-3-5-sonnet-20241022"),
      row("anthropic", "claude-3-opus-20240229"),
      row("anthropic", "claude-haiku-4-5"),
      row("anthropic", "claude-sonnet-4-6"),
      row("anthropic", "claude-opus-4-8"),
    ];
    expect(resolveTiers("anthropic", rows)).toEqual({
      fast: "claude-haiku-4-5",
      balanced: "claude-sonnet-4-6",
      best: "claude-opus-4-8",
    });
  });

  it("a mixed set incl. 5.x, dated ids, [1m] and legacy picks the newest", () => {
    const rows: Rows = [
      row("anthropic", "claude-3-haiku-20240307"),
      row("anthropic", "claude-3-7-sonnet-20250219"),
      row("anthropic", "claude-opus-4-20250514"),
      row("anthropic", "claude-opus-4-8"),
      row("anthropic", "claude-sonnet-4-6[1m]"),
      row("anthropic", "claude-haiku-4-5-20251001"),
      row("anthropic", "claude-sonnet-5"),
      row("anthropic", "claude-opus-5-5"),
      row("anthropic", "claude-fable-5-1"),
    ];
    const want = {
      fast: "claude-haiku-4-5-20251001",
      balanced: "claude-sonnet-5",
      best: "claude-opus-5-5",
    };
    expect(resolveTiers("anthropic", rows)).toEqual(want);
    // the same rows as claude-cli, order reversed: the answer does not depend on order
    const cli = rows.map((r) => ({ ...r, provider: "claude-cli" })).reverse();
    expect(resolveTiers("claude-cli", cli)).toEqual(want);
  });

  it("a label without a version falls back to the id's version", () => {
    const rows: Rows = [
      row("claude-cli", "claude-3-7-sonnet-20250219", "Sonnet"),
      row("claude-cli", "claude-sonnet-5-5", "Sonnet"),
      row("claude-cli", "claude-3-haiku-20240307", "Haiku"),
      row("claude-cli", "claude-haiku-4-5-20251001", "Haiku 4.5"),
      row("claude-cli", "claude-opus-5-5", "Opus 5.5"),
    ];
    expect(resolveTiers("claude-cli", rows)).toEqual({
      fast: "claude-haiku-4-5-20251001",
      balanced: "claude-sonnet-5-5",
      best: "claude-opus-5-5",
    });
  });
});

describe("the switcher's dial on an API-key anthropic with legacy ids", () => {
  it("Fast and Balanced name the 4.x models, not Claude 3", async () => {
    H.models = [
      row("anthropic", "claude-3-haiku-20240307"),
      row("anthropic", "claude-3-7-sonnet-20250219"),
      row("anthropic", "claude-haiku-4-5"),
      row("anthropic", "claude-sonnet-4-6"),
      row("anthropic", "claude-opus-4-8"),
    ] as unknown as Record<string, unknown>[];
    const { ModelSwitcher } = await import("@/components/ModelSwitcher");
    render(<ModelSwitcher />);
    fireEvent.click(await screen.findByRole("button", { name: /switch the active model/i }));
    const fast = await screen.findByRole("button", { name: /^Fast/ });
    await waitFor(() => expect(fast).toHaveAttribute("title", "claude-haiku-4-5"));
    expect(screen.getByRole("button", { name: /^Balanced/ })).toHaveAttribute("title", "claude-sonnet-4-6");
    expect(screen.getByRole("button", { name: /^Best/ })).toHaveAttribute("title", "claude-opus-4-8");
  });
});
