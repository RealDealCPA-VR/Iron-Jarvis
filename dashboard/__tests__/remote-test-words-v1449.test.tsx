/**
 * A remote agent's Test reads in plain words, the probe's own text folded
 * (v1.449.0, readiness item 19).
 *
 * THE FAILURE: the card printed the daemon's probe detail raw — "probe
 * failed", "request failed: ConnectError: [Errno 11001] getaddrinfo failed"
 * — the one Test button in the app that did not go through the connection
 * Test's words. The daemon now answers `title` + `fix` beside the unchanged
 * `detail` (worded through failure_words.friendly_error) and the masked
 * `technical` text; the card shows "title. fix" and folds the technical text
 * under Details, exactly as the Connections page does (lib/connectionTestWords).
 * An older daemon (no title) keeps its raw detail.
 */
import { render, screen, fireEvent, cleanup, within, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ answer: {} as Record<string, unknown>, posts: [] as string[] }));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 500;
  },
  del: vi.fn(async () => ({})),
  post: vi.fn(async (path: string) => {
    H.posts.push(path);
    return H.answer;
  }),
  patch: vi.fn(async () => ({})),
  put: vi.fn(async () => ({})),
  get: vi.fn(async () => ({})),
  API_BASE: "",
  ijToken: () => "",
}));

import { SetupCardHarness } from "./helpers/setupCardHarness";
import { DETAILS_WORDS } from "@/lib/failureActions";

const REMOTE = {
  name: "my-hermes",
  base_url: "http://192.168.1.20:8080",
  kind: "http-task",
  model: "",
  enabled: true,
  timeout_s: 120,
  has_credential: true,
};

function renderCard() {
  return render(
    <SetupCardHarness
      builtin={[]}
      dynamic={[]}
      remotes={[REMOTE]}
      models={[]}
      onAgentsChanged={() => {}}
      onRemotesChanged={() => {}}
    />,
  );
}

function expand() {
  const toggle = screen.getByRole("button", { name: /set up agents/i });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
}

async function pressTest() {
  renderCard();
  expand();
  fireEvent.click(await screen.findByRole("button", { name: /^test$/i }));
  return within(await screen.findByTestId("remote-test-my-hermes"));
}

afterEach(() => {
  cleanup();
  H.posts.length = 0;
  localStorage.clear();
});

describe("a remote agent's Test", () => {
  it("shows the daemon's title and fix, and folds the technical text under Details", async () => {
    H.answer = {
      ok: false,
      detail: "request failed: ConnectError: [Errno 11001] getaddrinfo failed",
      title: "My-hermes isn't connected right now",
      fix: "Check its address, and that the other computer can be reached from this one, then press Test again.",
      technical: "request failed: ConnectError: [Errno 11001] getaddrinfo failed",
    };
    const box = await pressTest();
    expect(H.posts).toEqual(["/agents/remote/my-hermes/test"]);
    await waitFor(() =>
      expect(box.getByText(/My-hermes isn't connected right now\. Check its address/)).toBeTruthy(),
    );
    const fold = box.getByTestId("remote-test-technical");
    expect(fold.tagName).toBe("DETAILS");
    expect(within(fold).getByText(DETAILS_WORDS)).toBeTruthy();
    expect(within(fold).getByText(/getaddrinfo failed/)).toBeTruthy();
    // The raw probe text is never the headline.
    const headline = screen.getByTestId("remote-test-my-hermes").querySelector("p");
    expect(headline?.textContent ?? "").not.toMatch(/getaddrinfo|request failed/);
  });

  it("a reply reads as one plain line with nothing to fold", async () => {
    H.answer = { ok: true, detail: "my-hermes replied", title: "My-hermes replied", fix: "", technical: "" };
    const box = await pressTest();
    await waitFor(() => expect(box.getByText("My-hermes replied.")).toBeTruthy());
    expect(box.queryByTestId("remote-test-technical")).toBeNull();
  });

  it("an older daemon's raw detail is shown exactly as before", async () => {
    H.answer = { ok: false, detail: "probe failed" };
    const box = await pressTest();
    await waitFor(() => expect(box.getByText("probe failed")).toBeTruthy());
    expect(box.queryByTestId("remote-test-technical")).toBeNull();
  });
});
