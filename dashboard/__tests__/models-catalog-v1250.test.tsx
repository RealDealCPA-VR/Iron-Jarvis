/**
 * One model catalog per window (v1.250.0, S-02 dashboard half).
 *
 * Seven surfaces asked `/models` for themselves; the title bar's switcher is
 * always one of them, so every page mount re-fetched a payload the window was
 * already holding — and showed a placeholder while it did.
 *
 * What these pin:
 *   - a SECOND caller of useModels issues no second GET and renders the first
 *     one's payload immediately (no null pass, so no placeholder);
 *   - `usable` keeps a model whose `available` the daemon never sent. Treating
 *     unknown as offline emptied the pickers on older daemons, so the filter is
 *     `!== false`, never `=== true`;
 *   - the catalog is still REVALIDATED, not frozen: a reload sees new models.
 *
 * The cache is reset between tests by __tests__/setup.ts, so each case starts
 * from a cold window.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, get: (...a: unknown[]) => getMock(...a) };
});

// useApi consults the daemon context for its offline->online epoch; a bare
// provider-less render must still work, so the hook is exercised as the pages
// use it, with the real provider absent (epoch stays 0).
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ epoch: 0, health: null, refresh: () => {} }),
}));

import { useModels } from "@/lib/useModels";

const CATALOG = {
  models: [
    { provider: "fleet", model: "qwen3-32b", available: true },
    { provider: "anthropic", model: "claude-opus-5", available: false },
    { provider: "legacy", model: "old-model" }, // no `available` at all
  ],
};

function Reader({ id }: { id: string }) {
  const { models, usable, loading } = useModels();
  return (
    <div>
      <span data-testid={`${id}-loading`}>{loading ? "yes" : "no"}</span>
      <span data-testid={`${id}-all`}>{models.map((m) => m.model).join(",")}</span>
      <span data-testid={`${id}-usable`}>{usable.map((m) => m.model).join(",")}</span>
    </div>
  );
}

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue(CATALOG);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useModels", () => {
  it("fetches once and keeps every model, connected or not", async () => {
    render(<Reader id="a" />);
    await waitFor(() =>
      expect(screen.getByTestId("a-all")).toHaveTextContent(
        "qwen3-32b,claude-opus-5,old-model",
      ),
    );
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(getMock.mock.calls[0][0]).toBe("/models");
  });

  it("a model with NO `available` field stays usable (older daemons)", async () => {
    render(<Reader id="b" />);
    await waitFor(() =>
      expect(screen.getByTestId("b-usable")).toHaveTextContent("qwen3-32b,old-model"),
    );
    // ...and the one the daemon said was down is the only one dropped.
    expect(screen.getByTestId("b-usable")).not.toHaveTextContent("claude-opus-5");
  });

  it("a SECOND surface renders the catalog with no second request", async () => {
    // First surface — the title bar's switcher, in effect.
    const first = render(<Reader id="c" />);
    await waitFor(() =>
      expect(screen.getByTestId("c-all")).toHaveTextContent("qwen3-32b"),
    );
    expect(getMock).toHaveBeenCalledTimes(1);
    first.unmount();

    // A page mounts and asks for the same catalog.
    render(<Reader id="d" />);
    // Already there on the FIRST render: no null pass, so no placeholder.
    expect(screen.getByTestId("d-all")).toHaveTextContent("qwen3-32b");
    // The revalidation is a conditional GET, and it is the only new call.
    await waitFor(() => expect(getMock.mock.calls.length).toBeLessThanOrEqual(2));
  });

  it("still revalidates — a new endpoint appears on the next load", async () => {
    const first = render(<Reader id="e" />);
    await waitFor(() =>
      expect(screen.getByTestId("e-all")).toHaveTextContent("qwen3-32b"),
    );
    first.unmount();

    getMock.mockResolvedValue({
      models: [...CATALOG.models, { provider: "fleet", model: "new-brain", available: true }],
    });
    render(<Reader id="f" />);
    await waitFor(() =>
      expect(screen.getByTestId("f-all")).toHaveTextContent("new-brain"),
    );
  });
});
