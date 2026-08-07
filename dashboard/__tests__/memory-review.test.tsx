import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

/**
 * Memory housekeeping (v1.143.0) — the review card's four promises.
 *
 * The card is the visible half of one rule: the steward may ADD memory freely,
 * but every REVISION waits for a click. Three of its four failure modes are
 * invisible on a happy path —
 *
 *  - the PROMISE. "Nothing is changed until you approve" has to be on screen,
 *    in words, or the rule is only in the code.
 *  - the DEGRADE. An older daemon has no /memory/review; the card must be
 *    absent entirely, not an empty box advertising a feature that isn't there.
 *  - the HONESTY. A memory base this daemon can't rewrite (Notion, a plug-in)
 *    must disable Approve and SAY why, before the click, not after it fails.
 *  - the ACTION. Approve is the only thing that calls the approve route, and
 *    it goes through the row's own id.
 */

const apiState = vi.hoisted(() => ({
  calls: [] as string[],
  posts: [] as string[],
  /** Reject GET /memory/review with this status (404 = older daemon). */
  failStatus: null as number | null,
  overview: {} as Record<string, unknown>,
  /** What POST /memory/review/run answers. */
  runResult: {} as Record<string, unknown>,
  /** What POST …/approve answers (the honest post-hoc undo truth). */
  approveResult: {} as Record<string, unknown>,
  /** Make one POST path reject, to check the error is relayed verbatim. */
  postFailure: null as { path: string; message: string } | null,
}));

class FakeApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

vi.mock("@/lib/api", () => ({
  ApiError: FakeApiError,
  get: (path: string) => {
    apiState.calls.push(path);
    if (apiState.failStatus !== null) {
      return Promise.reject(new FakeApiError("nope", apiState.failStatus));
    }
    return Promise.resolve(apiState.overview);
  },
  post: (path: string) => {
    apiState.posts.push(path);
    if (path === "/memory/review/run") return Promise.resolve(apiState.runResult);
    if (apiState.postFailure && path.includes(apiState.postFailure.path)) {
      return Promise.reject(new FakeApiError(apiState.postFailure.message, 409));
    }
    if (path.endsWith("/approve")) return Promise.resolve(apiState.approveResult);
    return Promise.resolve({});
  },
}));

// Imported AFTER the mock so the component binds to the fake client.
const { MemoryReview } = await import("@/components/memory/MemoryReview");

const PROPOSAL = {
  id: "mprop_1",
  kind: "duplicate",
  base: "brain",
  refs: ["C:/notes/alpha.md", "C:/notes/alpha-copy.md"],
  rationale: "Both notes record the same filing deadline.",
  suggested_action: "Keep “alpha” and remove the copy.",
  status: "pending",
  can_apply: true,
  undoable: true,
  base_note: "Applying this is undoable — it lands on the Time travel list.",
  rewrites: false,
  survivor_ref: "",
  remove_refs: ["C:/notes/alpha-copy.md"],
  removes: 1,
};

function overview(over: Record<string, unknown> = {}) {
  return {
    proposals: [PROPOSAL],
    pending: 1,
    stats: { pending: 1, approved: 0, dismissed: 0 },
    steward: { available: true, stats: { last_run_at: "", notes_added: 4 }, runs: [] },
    template: {
      name: "memory-review-weekly",
      label: "Memory review — weekly",
      cron: "0 9 * * 1",
      kind: "task",
      task: "Review my recent conversations…",
      description: "Once a week…",
      installed: false,
    },
    ...over,
  };
}

