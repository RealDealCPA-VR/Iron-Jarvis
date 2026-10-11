/**
 * v1.332.0 (review): a local-only chat's "summary sheet" batch.
 *
 * The card POSTs /documents/batch, which runs one model call per document.
 * Before this it sent neither `local_only` nor the chat's `thread_id`, so the
 * daemon could not tell the batch belonged to a local-only chat and ran it on
 * the default model wherever that lives. Pinned here:
 *  - the card sends `local_only` and the open saved chat's `thread_id`, read at
 *    the press;
 *  - CONTROL: a chat that is not local only sends `local_only: false`;
 *  - the daemon's 409 sentence is shown where the card shows errors;
 *  - the chat page hands the card its local-only state and the saved chat id.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
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
    posts: [] as { path: string; body: Record<string, unknown> }[],
    reply: null as unknown,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  post: async (path: string, body: Record<string, unknown>) => {
    H.posts.push({ path, body });
    if (H.reply instanceof Error) throw H.reply;
    return H.reply ?? { output: "", report: {}, created_paths: [] };
  },
}));

import { BatchSuggestCard } from "@/components/chat/BatchSuggestCard";

const PREVIEW = {
  folder: "C:/clients/acme",
  count: 6,
  estimate_calls: 7,
  cap: 25,
  truncated: false,
  files: [],
  skipped: [],
};

afterEach(() => {
  cleanup();
  H.posts.length = 0;
  H.reply = null;
});

function press(props: Record<string, unknown>) {
  render(<BatchSuggestCard preview={PREVIEW} events={[]} {...props} />);
  fireEvent.click(screen.getByRole("button", { name: /Make the summary sheet/ }));
}

describe("the summary-sheet batch in a local-only chat", () => {
  it("sends local_only and the saved chat's id, read at the press", async () => {
    let tid = "t-old";
    render(
      <BatchSuggestCard preview={PREVIEW} events={[]} localOnly getThreadId={() => tid} />,
    );
    tid = "t-saved";
    fireEvent.click(screen.getByRole("button", { name: /Make the summary sheet/ }));
    await waitFor(() => expect(H.posts).toHaveLength(1));
    expect(H.posts[0].path).toBe("/documents/batch");
    expect(H.posts[0].body.local_only).toBe(true);
    expect(H.posts[0].body.thread_id).toBe("t-saved");
  });

  it("CONTROL: a chat that is not local only says so", async () => {
    press({});
    await waitFor(() => expect(H.posts).toHaveLength(1));
    expect(H.posts[0].body.local_only).toBe(false);
    expect(H.posts[0].body.thread_id).toBe("");
  });

  it("shows the daemon's local-only sentence where the card shows errors", async () => {
    const sentence =
      "This chat is local only, so nothing was sent to Claude. Pick a local model, or allow Claude for this one message.";
    H.reply = new H.FakeApiError(sentence, 409);
    press({ localOnly: true });
    expect(await screen.findByText(sentence)).toBeTruthy();
  });
});

describe("the chat page wires the card", () => {
  const page = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
  const at = page.indexOf("<BatchSuggestCard");
  const tag = page.slice(at, page.indexOf("/>", page.indexOf("onDismiss=", at)));

  it("passes the chat's local-only state and the open saved chat's id", () => {
    expect(at).toBeGreaterThan(0);
    expect(tag).toContain("localOnly={localOnly}");
    expect(tag).toContain('getThreadId={() => saveTargetRef.current.id ?? ""}');
  });
});
