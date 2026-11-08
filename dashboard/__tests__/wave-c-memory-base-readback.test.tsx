/**
 * Wave C (readiness plan 0h.5, item 28): a memory base added on the Memory
 * page is read back by the daemon, and the page says the daemon's OWN words
 * — "Added and answering.", or why not (in the warn tone) — the same words
 * chat's memory_base_add gives (tests/test_wave_c_memory_base_readback.py).
 * An older daemon that sends no words gets the plain "added" line.
 */
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: {} as Record<string, unknown>,
    posts: [] as { path: string; body: unknown }[],
    // The daemon's next answer: a row, or an Error (a 4xx) the mock throws.
    answer: {} as unknown,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.gets[path] ?? H.gets[path.split("?")[0]];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    // An Error planted as the answer is the daemon's refusal (a 4xx).
    if (H.answer instanceof Error) throw H.answer;
    return H.answer;
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/memory",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children?: ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { LongTerm } from "@/components/memory/LongTerm";

beforeEach(() => {
  H.gets = {
    "/ltm/sources": { sources: [], active: ["brain"], bases: [] },
    "/ltm/browse": { entries: [] },
  };
  H.posts = [];
});
afterEach(() => cleanup());

async function addFolder(name: string) {
  render(<LongTerm />);
  const box = await screen.findByPlaceholderText("my-notes");
  fireEvent.change(box, { target: { value: name } });
  // The folder box's placeholder is an example path (String.raw in the page).
  const folder = screen.getByPlaceholderText(String.raw`C:\Users\me\notes`);
  fireEvent.change(folder, { target: { value: "D:\\vault" } });
  fireEvent.click(screen.getByRole("button", { name: /add source/i }));
  await waitFor(() => expect(H.posts.some((p) => p.path === "/ltm/sources")).toBe(true));
}

describe("the Memory page reads a new base back (Wave C, item 28)", () => {
  it("says the daemon's words when the base answers", async () => {
    H.answer = { name: "vault", kind: "markdown", answering: true, words: "Added and answering." };
    await addFolder("vault");
    const note = await screen.findByText("vault: Added and answering.");
    expect(note).toBeInTheDocument();
    expect(screen.queryByTestId("ltm-added-not-answering")).toBeNull();
  });

  it("says why not, in the warn tone, when the base does not answer", async () => {
    H.answer = {
      name: "gone", kind: "markdown", answering: false,
      words: "Added, but it is not answering: folder not found: D:\\vault.",
    };
    await addFolder("gone");
    const warn = await screen.findByTestId("ltm-added-not-answering");
    expect(warn.textContent).toContain("gone: Added, but it is not answering: folder not found: D:\\vault.");
  });

  it("an older daemon that sends no words gets the plain added line", async () => {
    H.answer = { name: "old", kind: "markdown" };
    await addFolder("old");
    expect(await screen.findByText('Memory base "old" added.')).toBeInTheDocument();
  });

  it("a refused add (4xx) after a not-answering one shows the refusal alone", async () => {
    // Wave C review: the read-back's warn note must not outlive a refusal —
    // the next attempt clears it, and a 409 ends in the error note with
    // nothing "added" on screen.
    H.answer = { name: "gone", kind: "markdown", answering: false, words: "Added, but it is not answering: x." };
    await addFolder("gone");
    await screen.findByTestId("ltm-added-not-answering");
    H.posts = [];
    H.answer = new H.FakeApiError("source 'dup' already exists", 409);
    fireEvent.change(screen.getByPlaceholderText("my-notes"), { target: { value: "dup" } });
    fireEvent.change(screen.getByPlaceholderText(String.raw`C:\Users\me\notes`), { target: { value: "D:\\v2" } });
    fireEvent.click(screen.getByRole("button", { name: /add source/i }));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/ltm/sources")).toBe(true));
    expect(await screen.findByText("source 'dup' already exists")).toBeInTheDocument();
    expect(screen.queryByTestId("ltm-added-not-answering")).toBeNull();
    expect(screen.queryByText(/^Added|: Added/)).toBeNull();
  });
});