beforeEach(() => {
  apiState.calls = [];
  apiState.posts = [];
  apiState.failStatus = null;
  apiState.overview = overview();
  apiState.runResult = { started: true, session_id: "sess_1" };
  apiState.approveResult = { applied: { ok: true, undoable: true } };
  apiState.postFailure = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the safety promise is on screen", () => {
  it("says nothing changes until you approve", async () => {
    render(<MemoryReview />);
    await screen.findByText(/Nothing is changed until you approve/i);
  });

  it("speaks the canon: memory base, never 'source' or 'brain' as a noun", async () => {
    const { container } = render(<MemoryReview />);
    await screen.findByText("Duplicate");
    expect(container.textContent).toContain("memory base");
    expect(container.textContent).not.toMatch(/\bsources?\b/i);
    // "brain" survives ONLY as the built-in base's identity (renaming it would
    // break saved bindings) — never as prose describing the memory.
    expect(container.textContent).not.toMatch(/\b(the|its|my) brain\b/i);
  });
});

describe("degrades on an older daemon", () => {
  it("renders nothing at all when /memory/review 404s", async () => {
    apiState.failStatus = 404;
    const { container } = render(<MemoryReview />);
    await waitFor(() => expect(apiState.calls).toContain("/memory/review"));
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("renders nothing before the first answer lands", () => {
    const { container } = render(<MemoryReview />);
    expect(container.textContent).toBe("");
  });
});

describe("the review queue", () => {
  it("shows the kind badge, the reason, and the affected notes by name", async () => {
    render(<MemoryReview />);
    await screen.findByText("Duplicate");
    expect(screen.getByText(/Both notes record the same filing deadline/)).toBeTruthy();
    expect(screen.getByText("alpha")).toBeTruthy();
    expect(screen.getByText("alpha-copy")).toBeTruthy();
  });

  it("carries a compact steward status line", async () => {
    render(<MemoryReview />);
    await screen.findByText(/No review has run yet · 4 notes added · 1 suggestion waiting/);
  });

  it("approve posts to this row's approve route", async () => {
    render(<MemoryReview />);
    const button = await screen.findByRole("button", { name: /Approve/i });
    await act(async () => {
      button.click();
    });
    expect(apiState.posts).toContain("/memory/review/mprop_1/approve");
  });

  it("dismiss posts to this row's dismiss route", async () => {
    render(<MemoryReview />);
    const button = await screen.findByRole("button", { name: /Dismiss/i });
    await act(async () => {
      button.click();
    });
    expect(apiState.posts).toContain("/memory/review/mprop_1/dismiss");
  });
});

describe("honest about bases it cannot change", () => {
  it("disables Approve and explains why, before the click", async () => {
    apiState.overview = overview({
      proposals: [
        {
          ...PROPOSAL,
          base: "notion",
          can_apply: false,
          undoable: false,
          base_note:
            "“notion” keeps its notes outside this computer, so Iron Jarvis can’t rewrite or remove them from here.",
        },
      ],
    });
    render(<MemoryReview />);
    const approve = await screen.findByRole("button", { name: /Approve/i });
    expect(approve).toBeDisabled();
    expect(screen.getByText(/keeps its notes outside this computer/)).toBeTruthy();
    // Dismiss stays available — "I'll handle it there" must still be sayable.
    expect(screen.getByRole("button", { name: /Dismiss/i })).not.toBeDisabled();
  });

  it("only badges a row as undoable when the base actually supports it", async () => {
    const { unmount } = render(<MemoryReview />);
    await screen.findByTitle(/Time travel list/i); // undoable: true -> badged
    unmount();

    apiState.overview = overview({
      proposals: [{ ...PROPOSAL, base: "notion", can_apply: false, undoable: false }],
    });
    render(<MemoryReview />);
    await screen.findByText("Duplicate");
    expect(screen.queryByTitle(/Time travel list/i)).toBeNull();
  });
});

describe("Review now is honest about what happened", () => {
  it("reports a started review", async () => {
    render(<MemoryReview />);
    const button = await screen.findByRole("button", { name: /Review now/i });
    await act(async () => {
      button.click();
    });
    expect(apiState.posts).toContain("/memory/review/run");
    await screen.findByText(/Review started/i);
  });

  it("relays 'nothing new to review' instead of faking a run", async () => {
    apiState.runResult = {
      started: false,
      note: "Nothing new to review — there are no conversations since the last memory review.",
    };
    render(<MemoryReview />);
    const button = await screen.findByRole("button", { name: /Review now/i });
    await act(async () => {
      button.click();
    });
    await screen.findByText(/Nothing new to review/i);
    expect(screen.queryByText(/Review started/i)).toBeNull();
  });
});

describe("the card shows what approving will ACTUALLY do", () => {
  it("names the notes that get deleted, from the payload not the prose", async () => {
    render(<MemoryReview />);
    await screen.findByText(/Approving deletes 1 note: “alpha-copy”\./);
  });

  it("says which note gets replaced on a merge", async () => {
    apiState.overview = overview({
      proposals: [
        {
          ...PROPOSAL,
          kind: "merge",
          rewrites: true,
          survivor_ref: "C:/notes/alpha.md",
          remove_refs: ["C:/notes/alpha-copy.md", "C:/notes/old.md"],
          removes: 2,
        },
      ],
    });
    render(<MemoryReview />);
    await screen.findByText(
      /Approving replaces everything in “alpha”, and deletes 2 notes: “alpha-copy”, “old”\./,
    );
  });

  it("warns when an earlier approve got part-way and then failed", async () => {
    apiState.overview = overview({
      proposals: [
        {
          ...PROPOSAL,
          partial: true,
          applied: { changed: ["Rewrote “alpha”"], partial: true },
        },
      ],
    });
    render(<MemoryReview />);
    await screen.findByText(/An earlier attempt got part-way/);
    await screen.findByText(/Rewrote “alpha”/);
  });

  it("promises undo from what the server journalled, not from the forecast", async () => {
    apiState.approveResult = { applied: { ok: true, undoable: false } };
    render(<MemoryReview />);
    const button = await screen.findByRole("button", { name: /Approve/i });
    await act(async () => {
      button.click();
    });
    await screen.findByText("Memory updated.");
    expect(screen.queryByText(/undo it from Time travel/i)).toBeNull();
  });

  it("relays an approve failure verbatim, on the row", async () => {
    apiState.postFailure = {
      path: "/approve",
      message: "could not remove “old”: locked by another process",
    };
    render(<MemoryReview />);
    const button = await screen.findByRole("button", { name: /Approve/i });
    await act(async () => {
      button.click();
    });
    await screen.findByText(/locked by another process/);
  });
});

describe("the review point is honest about what it will not re-read", () => {
  it("shows the limitation and the escape hatch together", async () => {
    apiState.overview = overview({
      steward: {
        available: true,
        stats: {
          last_run_at: "",
          notes_added: 4,
          cursor_note:
            "Reviews resume from the newest conversation already reviewed. " +
            "Conversations that were indexed later but happened EARLIER are not " +
            "offered again — reset the review point to re-read them.",
        },
        runs: [],
      },
    });
    render(<MemoryReview />);
    await screen.findByText(/Reviews resume from the newest conversation/);
    const reset = await screen.findByRole("button", { name: /Reset the review point/i });
    await act(async () => {
      reset.click();
    });
    expect(apiState.posts).toContain("/memory/review/reset");
  });

  it("says nothing about a review point before the first review", async () => {
    render(<MemoryReview />);
    await screen.findByText("Duplicate");
    expect(screen.queryByRole("button", { name: /Reset the review point/i })).toBeNull();
  });
});

describe("the weekly schedule is opt-in", () => {
  it("offers it as a button that POSTs an ordinary schedule", async () => {
    render(<MemoryReview />);
    const button = await screen.findByRole("button", { name: /Review weekly/i });
    await act(async () => {
      button.click();
    });
    expect(apiState.posts).toContain("/schedules");
  });

  it("stops offering it once it is installed", async () => {
    apiState.overview = overview({
      template: { ...(overview().template as object), installed: true },
    });
    render(<MemoryReview />);
    await screen.findByText(/Reviewing weekly/);
    expect(screen.queryByRole("button", { name: /Review weekly/i })).toBeNull();
  });
});
