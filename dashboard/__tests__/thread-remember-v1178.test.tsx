import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.178.0 P3 — committing what a panel concluded, AFTER reading it
 * (RoundTable's "Extract and add to memory").
 *
 * `POST /agents/threads/{id}/remember` is a two-step contract and BOTH steps
 * fail silently when they are wrong — a wrong write looks exactly like a right
 * one until months later, when the app quotes back a memory nobody wrote:
 *
 *  - THE LOOK WRITES NOTHING. The first call must not carry `preview:false`.
 *    A mutant that commits on the first click renders the same success-shaped
 *    UI and has already written to long-term memory; only the request body
 *    proves it, so the body is what is asserted.
 *  - THE REVIEW HAS CONTENT. A confirm dialog with no text is not a review.
 *    The extracted items must be ON SCREEN, and a truncation marker among them
 *    must survive to the screen too — a preview that quietly shows less than
 *    what lands is worse than no preview.
 *  - THE COMMIT SENDS THE APPROVED TEXT BACK, VERBATIM. Without `content` the
 *    daemon re-runs the distillation, and a model asked twice does not answer
 *    twice the same: the text stored is then not the text approved. A mutant
 *    that posts `{preview:false}` alone still shows a green receipt.
 *  - `distilled:false` IS SAID OUT LOUD. With no real model connected the
 *    daemon stores a verbatim excerpt; rendering that like a real distillation
 *    lets the user believe a summary was written when it was not.
 *  - A FAILED COMMIT IS A FAILURE. The error is shown, no success is claimed,
 *    and the review the user was reading is still standing.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    thread: null as unknown,
    /** A SECOND thread, so a thread switch can be driven for real. */
    thread2: null as unknown,
    /** Every POST, in order — path AND body (the body is the evidence). */
    posts: [] as { path: string; body: Record<string, unknown> }[],
    /** Per-test responder; sees the request body so it can act like the
     *  daemon does (preview vs commit). */
    reply: null as null | ((body: Record<string, unknown>) => Promise<unknown>),
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => {
    if (path === "/agents/threads/t1") return Promise.resolve(api.thread);
    if (path === "/agents/threads/t2") return Promise.resolve(api.thread2);
    return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
  },
  post: (path: string, body?: unknown) => {
    const b = (body ?? {}) as Record<string, unknown>;
    api.posts.push({ path, body: b });
    return api.reply ? api.reply(b) : Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));

// The markdown pipeline is not under test here and only slows the run down.
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { RoundTable } from "@/components/agents/RoundTable";

// jsdom has no scrollIntoView; the RoundTable pins its transcript with it.
window.HTMLElement.prototype.scrollIntoView = () => {};

const REMEMBER_PATH = "/agents/threads/t1/remember";

const THREAD = {
  id: "t1",
  title: "Pricing",
  participants: [
    { key: "builtin:builder", source: "builtin", name: "builder", role: "lead" },
    { key: "dynamic:remy", source: "dynamic", name: "remy", role: "critic" },
  ],
  message_count: 3,
  updated_at: "2026-08-16T10:00:00Z",
  messages: [
    { who: "user", content: "flat rate or per-seat?", at: "2026-08-16T10:00:00Z" },
    { who: "builtin:builder", content: "hello there", at: "2026-08-16T10:00:05Z" },
    { who: "dynamic:remy", content: "flat rate", at: "2026-08-16T10:00:09Z" },
  ],
};

/** Another panel entirely — the thread the user switches TO. */
const OTHER_THREAD = {
  ...THREAD,
  id: "t2",
  title: "Audit timing",
  messages: [
    { who: "user", content: "when do we start the audit?", at: "2026-08-16T11:00:00Z" },
    { who: "builtin:builder", content: "bring the audit forward", at: "2026-08-16T11:00:04Z" },
  ],
};

/** The daemon's preview shape (agents/threads.py AgentThreads.remember). */
const PREVIEW = {
  ok: true,
  preview: true,
  ref: "",
  source: "brain",
  mode: "distill",
  distilled: true,
  title: "Panel: Pricing",
  messages: 3,
  participants: ["builder", "remy"],
  items: [
    "Decision: ship the flat-rate tier first",
    "Risk: the migration needs a backfill",
  ],
  content:
    "Panel: Pricing — 2 participants, 3 messages\n\n" +
    "- Decision: ship the flat-rate tier first\n" +
    "- Risk: the migration needs a backfill\n\nagent thread: t1",
};

function renderTable() {
  return render(
    <RoundTable threadId="t1" reloadNonce={0} onEditPanel={() => {}} onRoundDone={() => {}} />,
  );
}

/** Render, wait for the transcript, click Extract, wait for the review card. */
async function openPreview() {
  const view = renderTable();
  expect(await screen.findByText("hello there")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Extract and add to memory/i }));
  await screen.findByRole("button", { name: /Save to memory/i });
  return view;
}

