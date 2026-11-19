/**
 * v1.540.0 (fourth review C-LX-2): the Updates card says how THIS Linux copy
 * updates, from the `install` field a Linux desktop sends (appimage, deb or
 * folder). An unpacked folder's line replaces "Auto-update isn't available in
 * this build." A Windows desktop sends no such field, so its card is the same
 * as before (pinned below).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "http://api.test",
  ijToken: () => null,
  get: () => Promise.resolve({}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({
    data: { available: false, reason: "not a source checkout (installed package)" },
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const tag = (t: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!["initial", "animate", "exit", "transition", "variants", "layout", "whileHover", "whileTap"].includes(k)) {
        rest[k] = v;
      }
    }
    return createElement(t, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, { get: (_t, k) => tag(String(k)) });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    LazyMotion: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    domAnimation: {},
  };
});

import UpdatesPage from "@/components/settings/pages/UpdatesPage";

const APPIMAGE = "This copy updates itself: a new version installs when you close the app or press Restart & install.";
const DEB = "Updates arrive by themselves; installing one may ask for your password.";
const FOLDER = "This copy cannot update itself. Download the newest version from the releases page.";
const UNSUPPORTED = "Auto-update isn't available in this build.";

function installBridge(state: Record<string, unknown>) {
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
    update: {
      getState: vi.fn(() => Promise.resolve({ current: "1.540.0", ...state })),
      check: vi.fn(() => Promise.resolve({ current: "1.540.0", ...state })),
      apply: vi.fn(() => Promise.resolve(true)),
      onState: vi.fn(() => () => {}),
    },
  };
}

async function card(state: Record<string, unknown>) {
  installBridge(state);
  render(<UpdatesPage />);
  await screen.findByText("Installed version");
  await waitFor(() => expect(screen.getByText("v1.540.0")).toBeInTheDocument());
}

afterEach(() => {
  cleanup();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

describe("a Linux copy says how it updates", () => {
  it("the single file updates itself", async () => {
    await card({ status: "up-to-date", install: "appimage" });
    expect(screen.getByTestId("update-install-line").textContent).toBe(APPIMAGE);
    expect(screen.getByText(/You.re on the latest version\./)).toBeInTheDocument();
  });

  it("the package promises only what has been tried", async () => {
    await card({ status: "up-to-date", install: "deb" });
    expect(screen.getByTestId("update-install-line").textContent).toBe(DEB);
  });

  it("an unpacked folder says it cannot, in place of the old line, and links the releases list", async () => {
    await card({ status: "unsupported", install: "folder" });
    const line = await screen.findByTestId("update-install-line");
    expect(line.textContent).toBe(FOLDER);
    expect(screen.queryByText(UNSUPPORTED)).toBeNull();
    const link = screen.getByRole("link", { name: "releases page" });
    expect(link.getAttribute("href")).toBe("https://github.com/RealDealCPA-VR/Iron-Jarvis/releases");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("an unpacked folder is not told to check for a version it cannot install", async () => {
    await card({ status: "idle", install: "folder" });
    expect(screen.getByTestId("update-install-line").textContent).toBe(FOLDER);
    expect(screen.queryByText("Click below to check for a new version.")).toBeNull();
  });
});

describe("Windows sends no field, so its card is unchanged", () => {
  it("unsupported still reads the old line, and no Linux line is drawn", async () => {
    await card({ status: "unsupported" });
    expect(screen.getByText(UNSUPPORTED)).toBeInTheDocument();
    expect(screen.queryByTestId("update-install-line")).toBeNull();
    for (const words of [APPIMAGE, DEB, FOLDER]) expect(document.body.textContent).not.toContain(words);
  });

  it("idle still asks for a check, and no Linux line is drawn", async () => {
    await card({ status: "idle" });
    expect(screen.getByText("Click below to check for a new version.")).toBeInTheDocument();
    expect(screen.queryByTestId("update-install-line")).toBeNull();
  });
});
