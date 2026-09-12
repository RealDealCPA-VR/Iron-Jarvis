/**
 * C-06 — the draft card can put the email in your Drafts folder.
 *
 * WHAT MATTERS HERE, in order:
 *  - SUGGEST, DON'T ACT. Both buttons open a confirm dialog; nothing reaches
 *    the daemon until its own button is pressed, and "Save to Drafts" is the
 *    default mode.
 *  - THE FILE RIDES ALONG. The dialog offers exactly this conversation's files
 *    (the Files rail's list) and pre-ticks the one the draft names, because
 *    hunting for the workbook the chat just made is the job this replaces.
 *  - THE HEADERS BECOME HEADERS. A "To:" line the model wrote at the top of the
 *    body is the recipient, not the first paragraph of the email.
 *  - NO ACCOUNT IS NOT A FAILURE. With no email connected, the dialog says how
 *    to connect one and posts nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DraftCard } from "@/components/chat/DraftCard";
import { draftHeaders, splitAddresses } from "@/components/chat/EmailComposeDialog";
import { getThreadFiles, setThreadFiles, useThreadFiles } from "@/lib/threadFiles";

const api = vi.hoisted(() => ({
  posts: [] as { path: string; body: unknown }[],
  channels: [{ name: "work-email", type: "email" }] as { name: string; type: string }[],
  postResult: {} as Record<string, unknown>,
  postError: null as Error | null,
}));

vi.mock("@/lib/api", () => ({
  get: (path: string) =>
    path.startsWith("/comm/channels")
      ? Promise.resolve({ channels: api.channels })
      : Promise.resolve({}),
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    return api.postError ? Promise.reject(api.postError) : Promise.resolve(api.postResult);
  },
  ApiError: class ApiError extends Error {},
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

const FILE = "C:\\Users\\VR\\Documents\\Iron Jarvis\\2026-09-11 HarborPoint\\HarborPoint_Q1.xlsx";
const BODY = "To: client@example.com\n\nHi — the HarborPoint_Q1.xlsx workbook is attached.";

function renderCard(text = BODY) {
  return render(
    <DraftCard subject="Q1 expenses" text={text}>
      <p>{text}</p>
    </DraftCard>,
  );
}

/**
 * Open the confirm dialog and WAIT FOR THE FORM, not just the box (v1.255.1).
 *
 * The dialog's container mounts immediately; its form appears only once the
 * channels lookup has resolved (that is how the no-account branch below can
 * exist at all). Awaiting `email-compose-dialog` and then reading a field
 * synchronously therefore caught the pre-form state on a loaded runner: the
 * v1.255.0 Tests gate failed with "Unable to find a label with the text of:
 * To" while Release passed the same commit. Seven of the eight callers here
 * touch the form, so the wait belongs in one place; `form: false` is for the
 * one case that asserts the form is NOT offered.
 */
async function openDialog(
  which: "draft" | "send" = "draft",
  { form = true }: { form?: boolean } = {},
) {
  fireEvent.click(screen.getByTestId(which === "draft" ? "draft-save-draft" : "draft-send"));
  await screen.findByTestId("email-compose-dialog");
  if (form) await screen.findByTestId("compose-submit");
}

beforeEach(() => {
  api.posts = [];
  api.channels = [{ name: "work-email", type: "email" }];
  api.postResult = { ok: true, mode: "draft", folder: "[Gmail]/Drafts" };
  api.postError = null;
  setThreadFiles([FILE]);
});
afterEach(() => {
  cleanup();
  setThreadFiles([]);
});

// WHAT THE LIVE CHECK CAUGHT, AND THESE TESTS DID NOT. Every test in this file
// seeds `setThreadFiles([FILE])` in beforeEach, so all of them ran with the
// conversation's files already published. On the real surface the publisher is
// the ArtifactsRail, and chat/page.tsx mounts that rail only while the
// project/files panel is open (`workspaceOpen`, default OFF and persisted) —
// so a user with the panel shut opens this dialog and is offered NOTHING to
// attach. The mechanism worked; the feature did not reach the user. These two
// pin BOTH states so the empty one can never again read as the working one.
describe("the attachment list when this conversation has published no files", () => {
  it("offers nothing, says so, and still saves the draft", async () => {
    setThreadFiles([]); // the panel-closed state: no rail, no publisher
    renderCard();
    await openDialog();

    expect(
      document.querySelectorAll('[data-testid="email-compose-dialog"] input[type="checkbox"]')
        .length,
    ).toBe(0);
    expect(screen.getByText("No files in this conversation yet.")).toBeTruthy();

    // The dialog is still fully usable — an empty attachment list must never
    // become a dead end.
    fireEvent.click(screen.getByTestId("compose-submit"));
    await waitFor(() => expect(api.posts.length).toBe(1));
    expect(api.posts[0].path).toBe("/comm/email/compose");
    expect((api.posts[0].body as { attachments: string[] }).attachments).toEqual([]);
  });

  it("offers the file as soon as the rail publishes it — that chain IS the feature", async () => {
    setThreadFiles([]);
    renderCard();
    await openDialog();
    expect(screen.getByText("No files in this conversation yet.")).toBeTruthy();

    // What mounting the ArtifactsRail does (it calls setThreadFiles with its
    // rows). The dialog must pick the file up through the store, not a prop.
    await act(async () => {
      setThreadFiles([FILE]);
    });
    expect(getThreadFiles()).toEqual([FILE]);
  });
});

