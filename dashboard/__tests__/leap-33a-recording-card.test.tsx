/**
 * Idea 33-a: a recording becomes a transcript, decisions and open items — the
 * card under the reply.
 *
 * - The card rides the reply's `config_cards` (kind "recording") and is
 *   whitelisted: a bad id, a time that is not a time, junk is dropped.
 * - While it is heard the card says how far it is ("Turning speech into text
 *   on this PC: 12 of 40 minutes") and reads the job again every few seconds,
 *   ONLY while the page is on screen, and stops when it ends (G7).
 * - Done: the counts, each item with its time, how many were left out, the
 *   two files, and who made it (the small offline model's words).
 * - D-33.1: when the notes would go to a model off this PC the one main
 *   button is "Write the notes", naming the model and the words; only the
 *   press posts, for THAT model; a changed choice is said, nothing sent.
 * - The words are in VOCABULARY.md; at 390 px every row wraps.
 *
 * Driven through the real ConfigCards and RecordingCard; only the transport
 * and the clock are stand-ins.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
  return { API_BASE: "http://api.test", get: H.get, post: H.post, ApiError, ijToken: () => "tok" };
});

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ConfigCards } from "@/components/chat/ConfigCards";
import { RECORDING_POLL_MS, RecordingCard } from "@/components/chat/RecordingCard";
import { cardId, decodeConfigCards } from "@/lib/configCards";
import {
  RECORDING_SUFFIXES,
  decodeRecordingCard,
  desktopPathOf,
  isRecordingName,
  notesBody,
  offerWords,
  workingWords,
  type RecordingCard as Card,
} from "@/lib/recording";

const DASH = join(__dirname, "..");
const ID = "rec_0123456789abcdef";

function raw(extra: Record<string, unknown> = {}) {
  return {
    kind: "recording",
    recording_id: ID,
    source_name: "meeting.m4a",
    source: "C:\\Work\\meeting.m4a",
    status: "done",
    heard_s: 2412,
    total_s: 2412,
    progress_words: "40 of 40 minutes",
    who: "the offline speech model on this PC",
    on_this_pc: true,
    made_words: "Made on this PC by the small offline speech model. It cannot tell voices apart and may mishear words.",
    transcriber_note: "",
    transcript: "C:\\Work\\meeting transcript.md",
    notes: "C:\\Work\\meeting notes.md",
    words: 6120,
    length_words: "40 min 12 s",
    cut_words: "",
    notes_by: "Ollama on this PC",
    counts: { decisions: 2, you_owe: 1, they_owe: 1, dropped: 2 },
    counts_words: "2 decisions, 1 thing you owe, 1 thing they owe",
    dropped_words: "2 items were left out: their words are not in the recording near the time given.",
    who_owes_words: "Who owes what is read from the words: the speech model cannot tell voices apart.",
    items: {
      decisions: [
        { time: "12:31", seconds: 751, note: "The launch moves to March." },
        { time: "18:02", seconds: 1082, note: "The price stays at forty dollars." },
      ],
      you_owe: [{ time: "14:02", seconds: 842, note: "Send the revised plan on Friday." }],
      they_owe: [{ time: "19:40", seconds: 1180, note: "Maria checks the numbers." }],
    },
    offer: null,
    notes_why: "",
    error: "",
    ...extra,
  };
}

const card = (extra: Record<string, unknown> = {}) => decodeRecordingCard(raw(extra)) as Card;

beforeEach(() => {
  H.get.mockReset();
  H.post.mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the wire", () => {
  it("rides config_cards, whitelisted", () => {
    const got = decodeConfigCards([raw(), { ...raw(), recording_id: "../x" }, { kind: "recording" }]);
    expect(got).toHaveLength(1);
    expect(cardId(got[0])).toBe(`recording:${ID}`);
    const c = decodeRecordingCard(raw({ items: { decisions: [{ time: "soon", note: "x" }, { time: "1:02:03", note: "y" }] } }));
    expect(c?.items.decisions.map((i) => i.time)).toEqual(["1:02:03"]);
    expect(decodeRecordingCard(raw({ status: "whatever" }))?.status).toBe("failed");
    expect(decodeRecordingCard(raw({ offer: { provider: "anthropic", name: "Claude" } }))?.offer).toBeNull();
  });
});

describe("while it is heard", () => {
  it("says how far it is, reads the job again only while on screen, and settles when done", async () => {
    vi.useFakeTimers();
    const settled: Card[] = [];
    const running = card({ status: "hearing", heard_s: 720, total_s: 2400, progress_words: "12 of 40 minutes",
                           transcript: "", notes: "", counts_words: "", dropped_words: "" });
    H.get.mockResolvedValueOnce(raw({ status: "hearing", heard_s: 1500, total_s: 2400, progress_words: "25 of 40 minutes" }));
    H.get.mockResolvedValueOnce(raw());
    render(<RecordingCard card={running} onSettle={(c) => settled.push(c)} />);
    expect(screen.getByText("Turning speech into text on this PC: 12 of 40 minutes")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("30");
    expect(H.get).not.toHaveBeenCalled();

    // Off screen: nothing is read.
    const vis = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECORDING_POLL_MS * 3);
    });
    expect(H.get).not.toHaveBeenCalled();
    vis.mockReturnValue("visible");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECORDING_POLL_MS);
    });
    expect(H.get).toHaveBeenCalledWith(`/documents/recording/${ID}`);
    expect(screen.getByText("Turning speech into text on this PC: 25 of 40 minutes")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECORDING_POLL_MS);
    });
    expect(screen.getByTestId("recording-counts").textContent).toBe("2 decisions, 1 thing you owe, 1 thing they owe");
    expect(settled.map((c) => c.status)).toEqual(["done"]);
    // Ended: no more reads.
    const calls = H.get.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECORDING_POLL_MS * 5);
    });
    expect(H.get.mock.calls.length).toBe(calls);
    vis.mockRestore();
  });

  it("names the speech server when it is the one hearing it", () => {
    const c = card({ status: "hearing", on_this_pc: false, who: "the speech server at 192.168.1.20:9000",
                     progress_words: "3 of 40 minutes" });
    expect(workingWords(c)).toBe("Turning speech into text with the speech server at 192.168.1.20:9000: 3 of 40 minutes");
  });
});

describe("done", () => {
  it("shows each item with its time, what was left out, the files and who made it", () => {
    render(<ConfigCards cards={decodeConfigCards([raw()])} onSettle={() => {}} />);
    const notes = screen.getByTestId("recording-notes");
    expect(notes.textContent).toContain("Decisions");
    expect(notes.textContent).toContain("[12:31]");
    expect(notes.textContent).toContain("The launch moves to March.");
    expect(notes.textContent).toContain("Things you owe");
    expect(notes.textContent).toContain("Things they owe");
    expect(screen.getByTestId("recording-dropped").textContent).toContain("2 items were left out");
    expect(screen.getByText("meeting transcript.md")).toBeTruthy();
    expect(screen.getByText("meeting notes.md")).toBeTruthy();
    expect(screen.getByText(/small offline speech model/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Write the notes" })).toBeNull();
  });

  it("a failure is said in its own words", () => {
    render(<RecordingCard card={card({ status: "failed", error: "meeting.m4a has no sound in it, so there was nothing to turn into text." })} onSettle={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("has no sound in it");
  });
});

describe("D-33.1: the notes go to a model off this PC only on the press", () => {
  const offer = { provider: "anthropic", model: null, name: "Claude", words: 6120 };

  it("names the model and the words, and only the press sends, for that model", async () => {
    const asking = card({ status: "ask", offer, notes: "", counts_words: "", dropped_words: "" });
    expect(offerWords(asking.offer!)).toBe(
      "Write the notes with Claude? The transcript (6,120 words) is sent to it. Nothing has been sent yet.",
    );
    H.post.mockResolvedValueOnce({ ok: true, card: raw({ status: "notes", notes: "", notes_by: "Claude" }) });
    render(<RecordingCard card={asking} onSettle={() => {}} scope={() => ({ threadId: "thr_1", localOnly: false })} />);
    expect(H.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Write the notes" }));
    await waitFor(() => expect(H.post).toHaveBeenCalledTimes(1));
    expect(H.post).toHaveBeenCalledWith(`/documents/recording/${ID}/notes`, {
      provider: "anthropic", model: null, thread_id: "thr_1", local_only: false,
    });
    await waitFor(() => expect(screen.getByText("Writing the notes with Claude…")).toBeTruthy());
  });

  it("a changed choice is said, and nothing was sent", async () => {
    const asking = card({ status: "ask", offer });
    H.post.mockResolvedValueOnce({ ok: false, changed: true,
      card: raw({ status: "ask", offer: { provider: "openai", model: null, name: "ChatGPT", words: 6120 } }) });
    render(<RecordingCard card={asking} onSettle={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Write the notes" }));
    await waitFor(() => expect(screen.getByText("Nothing was sent. The notes would now be written with ChatGPT.")).toBeTruthy());
  });

  it("the press carries the chat's Local only as it is at the press", () => {
    const asking = card({ status: "ask", offer });
    expect(notesBody(asking, { threadId: "t", localOnly: true }).local_only).toBe(true);
    expect(notesBody(asking, null)).toEqual({ provider: "anthropic", model: null, thread_id: "", local_only: false });
  });
});

describe("a recording too large to upload is read where it is", () => {
  it("the suffixes are the daemon's own", () => {
    const py = readFileSync(join(DASH, "..", "src", "iron_jarvis", "documents", "recording.py"), "utf-8");
    const flat = py.replace(/\r?\n/g, " ");
    const set = (name: string) => {
      const at = flat.indexOf(`${name}: frozenset[str] = frozenset({`);
      const body = at < 0 ? "" : flat.slice(at, flat.indexOf("})", at));
      return (body.match(/"\.[a-z0-9]+"/g) ?? []).map((x) => x.slice(1, -1));
    };
    const want = [...set("AUDIO_SUFFIXES"), ...set("VIDEO_SUFFIXES")].sort();
    expect(want.length).toBeGreaterThan(10);
    expect([...RECORDING_SUFFIXES].sort()).toEqual(want);
    expect(isRecordingName("Board Meeting.M4A")).toBe(true);
    expect(isRecordingName("notes.pdf")).toBe(false);
  });
  it("the desktop app says where the file is; a browser says nothing", () => {
    const file = new File(["x"], "meeting.m4a");
    expect(desktopPathOf(file)).toBe("");
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { pathForFile: (f: File) => `C:/Rec/${f.name}` };
    try {
      expect(desktopPathOf(file)).toBe("C:/Rec/meeting.m4a");
    } finally {
      delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
    }
  });
  it("the chat attaches such a recording by its path, with the uploads, never copied", () => {
    const page = readFileSync(join(DASH, "app", "chat", "page.tsx"), "utf-8");
    expect(page).toContain("isRecordingName(f.name) ? desktopPathOf(f) : \"\"");
    expect(page).toContain("...uploaded.filter((u): u is UploadedFile => !!u), ...inPlace]");
  });
});

describe("calm", () => {
  it("the new words are in VOCABULARY.md", () => {
    const vocab = readFileSync(join(DASH, "..", "VOCABULARY.md"), "utf-8");
    for (const w of ["Turning speech into text on this PC", "Things you owe", "Things they owe", "Write the notes",
                     "Recordings: who turns speech into text"]) {
      expect(vocab).toContain(w);
    }
  });
  it("one main button, and nothing wider than a phone", () => {
    const src = readFileSync(join(DASH, "components", "chat", "RecordingCard.tsx"), "utf-8");
    expect(src.match(/variant="primary"/g)).toHaveLength(1);
    expect(src).not.toMatch(/\bw-\[\d{3,}px\]|\bmin-w-\[(?:[4-9]\d\d|\d{4,})px\]/);
    expect(src).toContain("flex-wrap");
  });
  it("a long file name wraps instead of widening the card", () => {
    const long = "Quarterly_Board_Meeting_Recording_From_The_Conference_Room_Phone.m4a";
    render(<RecordingCard card={card({ source_name: long })} onSettle={() => {}} />);
    const name = screen.getByText(long);
    expect(name.className).toContain("[overflow-wrap:anywhere]");
    expect(name.className).toContain("min-w-0");
  });
});
