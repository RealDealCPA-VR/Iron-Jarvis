/**
 * v1.443.0 — Antigravity on the Connections page (the user: "The login is not
 * being recognized in the connections"). Its row reads the Code view's own
 * tool list (GET /build/app/harnesses): signed in, not signed in (with the
 * Sign in press, a Build terminal running agy), not on this PC, or not
 * checked yet; "Check again" and the card's "Look again" ask for a fresh read.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  answers: [] as unknown[],
  fail: false,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    get: vi.fn(async (path: string) => {
      H.gets.push(path);
      if (H.fail) throw new ApiError("Not Found", 404);
      return H.answers.length > 1 ? H.answers.shift() : H.answers[0];
    }),
    post: vi.fn(async (path: string, body: unknown) => {
      H.posts.push({ path, body });
      return { id: "pane-7" };
    }),
  };
});

import { AntigravityRow, AGY_SIGNED_IN, AGY_NOT_SIGNED_IN, antigravityState } from "@/components/connections/AntigravityRow";
import { decodeCodingTools } from "@/lib/buildApp";

function agy(extra: Record<string, unknown> = {}) {
  return {
    id: "antigravity",
    label: "Antigravity",
    installed: true,
    own_sign_in: true,
    only_when_installed: true,
    signed_in: null,
    models: [{ id: "", label: "Antigravity's default" }],
    modes: [],
    efforts: [],
    accounts: [],
    commands: [],
    ...extra,
  };
}

beforeEach(() => {
  H.gets.length = 0;
  H.posts.length = 0;
  H.answers.length = 0;
  H.fail = false;
});

describe("the daemon's sign-in hint is decoded only for Connections", () => {
  it("keeps signin_finished and never turns it into signed out", () => {
    const [t] = decodeCodingTools({ harnesses: [agy({ signin_finished: false })] });
    expect(t.signinFinished).toBe(false);
    expect(t.signedIn).toBeNull();
    const [u] = decodeCodingTools({ harnesses: [agy()] });
    expect("signinFinished" in u).toBe(false);
    expect(antigravityState([t])).toBe("signed_out");
    expect(antigravityState([u])).toBe("unknown");
    expect(antigravityState([])).toBe("missing");
    expect(antigravityState(null)).toBeNull();
  });
});

describe("the Antigravity row", () => {
  it("says signed in when Antigravity listed its models", async () => {
    H.answers.push({ harnesses: [agy({ signed_in: true, signin_finished: true })] });
    render(<AntigravityRow />);
    expect((await screen.findByTestId("agy-badge")).textContent).toContain(AGY_SIGNED_IN);
    expect(screen.queryByText("Sign in")).toBeNull();
    expect(screen.getByText(/used in Build › Code/)).toBeTruthy();
  });

  it("offers Sign in (a Build terminal running agy, no account) before its sign-in finished", async () => {
    H.answers.push({ harnesses: [agy({ signin_finished: false, models_note: "Antigravity isn't signed in yet." })] });
    render(<AntigravityRow />);
    expect((await screen.findByTestId("agy-badge")).textContent).toContain(AGY_NOT_SIGNED_IN);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(H.posts).toEqual([{ path: "/terminals/launch", body: { cli: "agy" } }]));
    const link = await screen.findByRole("link", { name: "Open the Build terminal" });
    expect(link.getAttribute("href")).toBe("/terminals?focus=pane-7");
  });

  it("links Google's page when Antigravity is not on this PC", async () => {
    H.answers.push({ harnesses: [] });
    render(<AntigravityRow />);
    const link = await screen.findByRole("link", { name: "Get Antigravity" });
    expect(link.getAttribute("href")).toContain("antigravity");
  });

  it("looks again while its list is being read, then says what it found", async () => {
    H.answers.push(
      { harnesses: [agy({ models_reading: true, signin_finished: true })] },
      { harnesses: [agy({ signed_in: true, signin_finished: true })] },
    );
    render(<AntigravityRow />);
    expect((await screen.findByTestId("agy-badge")).textContent).toContain("Checking");
    await waitFor(() => expect(screen.getByTestId("agy-badge").textContent).toContain(AGY_SIGNED_IN), {
      timeout: 6000,
    });
    expect(H.gets).toEqual(["/build/app/harnesses", "/build/app/harnesses"]);
  });

  it("Check again and the card's Look again ask for a fresh read", async () => {
    H.answers.push({ harnesses: [agy({ signin_finished: true })] });
    const { rerender } = render(<AntigravityRow lookAgain={0} />);
    fireEvent.click(await screen.findByTestId("agy-check-again"));
    await waitFor(() => expect(H.gets).toContain("/build/app/harnesses?recheck=antigravity"));
    const before = H.gets.length;
    await act(async () => rerender(<AntigravityRow lookAgain={1} />));
    await waitFor(() => expect(H.gets.length).toBe(before + 1));
    expect(H.gets[H.gets.length - 1]).toBe("/build/app/harnesses?recheck=antigravity");
  });

  it("says nothing on an older daemon or a list that is not the Code view's", async () => {
    H.fail = true;
    const { container, unmount } = render(<AntigravityRow />);
    await waitFor(() => expect(H.gets.length).toBe(1));
    expect(container.textContent).toBe("");
    unmount();
    H.fail = false;
    H.answers.push({});
    const second = render(<AntigravityRow />);
    await waitFor(() => expect(H.gets.length).toBe(2));
    expect(second.container.textContent).toBe("");
  });
});
