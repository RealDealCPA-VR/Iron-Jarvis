import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.181.0 — THE THREAD HUGS ITS CONVERSATION.
 *
 * The report: "the chat card is pinned to the round table card leaving a large
 * blank space above. The chat portion in the agents module should be pinned to
 * the top." The cause was a floor on the transcript (`min-h-[40vh]`): a
 * two-message thread painted its bubbles at the top of a box 40% of the
 * viewport tall and then a dead band down to the composer, which read as the
 * composer being pinned to the bottom of a card that never needed to be that
 * tall. Stacked under the roster (v1.180.0) that band is the last thing on the
 * page — nothing below it explains it away.
 *
 * WHAT THESE TESTS PROTECT, and why each one can regress on its own:
 *
 *  - A SETTLED THREAD RESERVES NOTHING. The whole complaint. Asserted as the
 *    ABSENCE of any min-height on the scroller, because "the composer follows
 *    the conversation" is exactly "the scroller is content-sized".
 *  - AN EMPTY THREAD IS STILL ROOMY. The obvious over-correction is to delete
 *    the floor outright, which flattens a fresh thread into a cramped strip and
 *    loses the idle-faces treatment. The empty state keeps its own generous
 *    centred block AND fills whatever room the card has (`flex-1`, the chat
 *    page's technique).
 *  - THE OPENING ROUND DOES NOT FLINCH. The subtle one. Going from "empty and
 *    roomy" to "one bubble and tiny" the instant Send is pressed is a worse
 *    artifact than the gap. The floor therefore survives through the round that
 *    STARTED on an empty thread (`round.base === 0`) — long enough for the
 *    answers to fill it — and is released when that round ends.
 *  - A ROUND IN AN EXISTING THREAD NEVER GAINS THE FLOOR. The mirror image: if
 *    the floor keyed off "a round is in flight" instead of "the opening round",
 *    sending into a short thread would grow the card to 36vh and shrink it back
 *    when the reply landed — inventing the very jump the floor exists to avoid.
 *  - A LONG THREAD IS STILL BOUNDED. Content-sized must not mean unbounded:
 *    `max-h-[62vh]` + `overflow-y-auto` keep a long transcript scrolling inside
 *    the card instead of growing the page forever.
 *
 * Every assertion reads real classes off the rendered nodes — no snapshots, so
 * a layout change has to be argued for rather than re-recorded.
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
    /** Resolver for POST /say, so a round can be held IN FLIGHT while the
     *  transcript is inspected mid-round. */
    releaseSay: null as null | ((v: unknown) => void),
    sayCalls: [] as string[],
    FakeApiError,
  };
});

/** A hand-cranked stand-in for the daemon's live event stream, so a test can
 *  land a reply MID-ROUND — the moment `messages.length` stops being 0 while
 *  the round is still speaking. Nothing else in this file can reach that
 *  state, and it is the only place two candidate implementations of the floor
 *  ("empty thread" vs "the opening round") disagree. */
