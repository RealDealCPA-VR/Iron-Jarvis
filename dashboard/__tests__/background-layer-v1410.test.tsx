/**
 * v1.410.0 — the animated background behind the app, and its picker.
 *
 * The layer: sealed off (sandbox="allow-scripts" and never allow-same-origin,
 * no pointer events, no focus, hidden from screen readers, no token in its
 * address), invisible until the page says it drew, nothing at all when the
 * file is missing, an out-of-date file fetched again once, and told to stop
 * when the window is hidden and to hold still when asked.
 * The picker: a pick downloads that one background (a saved one is used as
 * it is), a failed download says so and changes nothing, Delete deletes.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({
  list: [] as { id: string; name: string; size: number; state: string; bytes: number }[],
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "http://127.0.0.1:8787",
  get: (...a: unknown[]) => H.get(...a),
  post: (...a: unknown[]) => H.post(...a),
  del: (...a: unknown[]) => H.del(...a),
}));

import { BackgroundLayer, BACKGROUND_SANDBOX, backgroundMessages } from "@/components/BackgroundLayer";
import { BackgroundPicker, DownloadedBackgrounds } from "@/components/BackgroundPicker";
import { BACKGROUNDS } from "@/lib/backgroundCatalog";
import { makePalette, PALETTES_KEY, SCRIM_ALPHA, type PaletteBackground } from "@/lib/themePalette";
import { applyTheme } from "@/lib/theme";

function row(id: string, state: string) {
  const b = BACKGROUNDS.find((x) => x.id === id)!;
  return { id, name: b.name, size: b.size, state, bytes: state === "absent" ? 0 : b.size };
}

function useTheme(background: PaletteBackground | null) {
  const p = makePalette({ name: "T", accent: "#22d3ee", surface: "cool", mode: "dark", background });
  localStorage.setItem(PALETTES_KEY, JSON.stringify([p]));
  act(() => {
    applyTheme(p.id);
  });
  return p;
}

beforeEach(() => {
  H.list = BACKGROUNDS.map((b) => row(b.id, "absent"));
  H.get.mockImplementation(async (path: string) => {
    if (path === "/backgrounds")
      return { backgrounds: H.list, total_bytes: H.list.reduce((n, r) => n + r.bytes, 0) };
    throw new Error("unexpected " + path);
  });
  H.post.mockImplementation(async (path: string) => {
    const id = path.split("/")[2];
    H.list = H.list.map((r) => (r.id === id ? row(id, "ready") : r));
    return row(id, "ready");
  });
  H.del.mockImplementation(async (path: string) => {
    const id = path.split("/")[2];
    const freed = H.list.find((r) => r.id === id)?.bytes ?? 0;
    H.list = H.list.map((r) => (r.id === id ? row(id, "absent") : r));
    return { id, freed_bytes: freed };
  });
});

afterEach(() => {
  cleanup();
  const html = document.documentElement;
  delete html.dataset.theme;
  delete html.dataset.scheme;
  html.removeAttribute("style");
  localStorage.clear();
  H.get.mockReset();
  H.post.mockReset();
  H.del.mockReset();
});

describe("A. the layer is sealed off", () => {
  it("draws the saved file in a sandboxed, inert frame with no token in its address", async () => {
    H.list = H.list.map((r) => (r.id === "aurora" ? row("aurora", "ready") : r));
    localStorage.setItem("ij_token", "SECRET-TOKEN");
    useTheme({ id: "aurora", motion: "animated" });
    render(<BackgroundLayer />);
    const frame = (await screen.findByTestId("app-background-frame")) as HTMLIFrameElement;
    expect(BACKGROUND_SANDBOX).toBe("allow-scripts");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toMatch(/same-origin/);
    expect(frame.getAttribute("allow")).toBeNull();
    expect(frame.getAttribute("aria-hidden")).toBe("true");
    expect(frame.getAttribute("tabindex")).toBe("-1");
    expect(frame.style.pointerEvents).toBe("none");
    const wrap = screen.getByTestId("app-background");
    expect(wrap.getAttribute("aria-hidden")).toBe("true");
    expect(wrap.className).toMatch(/pointer-events-none/);
    expect(wrap.className).toMatch(/fixed/);
    const src = frame.getAttribute("src")!;
    expect(src).toMatch(/^http:\/\/127\.0\.0\.1:8787\/backgrounds\/aurora\/view#accent=[0-9a-f]{6}&second=[0-9a-f]{6}&bg=[0-9a-f]{6}&mode=dark$/);
    expect(src).not.toMatch(/SECRET|token/i);
    localStorage.removeItem("ij_token");
  });

  it("stays invisible (no scrim) until THAT frame says it drew", async () => {
    H.list = H.list.map((r) => (r.id === "waves" ? row("waves", "ready") : r));
    useTheme({ id: "waves", motion: "animated" });
    render(<BackgroundLayer />);
    const frame = (await screen.findByTestId("app-background-frame")) as HTMLIFrameElement;
    expect(frame.style.opacity).toBe("0");
    expect(screen.queryByTestId("app-background-scrim")).toBeNull();
    // A message from some other window is ignored.
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "ij-bg-ready" }, source: window }));
    });
    expect(screen.queryByTestId("app-background-scrim")).toBeNull();
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data: { type: "ij-bg-ready", id: "waves" }, source: frame.contentWindow }),
      );
    });
    const scrim = await screen.findByTestId("app-background-scrim");
    expect(scrim.style.background).toContain(`/ ${SCRIM_ALPHA.dark})`);
    expect(Number(frame.style.opacity)).toBeCloseTo(0.6);
  });
});

describe("B. a missing file falls back to the plain theme", () => {
  it("renders nothing when the file is not on this PC (deleted or never downloaded)", async () => {
    useTheme({ id: "reactor", motion: "animated" });
    render(<BackgroundLayer />);
    await waitFor(() => expect(H.get).toHaveBeenCalledWith("/backgrounds"));
    expect(screen.queryByTestId("app-background")).toBeNull();
    expect(H.post).not.toHaveBeenCalled(); // a deleted file is never fetched behind the user's back
  });

  it("renders nothing when the daemon cannot answer", async () => {
    H.get.mockRejectedValue(new Error("offline"));
    useTheme({ id: "aurora", motion: "animated" });
    render(<BackgroundLayer />);
    await waitFor(() => expect(H.get).toHaveBeenCalled());
    expect(screen.queryByTestId("app-background")).toBeNull();
  });

  it("renders nothing for a built-in theme or a palette with no background", async () => {
    act(() => {
      applyTheme("mark2");
    });
    render(<BackgroundLayer />);
    expect(screen.queryByTestId("app-background")).toBeNull();
    cleanup();
    useTheme(null);
    render(<BackgroundLayer />);
    expect(screen.queryByTestId("app-background")).toBeNull();
    expect(H.get).not.toHaveBeenCalled();
  });

  it("an out-of-date file after an update is fetched again, once, then shown", async () => {
    H.list = H.list.map((r) => (r.id === "particles" ? row("particles", "outdated") : r));
    useTheme({ id: "particles", motion: "animated" });
    render(<BackgroundLayer />);
    await screen.findByTestId("app-background-frame");
    expect(H.post).toHaveBeenCalledTimes(1);
    expect(H.post.mock.calls[0][0]).toBe("/backgrounds/particles/download");
  });

  it("Delete elsewhere in this window takes it away at once", async () => {
    H.list = H.list.map((r) => (r.id === "aurora" ? row("aurora", "ready") : r));
    useTheme({ id: "aurora", motion: "animated" });
    render(
      <>
        <BackgroundLayer />
        <DownloadedBackgrounds />
      </>,
    );
    await screen.findByTestId("app-background-frame");
    const item = await screen.findByTestId("downloaded-background-aurora");
    fireEvent.click(item.querySelector("button")!);
    fireEvent.click(await screen.findByText(/confirm|delete\?|yes/i));
    await waitFor(() => expect(screen.queryByTestId("app-background")).toBeNull());
    expect(H.del).toHaveBeenCalledWith("/backgrounds/aurora");
  });
});

describe("C. light on the PC", () => {
  // v1.410.2: the first cut spied on the frame's contentWindow.postMessage,
  // and on CI's runner the frame's window was replaced after the spy went on
  // (jsdom navigating it), so the spy heard nothing ("expected [] to deep
  // equally contain"). What the layer decides is read off its own attributes,
  // and the messages those decisions become are pinned as a pure function.
  it("says still and running as the page is told", () => {
    expect(backgroundMessages(true, false)).toEqual([
      { type: "ij-bg", still: true },
      { type: "ij-bg", run: false },
    ]);
    expect(backgroundMessages(false, true)).toEqual([
      { type: "ij-bg", still: false },
      { type: "ij-bg", run: true },
    ]);
  });

  it("stops while the window is hidden, and holds still when chosen", async () => {
    H.list = H.list.map((r) => (r.id === "aurora" ? row("aurora", "ready") : r));
    useTheme({ id: "aurora", motion: "still" });
    render(<BackgroundLayer />);
    await screen.findByTestId("app-background-frame");
    const layer = () => screen.getByTestId("app-background");
    expect(layer().dataset.motion).toBe("still");
    expect(layer().dataset.running).toBe("yes");
    const vis = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(layer().dataset.running).toBe("no"));
    vis.mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(layer().dataset.running).toBe("yes"));
    vis.mockRestore();
  });

  it("holds still on battery power", async () => {
    let charging = false;
    const battery = { get charging() { return charging; }, addEventListener: vi.fn(), removeEventListener: vi.fn() };
    Object.defineProperty(navigator, "getBattery", { value: () => Promise.resolve(battery), configurable: true });
    H.list = H.list.map((r) => (r.id === "aurora" ? row("aurora", "ready") : r));
    useTheme({ id: "aurora", motion: "animated" });
    render(<BackgroundLayer />);
    await screen.findByTestId("app-background-frame");
    await waitFor(() => expect(screen.getByTestId("app-background").dataset.motion).toBe("still"));
    charging = true;
    delete (navigator as unknown as Record<string, unknown>).getBattery;
  });
});

describe("C2. the app's own drifting glow stops while a background shows", () => {
  it("marks <html data-background=on> once drawn, and clears it when gone", async () => {
    const { readFileSync } = await import("node:fs");
    const css = readFileSync("app/globals.css", "utf8").replace(/\r\n/g, "\n");
    expect(css).toMatch(/:root\[data-background="on"\] body::before \{[^}]*display: none;/);
    H.list = H.list.map((r) => (r.id === "grid-beams" ? row("grid-beams", "ready") : r));
    useTheme({ id: "grid-beams", motion: "animated" });
    const view = render(<BackgroundLayer />);
    const frame = (await screen.findByTestId("app-background-frame")) as HTMLIFrameElement;
    expect(document.documentElement.dataset.background).toBeUndefined();
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "ij-bg-ready" }, source: frame.contentWindow }));
    });
    await waitFor(() => expect(document.documentElement.dataset.background).toBe("on"));
    view.unmount();
    expect(document.documentElement.dataset.background).toBeUndefined();
  });
});

describe("D. the picker downloads only what is picked", () => {
  it("a pick downloads that one file, then chooses it", async () => {
    const onChange = vi.fn();
    render(<BackgroundPicker value={null} onChange={onChange} />);
    await waitFor(() => expect(H.get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("background-choice-grid-beams"));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ id: "grid-beams", motion: "animated" }));
    expect(H.post).toHaveBeenCalledTimes(1);
    expect(H.post.mock.calls[0][0]).toBe("/backgrounds/grid-beams/download");
  });

  it("a background already on this PC is used without a download", async () => {
    H.list = H.list.map((r) => (r.id === "waves" ? row("waves", "ready") : r));
    const onChange = vi.fn();
    render(<BackgroundPicker value={{ id: "aurora", motion: "still" }} onChange={onChange} />);
    await waitFor(() => expect(screen.getByTestId("background-choice-waves").title).toMatch(/On this PC/));
    fireEvent.click(screen.getByTestId("background-choice-waves"));
    expect(onChange).toHaveBeenCalledWith({ id: "waves", motion: "still" });
    expect(H.post).not.toHaveBeenCalled();
  });

  it("a refused download says why and changes nothing", async () => {
    H.post.mockRejectedValue(
      new Error("The downloaded background did not match the one this version of Iron Jarvis expects, so it was not saved."),
    );
    const onChange = vi.fn();
    render(<BackgroundPicker value={null} onChange={onChange} />);
    await waitFor(() => expect(H.get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("background-choice-reactor"));
    const err = await screen.findByTestId("background-error");
    expect(err.textContent).toMatch(/Reactor could not be downloaded: The downloaded background did not match/);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("offers Animated / Still once one is chosen, and says when it holds still", async () => {
    const onChange = vi.fn();
    render(<BackgroundPicker value={{ id: "aurora", motion: "animated" }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: "Still" }));
    expect(onChange).toHaveBeenCalledWith({ id: "aurora", motion: "still" });
    expect(screen.getByTestId("background-picker").textContent).toMatch(/battery power/);
  });

  it("lists what is on this PC with sizes; nothing when none is", async () => {
    const { container } = render(<DownloadedBackgrounds />);
    await waitFor(() => expect(H.get).toHaveBeenCalled());
    expect(container.textContent).toBe("");
    cleanup();
    H.list = H.list.map((r) => (r.id === "waves" ? row("waves", "ready") : r));
    render(<DownloadedBackgrounds />);
    const list = await screen.findByTestId("downloaded-backgrounds");
    expect(list.textContent).toMatch(/Waves/);
    expect(list.textContent).toMatch(/7 KB/);
  });
});
