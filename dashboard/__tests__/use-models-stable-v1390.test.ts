/**
 * useModels hands back the SAME lists between renders (v1.390.0, the
 * Timeline review's open item 3): a fresh `[]` while `/models` had not
 * answered, and a fresh `usable` filter on every render, redrew every reader
 * that memoizes on them — the coding session's Timeline re-ran on every
 * streamed word.
 */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ data: null as unknown }));

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: H.data, loading: H.data === null, error: null, reload: () => {} }),
}));

import { useModels } from "@/lib/useModels";

describe("useModels keeps its lists stable between renders", () => {
  it("no catalog yet: the same empty lists every render", () => {
    H.data = null;
    const { result, rerender } = renderHook(() => useModels());
    const first = result.current;
    rerender();
    expect(result.current.models).toBe(first.models);
    expect(result.current.usable).toBe(first.usable);
    expect(first.models).toEqual([]);
  });

  it("a loaded catalog: the same usable list until the catalog changes", () => {
    const payload = {
      models: [
        { provider: "ollama", model: "a", available: true },
        { provider: "ollama", model: "b", available: false },
      ],
    };
    H.data = payload;
    const { result, rerender } = renderHook(() => useModels());
    const first = result.current;
    rerender();
    expect(result.current.usable).toBe(first.usable);
    expect(first.usable.map((m) => m.model)).toEqual(["a"]);
    H.data = { models: [...payload.models] };
    rerender();
    expect(result.current.usable).not.toBe(first.usable);
  });
});