const bus = vi.hoisted(() => ({
  events: [] as { id: string; type: string; payload: Record<string, unknown> }[],
  subs: new Set<() => void>(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => {
    if (path === "/agents/threads/t1") return Promise.resolve(api.thread);
    return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
  },
  post: (path: string) => {
    if (path.endsWith("/say")) {
      api.sayCalls.push(path);
      return new Promise((resolve) => {
        api.releaseSay = resolve;
      });
    }
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  return {
    useEvents: () => {
      const [, bump] = React.useState(0);
      React.useEffect(() => {
        const fn = () => bump((n) => n + 1);
        bus.subs.add(fn);
        return () => {
          bus.subs.delete(fn);
        };
      }, []);
      return { events: bus.events };
    },
  };
});

/** Push one `agent_thread.updated` frame, exactly as the daemon does after it
 *  persists a speaker's entry. The component answers by re-GETting the thread,
 *  so the test arms `api.thread` with the newer transcript first. */
function landReply(next: unknown) {
  api.thread = next;
  act(() => {
    bus.events = [
      { id: `e${bus.events.length + 1}`, type: "agent_thread.updated", payload: { thread_id: "t1" } },
      ...bus.events,
    ];
    bus.subs.forEach((f) => f());
  });
}

// The markdown pipeline is not under test here and only slows the run down.
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { RoundTable } from "@/components/agents/RoundTable";

// jsdom has no scrollIntoView; the RoundTable pins its transcript with it.
window.HTMLElement.prototype.scrollIntoView = () => {};

const PARTICIPANTS = [
  { key: "builtin:builder", source: "builtin", name: "builder", role: "lead" },
  { key: "dynamic:remy", source: "dynamic", name: "remy", role: "critic" },
];

function thread(messages: { who: string; content: string; at?: string }[]) {
  return {
    id: "t1",
    title: "Pricing",
    participants: PARTICIPANTS,
    message_count: messages.length,
    updated_at: "2026-08-16T10:00:00Z",
    messages,
  };
}

/** A settled two-message exchange — the shape the user complained about. */
const SHORT = thread([
  { who: "user", content: "flat rate or per-seat?", at: "2026-08-16T10:00:00Z" },
  { who: "builtin:builder", content: "per-seat, and here is why", at: "2026-08-16T10:00:05Z" },
]);

/** Enough turns that the transcript would run off the page uncapped. */
const LONG = thread(
  Array.from({ length: 40 }, (_, i) => ({
    who: i % 2 === 0 ? "user" : "builtin:builder",
    content: `turn number ${i}`,
    at: "2026-08-16T10:00:00Z",
  })),
);

function renderTable() {
  return render(
    <RoundTable threadId="t1" reloadNonce={0} onEditPanel={() => {}} onRoundDone={() => {}} />,
  );
}

const transcript = () => screen.getByTestId("thread-transcript");

/** Tailwind min-height utilities, in any unit — `min-h-[40vh]`, `min-h-[24rem]`,
 *  `min-h-96`. The claim under test is "no reserved band", not "not this one
 *  literal", so the check is deliberately the whole family, INCLUDING variant
 *  forms (`md:min-h-[40vh]`, `sm:min-h-96`): a floor re-introduced behind a
 *  breakpoint reserves exactly the same band on the screen the user is looking
 *  at, and a check anchored only to a word boundary would wave it through. */
const MIN_HEIGHT = /(?:^|\s|:)min-h-/;

beforeEach(() => {
  api.thread = SHORT;
  api.releaseSay = null;
  api.sayCalls = [];
  bus.events = [];
  bus.subs.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ------------------------------------------------- a settled short thread -- */

describe("a short thread reserves no space it cannot fill", () => {
  it("leaves no band between the last message and the composer", async () => {
    renderTable();
    expect(await screen.findByText("per-seat, and here is why")).toBeInTheDocument();
    // THE COMPLAINT, ASSERTED: the scroller is sized by its content, so the
    // composer sits directly under the conversation.
    expect(transcript().className).not.toMatch(MIN_HEIGHT);
  });

  it("does not smuggle the floor back onto the messages themselves", async () => {
    renderTable();
    const bubble = await screen.findByText("per-seat, and here is why");
    // A floor moved down one level (onto the last bubble, or onto a spacer)
    // would look identical on the card and re-open the gap. Nothing between
    // the transcript and a message may reserve height either.
    let node: HTMLElement | null = bubble;
    const scroller = transcript();
    while (node && node !== scroller) {
      expect(node.className).not.toMatch(MIN_HEIGHT);
      node = node.parentElement;
    }
  });

  it("still renders the composer inside the same card as the transcript", async () => {
    renderTable();
    expect(await screen.findByText("per-seat, and here is why")).toBeInTheDocument();
    // Guards the fix's shape: the conversation and the composer are one card,
    // so "hug the content" moves the composer UP rather than detaching it.
    const composer = screen.getByLabelText("Message the panel");
    const card = transcript().parentElement!;
    expect(card.contains(composer)).toBe(true);
  });
});

/* ------------------------------------------------------- the empty thread -- */

describe("an empty thread keeps its generous centred treatment", () => {
  beforeEach(() => {
    api.thread = thread([]);
  });

  it("gives a fresh thread real room, centred, with the panel's faces", async () => {
    renderTable();
    const empty = await screen.findByTestId("thread-empty");
    // Roomy…
    expect(empty.className).toMatch(/min-h-\[36vh\]/);
    // …centred, both axes…
    expect(empty.className).toMatch(/items-center/);
    expect(empty.className).toMatch(/justify-center/);
    // …and it FILLS whatever the card gives it rather than leaving the
    // reserved room blank under the copy (the chat page's technique).
    expect(empty.className).toMatch(/(?:^|\s)flex-1(?:\s|$)/);
    // The idle faces are the thing the room is for.
    expect(screen.getByTitle("builder")).toBeInTheDocument();
    expect(screen.getByTitle("remy")).toBeInTheDocument();
  });

  it("holds the room on the scroller too, so the card cannot render short", async () => {
    renderTable();
    await screen.findByTestId("thread-empty");
    expect(transcript().className).toMatch(/min-h-\[36vh\]/);
  });
});

/* ---------------------------------------------------- no jump on the open -- */

describe("the opening round does not flinch", () => {
  it("keeps the room while the first round on a fresh thread is in flight", async () => {
    api.thread = thread([]);
    renderTable();
    await screen.findByTestId("thread-empty");
    expect(transcript().className).toMatch(/min-h-\[36vh\]/);

    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "what should we charge?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ask the panel/i }));

    // Mid-round: the empty state is gone (one optimistic bubble + the speaker
    // strip stand in its place) and the card MUST NOT have collapsed around
    // them — the answers are about to land in exactly that room.
    await waitFor(() => {
      expect(screen.getByText("what should we charge?")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("thread-empty")).toBeNull();
    expect(transcript().className).toMatch(/min-h-\[36vh\]/);
  });

  it("holds the room while the opening round's replies stream in", async () => {
    api.thread = thread([]);
    renderTable();
    await screen.findByTestId("thread-empty");

    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "what should we charge?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ask the panel/i }));
    await waitFor(() => {
      expect(screen.getByText("what should we charge?")).toBeInTheDocument();
    });

    // The daemon persists the FIRST speaker's entry and announces it. The
    // round is still in flight (POST /say has not returned) but the transcript
    // is no longer empty — and that is the whole difference between a floor
    // keyed on "the thread is empty" and one keyed on "the OPENING round".
    // The first keeps the room only until the first reply arrives, then
    // shrinks the card out from under a round that is still speaking; a
    // mid-stream collapse is the exact artifact the floor exists to prevent.
    landReply(
      thread([
        { who: "user", content: "what should we charge?", at: "2026-08-16T10:01:00Z" },
        { who: "builtin:builder", content: "start at per-seat", at: "2026-08-16T10:01:04Z" },
      ]),
    );
    await waitFor(() => {
      expect(screen.getByText("start at per-seat")).toBeInTheDocument();
    });
    // Still speaking — remy has not answered yet.
    expect(screen.getByText(/1 of 2 answered/i)).toBeInTheDocument();
    expect(transcript().className).toMatch(/min-h-\[36vh\]/);
  });

  it("releases the room once the opening round has finished speaking", async () => {
    // THE OTHER HALF OF THE FLOOR, and the half nothing else here covers: the
    // floor is only defensible because it LETS GO. If the release ever broke —
    // a latch, a ref that never clears, a condition widened to "this thread
    // opened empty" — a freshly created thread would keep a 36vh floor for the
    // rest of its life, which is the user's original complaint reappearing on
    // exactly the threads they are most likely to be looking at.
    api.thread = thread([]);
    renderTable();
    await screen.findByTestId("thread-empty");

    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "what should we charge?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ask the panel/i }));
    await waitFor(() => {
      expect(transcript().className).toMatch(/min-h-\[36vh\]/);
    });

    // The blocking POST /say returns: the round is over and its entries are
    // the transcript. `base === 0`, so these ARE the whole thread.
    const entries = [
      { who: "user", content: "what should we charge?", at: "2026-08-16T10:01:00Z" },
      { who: "builtin:builder", content: "start at per-seat", at: "2026-08-16T10:01:04Z" },
      { who: "dynamic:remy", content: "only above ten seats", at: "2026-08-16T10:01:09Z" },
    ];
    await act(async () => {
      api.releaseSay!({ entries });
    });

    // The real assertion lives INSIDE the wait — the floor is released in the
    // `finally` that clears the round, which is a LATER state update than the
    // entries landing, so waiting on the text and then asserting the class
    // would be waiting on a signal that arrives first.
    await waitFor(() => {
      expect(transcript().className).not.toMatch(MIN_HEIGHT);
    });
    expect(screen.getByText("only above ten seats")).toBeInTheDocument();
  });

  it("never grows a short EXISTING thread when a round starts in it", async () => {
    renderTable();
    expect(await screen.findByText("per-seat, and here is why")).toBeInTheDocument();
    expect(transcript().className).not.toMatch(MIN_HEIGHT);

    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "say more" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ask the panel/i }));

    // The mirror image of the test above: a floor keyed off "speaking" rather
    // than "the opening round" would grow this card to 36vh here and shrink it
    // back when the reply landed — inventing a jump instead of preventing one.
    await waitFor(() => {
      expect(screen.getByText("say more")).toBeInTheDocument();
    });
    expect(transcript().className).not.toMatch(MIN_HEIGHT);
  });
});

/* --------------------------------------------------------- the long thread */

describe("a long thread scrolls inside the card", () => {
  beforeEach(() => {
    api.thread = LONG;
  });

  it("stays bounded and scrollable instead of growing the page", async () => {
    renderTable();
    expect(await screen.findByText("turn number 39")).toBeInTheDocument();
    const cls = transcript().className;
    // A ceiling…
    expect(cls).toMatch(/max-h-\[62vh\]/);
    // …that a taller transcript scrolls within, rather than overflowing the
    // card or pushing the composer off the page.
    expect(cls).toMatch(/overflow-y-auto/);
    // …and still no floor: content well past the ceiling must not also carry
    // reserved space.
    expect(cls).not.toMatch(MIN_HEIGHT);
  });
});
