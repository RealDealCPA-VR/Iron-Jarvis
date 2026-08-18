/**
 * Editing a connected remote agent (v1.164.0).
 *
 * The row used to offer only Test and Delete, so one wrong character in a base
 * URL meant re-entering the whole record — including a bearer token the user
 * may no longer have.
 *
 * WHAT THESE TESTS ARE REALLY GUARDING is the token field. It cannot be
 * prefilled (the secret is stored encrypted and never returned), so the form
 * must distinguish three intents that look almost identical in a UI: replace
 * the credential, KEEP it, or remove it. Sending an empty `token` on an
 * ordinary edit would clear a working secret the user cannot retype — strictly
 * worse than starting from scratch, which is the thing this feature exists to
 * avoid.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

const calls: { path: string; body: unknown }[] = [];

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 500;
  },
  del: vi.fn(async () => ({})),
  post: vi.fn(async () => ({})),
  patch: vi.fn(async (path: string, body: unknown) => {
    calls.push({ path, body });
    return {};
  }),
  get: vi.fn(async () => ({})),
  API_BASE: "",
  ijToken: () => "",
}));

import { SetupCard } from "@/components/agents/SetupCard";

import { SetupCardHarness } from "./helpers/setupCardHarness";
const REMOTE = {
  name: "my-hermes",
  base_url: "http://192.168.1.20:8080",
  kind: "http-task",
  model: "",
  enabled: true,
  timeout_s: 120,
  has_credential: true,
};

function renderCard(over: Partial<typeof REMOTE> = {}) {
  return render(
    <SetupCardHarness
      builtin={[]}
      dynamic={[]}
      remotes={[{ ...REMOTE, ...over }]}
      models={[]}
      onAgentsChanged={() => {}}
      onRemotesChanged={() => {}}
    />,
  );
}

/** Expand the card if it is collapsed. IDEMPOTENT on purpose: the component
 *  persists its open state in localStorage, so after the first test it mounts
 *  already open and an unconditional click would CLOSE it — which looked like
 *  "the Edit button does not exist". */
function expand() {
  const toggle = screen.getByRole("button", { name: /set up agents/i });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
}

/** The edit form only — the CREATE form below it carries identically labelled
 *  fields, so an unscoped query matches two elements. */
function form() {
  return within(screen.getByTestId("remote-edit-my-hermes"));
}

async function openEditor() {
  // The card is collapsed until "Set up agents" is pressed (it persists the
  // choice in localStorage). Expand it before looking for a row.
  expand();
  const edit = await screen.findByRole("button", { name: /edit/i });
  fireEvent.click(edit);
}

afterEach(() => {
  cleanup();
  calls.length = 0;
  localStorage.clear(); // the card remembers whether it was open
});

describe("the Edit affordance", () => {
  it("offers Edit alongside Test and Delete", async () => {
    renderCard();
    expand();
    expect(await screen.findByRole("button", { name: /edit/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /test/i })).toBeTruthy();
  });

  it("prefills what it CAN, so nothing is retyped", async () => {
    renderCard();
    await openEditor();
    const url = form().getByLabelText(/base url/i) as HTMLInputElement;
    expect(url.value).toBe("http://192.168.1.20:8080");
  });

  it("shows the name read-only, because panels refer to a remote by name", async () => {
    renderCard();
    await openEditor();
    // Present as text, but not as an editable field — renaming would orphan
    // panel/thread references silently.
    const named = screen.getAllByText("my-hermes");
    expect(named.length).toBeGreaterThan(0);
    const inputs = form().queryAllByDisplayValue("my-hermes");
    expect(inputs.length).toBe(0);
  });
});

describe("the credential, which is the part that loses data", () => {
  it("sends NO token when the box is left blank", async () => {
    renderCard();
    await openEditor();
    fireEvent.change(form().getByLabelText(/base url/i), {
      target: { value: "http://192.168.1.21:9090" },
    });
    fireEvent.click(form().getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(calls.length).toBe(1));
    const body = calls[0].body as Record<string, unknown>;
    expect(body.base_url).toBe("http://192.168.1.21:9090");
    // An empty string here would clear a working secret server-side.
    expect("token" in body).toBe(false);
    expect("clear_token" in body).toBe(false);
  });

  it("sends the token only when one was actually typed", async () => {
    renderCard();
    await openEditor();
    fireEvent.change(form().getByLabelText(/bearer secret/i), {
      target: { value: "rotated-bearer" },
    });
    fireEvent.click(form().getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(calls.length).toBe(1));
    expect((calls[0].body as Record<string, unknown>).token).toBe("rotated-bearer");
  });

  it("removes a credential only through the explicit checkbox", async () => {
    renderCard();
    await openEditor();
    fireEvent.click(form().getByLabelText(/remove the stored secret/i));
    fireEvent.click(form().getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(calls.length).toBe(1));
    expect((calls[0].body as Record<string, unknown>).clear_token).toBe(true);
  });

  it("does not offer removal when there is nothing stored", async () => {
    renderCard({ has_credential: false });
    await openEditor();
    expect(form().queryByLabelText(/remove the stored secret/i)).toBeNull();
  });

  it("tells the user a blank box keeps the current secret", async () => {
    // Without this the empty field reads as "there is no secret", and the
    // natural response is to hunt for a token they no longer have.
    renderCard();
    await openEditor();
    const box = form().getByLabelText(/bearer secret/i) as HTMLInputElement;
    expect(box.placeholder).toMatch(/keep/i);
  });
});

describe("the PATCH itself", () => {
  it("targets the agent by name", async () => {
    renderCard();
    await openEditor();
    fireEvent.click(form().getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].path).toBe("/agents/remote/my-hermes");
  });

  it("carries the fields the form owns", async () => {
    renderCard();
    await openEditor();
    fireEvent.click(form().getByLabelText(/^enabled$/i)); // turn it off
    fireEvent.click(form().getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(calls.length).toBe(1));
    const body = calls[0].body as Record<string, unknown>;
    expect(body.enabled).toBe(false);
    expect(body.kind).toBe("http-task");
  });
});
