/**
 * v1.449.0 — readiness Wave C item 25: the chat's secure card tests a
 * model provider's key before storing it. A key that fails its check is
 * NOT saved: the card says the daemon's words plus "Nothing was saved",
 * keeps the typed value for a fix, and never settles as "saved".
 *
 * The real CredentialCard; only the transport is mocked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  posts: [] as { path: string; body: unknown }[],
  answer: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "",
  ijToken: () => "",
  get: vi.fn(async () => ({})),
  post: vi.fn(async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    return H.answer;
  }),
  put: vi.fn(async () => ({})),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { CredentialCard } from "@/components/chat/ConfigCards";
import type { SecretRequestCard } from "@/lib/configCards";

const CARD: SecretRequestCard = {
  kind: "secret",
  name: "connection.openai",
  label: "API key for a model provider: openai",
  request_id: "seq_1",
};
const KEY = "sk-card-test-0123456789";
const WORDS = "OpenAI refused the key. Check the key and paste it again. Nothing was saved, and what answers you didn't change.";

beforeEach(() => {
  H.posts.length = 0;
  H.answer = {};
});
afterEach(cleanup);

function paste(value = KEY) {
  const field = screen.getByLabelText(CARD.label) as HTMLInputElement;
  fireEvent.change(field, { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
  return field;
}

describe("a key that fails its check", () => {
  it("is said in the daemon's words, keeps the value, and never settles as saved", async () => {
    H.answer = { status: "not_saved", saved: false, words: WORDS };
    const onSettle = vi.fn();
    render(<CredentialCard card={CARD} onSettle={onSettle} />);
    const field = paste();
    const note = await screen.findByTestId("credential-card-not-saved");
    expect(note.textContent).toBe(WORDS);
    expect(note.getAttribute("role")).toBe("status");
    expect(field.value).toBe(KEY); // kept for a fix
    expect(onSettle).not.toHaveBeenCalled();
    expect(screen.queryByTestId("credential-card-saved")).toBeNull();
    expect(H.posts).toEqual([{ path: "/config/secret", body: { name: CARD.name, value: KEY } }]);
  });

  it("words that would echo the key are replaced by a plain sentence", async () => {
    H.answer = { status: "not_saved", saved: false, words: `refused ${KEY}` };
    render(<CredentialCard card={CARD} onSettle={vi.fn()} />);
    paste();
    const note = await screen.findByTestId("credential-card-not-saved");
    expect(note.textContent).toBe("That key didn't pass its check. Nothing was saved.");
    expect(document.body.textContent).not.toContain(KEY);
  });

  it("a second, passing paste settles the card as saved and clears the note", async () => {
    H.answer = { status: "not_saved", saved: false, words: WORDS };
    const onSettle = vi.fn();
    render(<CredentialCard card={CARD} onSettle={onSettle} />);
    paste("sk-wrong");
    await screen.findByTestId("credential-card-not-saved");
    H.answer = { status: "stored", action_id: "tool_9" };
    paste();
    await waitFor(() => expect(onSettle).toHaveBeenCalledWith(expect.objectContaining({ saved: "stored" })));
    expect(screen.queryByTestId("credential-card-not-saved")).toBeNull();
  });
});

describe("CONTROL — a stored key settles exactly as before", () => {
  it("'stored' clears the field and settles the card", async () => {
    H.answer = { status: "stored", action_id: "tool_3" };
    const onSettle = vi.fn();
    render(<CredentialCard card={CARD} onSettle={onSettle} />);
    const field = paste();
    await waitFor(() => expect(onSettle).toHaveBeenCalledTimes(1));
    expect(onSettle.mock.calls[0][0]).toMatchObject({ saved: "stored", action_id: "tool_3" });
    expect(field.value).toBe("");
  });
});