beforeEach(() => {
  api.thread = THREAD;
  api.thread2 = OTHER_THREAD;
  api.posts = [];
  api.reply = async () => PREVIEW;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ------------------------------------------------------- the look is a look */

describe("extracting previews, and writes nothing", () => {
  it("posts to the thread's remember route WITHOUT committing", async () => {
    await openPreview();
    await waitFor(() => {
      const call = api.posts.find((p) => p.path === REMEMBER_PATH);
      expect(call).toBeTruthy();
      // THE WHOLE POINT: this call may not be a commit. `preview:false` here
      // means the panel was already written to memory before anyone read it.
      expect(call!.body.preview).not.toBe(false);
      // …and it carries no approved content, because nothing was approved yet.
      expect(call!.body.content).toBeUndefined();
    });
    // Exactly one call: the preview is not a preview+commit pair.
    expect(api.posts.filter((p) => p.path === REMEMBER_PATH)).toHaveLength(1);
  });

  it("says on screen that nothing has been written yet, and claims no save", async () => {
    await openPreview();
    expect(screen.getByText(/Nothing has been written yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/Saved to memory/i)).toBeNull();
  });

  it("cannot be triggered on a thread with no messages", async () => {
    api.thread = { ...THREAD, message_count: 0, messages: [] };
    renderTable();
    const button = await screen.findByRole("button", {
      name: /Extract and add to memory/i,
    });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(api.posts).toHaveLength(0);
  });
});

/* ----------------------------------------------------- the review has content */

describe("the preview is readable", () => {
  it("lists the extracted items", async () => {
    await openPreview();
    expect(
      screen.getByText("Decision: ship the flat-rate tier first"),
    ).toBeInTheDocument();
    expect(screen.getByText("Risk: the migration needs a backfill")).toBeInTheDocument();
  });

  it("keeps a truncation marker visible instead of hiding it", async () => {
    const marker =
      "[… only the first 40 items are listed here — the full text below is what will be committed …]";
    api.reply = async () => ({ ...PREVIEW, items: [...PREVIEW.items, marker] });
    await openPreview();
    expect(screen.getByText(marker)).toBeInTheDocument();
  });

  it("offers the exact text that would land", async () => {
    const { container } = await openPreview();
    // Read off textContent, not getByText: the body is multi-line and
    // testing-library normalizes whitespace, which would let a reflowed copy
    // pass as "the exact text".
    expect(container.querySelector("pre")?.textContent).toBe(PREVIEW.content);
  });
});

/* --------------------------------------------------- the commit is verbatim */

describe("committing sends back what was approved", () => {
  it("posts preview:false WITH the previewed content, unchanged", async () => {
    api.reply = async (body) =>
      body.preview === false
        ? {
            ...PREVIEW,
            preview: false,
            ref: "brain/panel-pricing.md",
            distilled: false,
            note: "committed the text you approved (no re-distillation)",
          }
        : PREVIEW;
    await openPreview();
    fireEvent.click(screen.getByRole("button", { name: /Save to memory/i }));
    await waitFor(() => {
      const commit = api.posts.find((p) => p.body.preview === false);
      expect(commit).toBeTruthy();
      expect(commit!.path).toBe(REMEMBER_PATH);
      // VERBATIM — byte for byte the string the preview showed. A commit
      // without it re-distills and stores text the user never saw.
      expect(commit!.body.content).toBe(PREVIEW.content);
    });
  });

  it("reports the save with the daemon's own note, inventing no degrade", async () => {
    api.reply = async (body) =>
      body.preview === false
        ? {
            ...PREVIEW,
            preview: false,
            ref: "brain/panel-pricing.md",
            source: "brain",
            // The commit path answers distilled:false because it did NOT
            // re-distill — a different claim from "no model was connected".
            distilled: false,
            note: "committed the text you approved (no re-distillation)",
          }
        : PREVIEW;
    await openPreview();
    fireEvent.click(screen.getByRole("button", { name: /Save to memory/i }));
    await waitFor(() => {
      expect(screen.getByText(/Saved to memory/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/no re-distillation/i)).toBeInTheDocument();
    expect(screen.queryByText(/verbatim excerpt/i)).toBeNull();
  });

  it("does not point at a full text it is not showing", async () => {
    // The empty-items fallback used to say "read the full text below" in ALL
    // cases — including the one where the <details> holding that text is not
    // rendered at all, because there was nothing to approve (reviewer
    // finding). Describing content the screen is not showing is the quiet
    // version of the same lie a truncated preview tells.
    api.reply = async () => ({ ...PREVIEW, items: [], content: "" });
    renderTable();
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Extract and add to memory/i }));
    await screen.findByRole("button", { name: /Save to memory/i });
    expect(screen.queryByText(/read the full text below/i)).toBeNull();
    expect(screen.getByText(/carried nothing to read/i)).toBeInTheDocument();
  });

  it("refuses to commit a preview that carried no text to approve", async () => {
    // Nothing to send back means a commit would re-run the ladder blind.
    api.reply = async () => ({ ...PREVIEW, content: "" });
    renderTable();
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Extract and add to memory/i }));
    const save = await screen.findByRole("button", { name: /Save to memory/i });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(api.posts.some((p) => p.body.preview === false)).toBe(false);
  });
});

/* ------------------------------------------------------------ the honesty */