describe("the draft card's mail buttons", () => {
  it("offers both, and neither sends anything on its own", async () => {
    renderCard();
    expect(screen.getByTestId("draft-save-draft")).toBeTruthy();
    expect(screen.getByTestId("draft-send")).toBeTruthy();
    // Opening the dialog talks to nobody except the account check.
    await openDialog();
    expect(api.posts).toEqual([]);
  });

  it("pre-fills the recipient from the draft's own To: line and ticks the file it names", async () => {
    renderCard();
    await openDialog();
    // A STRAIGHT ASSERTION, because the old one could not do what it looked
    // like (v1.255.1): `getByLabelText?.("To")` guards whether the FUNCTION
    // exists, and `getByLabelText` THROWS when it matches nothing — so the
    // `?? getByDisplayValue(...)` fallback was dead code, and a missing label
    // was always an exception, never the alternative it appeared to offer.
    expect(screen.getByLabelText("To")).toBeTruthy();
    expect(screen.getByDisplayValue("client@example.com")).toBeTruthy();
    expect(screen.getByDisplayValue("Q1 expenses")).toBeTruthy();
    const box = screen.getByRole("checkbox") as HTMLInputElement;
    expect(box.checked).toBe(true); // the body names HarborPoint_Q1.xlsx
  });

  it("saves to Drafts with the recipients, the file, and no To: line left in the body", async () => {
    renderCard();
    await openDialog();
    fireEvent.click(screen.getByTestId("compose-submit"));

    await waitFor(() => expect(api.posts.length).toBe(1));
    const { path, body } = api.posts[0];
    const sent = body as Record<string, unknown>;
    expect(path).toBe("/comm/email/compose");
    expect(sent.mode).toBe("draft");
    expect(sent.to).toEqual(["client@example.com"]);
    expect(sent.attachments).toEqual([FILE]);
    expect(String(sent.text)).not.toMatch(/^to:/i);
    expect(String(sent.text)).toContain("HarborPoint_Q1.xlsx");
    await screen.findByText(/Saved to your \[Gmail\]\/Drafts folder/);
  });

  it("sending is a deliberate switch and needs an address", async () => {
    renderCard("Hi — the numbers are attached."); // no To: line in the draft
    await openDialog("send");
    const submit = screen.getByTestId("compose-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(screen.getByTestId("compose-note").textContent).toMatch(/Sends now/);

    fireEvent.click(screen.getByTestId("compose-mode-draft"));
    expect(screen.getByTestId("compose-note").textContent).toMatch(/nothing is sent/);
    expect((screen.getByTestId("compose-submit") as HTMLButtonElement).disabled).toBe(false);
  });

  it("with no email account it explains how to connect one and posts nothing", async () => {
    api.channels = [{ name: "this-pc", type: "desktop" }];
    renderCard();
    // The one case with NO form to wait for — that is the whole assertion.
    await openDialog("draft", { form: false });

    await screen.findByTestId("compose-no-account");
    expect(screen.getByText(/Add your email in Channels/)).toBeTruthy();
    expect(screen.queryByTestId("compose-submit")).toBeNull();
    expect(api.posts).toEqual([]);
  });

  it("a refusal from the mail server stays on screen and claims nothing", async () => {
    api.postError = Object.assign(new Error("the mail server refused the login"), { status: 502 });
    renderCard();
    await openDialog();
    fireEvent.click(screen.getByTestId("compose-submit"));

    await screen.findByText(/refused the login/);
    expect(screen.getByTestId("email-compose-dialog")).toBeTruthy();
    expect(screen.queryByText(/Saved to your/)).toBeNull();
  });
});

describe("draft headers", () => {
  it("takes To/Cc from the first lines only and keeps the rest of the body", () => {
    const { to, cc, body } = draftHeaders("To: a@x.com, b@y.com\nCc: c@z.com\n\nHello there.\nTo: not a header.");
    expect(to).toEqual(["a@x.com", "b@y.com"]);
    expect(cc).toEqual(["c@z.com"]);
    expect(body).toBe("Hello there.\nTo: not a header.");
  });

  it("leaves a body with no headers alone", () => {
    const { to, cc, body } = draftHeaders("Hello there.");
    expect(to).toEqual([]);
    expect(cc).toEqual([]);
    expect(body).toBe("Hello there.");
  });

  it("splits addresses on commas and semicolons", () => {
    expect(splitAddresses("a@x.com, Ann <b@y.com>; c@z.com")).toEqual([
      "a@x.com",
      "Ann <b@y.com>",
      "c@z.com",
    ]);
  });
});

describe("the conversation's file list", () => {
  it("is what the rail published, and clears with it", () => {
    setThreadFiles(["a.xlsx", "b.pdf", "a.xlsx"]);
    expect(getThreadFiles()).toEqual(["a.xlsx", "b.pdf"]); // deduped, order kept
    setThreadFiles([]);
    expect(getThreadFiles()).toEqual([]);
  });

  it("re-renders a reader when it changes", () => {
    function Probe() {
      return <span data-testid="files">{useThreadFiles().join("|")}</span>;
    }
    render(<Probe />);
    act(() => setThreadFiles(["x.pdf"]));
    expect(screen.getByTestId("files").textContent).toBe("x.pdf");
    act(() => setThreadFiles(["x.pdf", "y.docx"]));
    expect(screen.getByTestId("files").textContent).toBe("x.pdf|y.docx");
  });

  it("the Files rail is the publisher", () => {
    // The rail owns the list; a deleted publish line means the dialog offers
    // no attachments at all, which no unit test of the store could see.
    const src = require("node:fs")
      .readFileSync(require("node:path").join(__dirname, "../components/chat/ArtifactsRail.tsx"), "utf8")
      .replace(/\r\n/g, "\n");
    expect(src).toContain("setThreadFiles(rows.map((r) => r.path))");
    expect(src).toContain("return () => setThreadFiles([]);");
  });
});