describe("an undistilled memory says so", () => {
  it("shows the daemon's note when distilled is false", async () => {
    api.reply = async () => ({
      ...PREVIEW,
      distilled: false,
      note: "no real model connected — this is a verbatim excerpt, not a distillation",
    });
    await openPreview();
    const note = screen.getByText(/no real model connected — this is a verbatim excerpt/i);
    expect(note).toBeInTheDocument();
    // And it reads as a WARNING (the house's amber), not as quiet prose the
    // eye slides past on the way to the Save button — the whole risk here is
    // the user believing a summary was written when it was not.
    expect(note.closest(".text-amber-200\\/90")).not.toBeNull();
  });

  it("still says it when the response set the flag but sent no note", async () => {
    api.reply = async () => ({ ...PREVIEW, distilled: false, note: undefined });
    await openPreview();
    expect(screen.getByText(/verbatim excerpt, not a distillation/i)).toBeInTheDocument();
  });

  it("does not accuse a real distillation of being an excerpt", async () => {
    await openPreview(); // distilled: true
    expect(screen.queryByText(/verbatim excerpt/i)).toBeNull();
  });

  it("says nothing about a degrade when the daemon sent no `distilled` at all", async () => {
    // THE OTHER DIRECTION OF THE SAME LIE (reviewer addition). A response
    // without the flag is UNKNOWN, not a denial: a variant or older daemon
    // that simply omits it must not make this screen announce "no real model
    // was connected" — that sentence would be the UI's invention, and it
    // would talk a user out of trusting a distillation that did happen.
    // Reads exactly like the mutant `result.distilled !== true`, which the
    // rest of this file cannot see (every other case sends the flag).
    const { distilled: _dropped, ...noFlag } = PREVIEW;
    api.reply = async () => noFlag;
    await openPreview();
    expect(screen.queryByText(/verbatim excerpt/i)).toBeNull();
    expect(screen.queryByText(/No real model was connected/i)).toBeNull();
    // …and the review itself still works — an unknown flag degrades to
    // "say less", never to "show less".
    expect(screen.getByText("Decision: ship the flat-rate tier first")).toBeInTheDocument();
  });
});

/* --------------------------------------------------- a preview is per-thread */

describe("a preview belongs to the thread it came from", () => {
  it("drops the pending review when the user switches threads", async () => {
    // The commit posts to the CURRENTLY open thread id but sends the text the
    // user approved. A preview surviving a thread switch therefore offers a
    // Save that writes panel A's conclusions into a note committed against
    // thread B — and the genRef guard cannot catch it, because that write is
    // a NEW, perfectly in-flight-for-this-thread request. The only defence is
    // dropping the preview, so the drop is asserted.
    const view = await openPreview();
    view.rerender(
      <RoundTable threadId="t2" reloadNonce={0} onEditPanel={() => {}} onRoundDone={() => {}} />,
    );
    await waitFor(() => {
      expect(screen.getByText("bring the audit forward")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Save to memory/i })).toBeNull();
      expect(screen.queryByText("Decision: ship the flat-rate tier first")).toBeNull();
    });
  });
});

/* ------------------------------------------------------------- the errors */

describe("failures are shown, never swallowed", () => {
  it("a failed commit shows the reason and claims no success", async () => {
    api.reply = async (body) => {
      if (body.preview === false) {
        throw new api.FakeApiError("could not write to 'brain': disk is full", 422);
      }
      return PREVIEW;
    };
    await openPreview();
    fireEvent.click(screen.getByRole("button", { name: /Save to memory/i }));
    await waitFor(() => {
      expect(screen.getByText(/disk is full/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Saved to memory/i)).toBeNull();
    // The review the user was reading is still standing — retrying sends the
    // same approved text rather than starting the whole ladder over.
    expect(screen.getByRole("button", { name: /Save to memory/i })).toBeInTheDocument();
    expect(screen.getByText("Decision: ship the flat-rate tier first")).toBeInTheDocument();
  });

  it("a failed preview shows the reason and fakes no review card", async () => {
    api.reply = async () => {
      throw new api.FakeApiError("no such thread", 404);
    };
    renderTable();
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Extract and add to memory/i }));
    await waitFor(() => {
      expect(screen.getByText(/no such thread/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /Save to memory/i })).toBeNull();
  });

  it("names the daemon being gone instead of relaying an empty message", async () => {
    api.reply = async () => {
      throw new api.FakeApiError("", 0); // lib/api maps a dead fetch to status 0
    };
    renderTable();
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Extract and add to memory/i }));
    await waitFor(() => {
      expect(screen.getByText(/Daemon offline/i)).toBeInTheDocument();
    });
  });

  it("leaves the transcript itself untouched when the commit fails", async () => {
    api.reply = async (body) => {
      if (body.preview === false) throw new api.FakeApiError("nope", 422);
      return PREVIEW;
    };
    await openPreview();
    fireEvent.click(screen.getByRole("button", { name: /Save to memory/i }));
    await waitFor(() => {
      expect(screen.getByText(/nope/i)).toBeInTheDocument();
    });
    expect(screen.getByText("hello there")).toBeInTheDocument();
    expect(screen.getByText("flat rate")).toBeInTheDocument();
  });
});
