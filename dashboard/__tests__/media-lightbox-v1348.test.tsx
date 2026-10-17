/**
 * v1.348.0 polish wave A, lane L4 — thumbnails + lightbox (ideas from 21st.dev
 * agent-elements, MIT: the Input Bar's picture thumbnails, and a lightbox).
 * The chat's look stays exactly as it was; pictures simply get better:
 *
 *  - a picture in a reply (a local file, the daemon's "Your image is ready"
 *    line, a web picture AFTER its "Load image" press) is a real button with
 *    a zoom-in cursor; pressing it opens the shared <Modal> full screen with
 *    the picture contained to the window, its name (and its alt words when
 *    they say something), a Close button, Escape, and Open (a local file
 *    through POST /documents/open like the files rail; a web picture in a
 *    new tab). Left/Right walk the pictures of the SAME message only;
 *  - video and audio keep their native players and never open it;
 *  - an attached picture shows a small square thumbnail in its composer chip
 *    (through the same local-media URL helper the replies use); other files
 *    keep the paperclip; the chip keeps its name, size and Remove button.
 *
 * Driven through the real Markdown renderer (real react-markdown) and the real
 * chat page with only the transport mocked.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[], replies: [] as string[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "tok-1",
  get: async (p: string) => {
    const r = H.api.getResponses[p];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${p}`, 404);
    return r;
  },
  post: async (p: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path: p, body });
    const r = H.api.postResponses[p];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (p: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(p);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      const reply = H.stream.replies.shift() ?? "done";
      onDelta(reply, reply);
      return { reply };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";
import { Markdown } from "@/components/Markdown";
import {
  altCaption,
  isThumbImage,
  localMediaSrc,
  mediaName,
  openWayFor,
  stepIndex,
} from "@/lib/mediaLightbox";

const PNG = "C:/Users/x/media/pixio/out.png";
const SRC = (p: string) =>
  `http://127.0.0.1:8787/creative/file-by-path?path=${encodeURIComponent(p)}&token=tok-1`;

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const dialog = () => screen.queryByRole("dialog");
const zoomButtons = () => screen.queryAllByTestId("zoomable-image");

describe("the pure helpers", () => {
  it("only png / jpg / jpeg / gif / webp get a thumbnail", () => {
    for (const yes of ["a.png", "b.JPG", "c.jpeg", "C:\\x\\d.gif", "/u/e.webp"]) {
      expect(isThumbImage(yes), yes).toBe(true);
    }
    for (const no of ["a.pdf", "b.mp4", "c.svg", "d.bmp", "e.png.txt", "", "png"]) {
      expect(isThumbImage(no), no).toBe(false);
    }
  });

  it("a local path loads through the daemon's guarded media route with the token", () => {
    expect(localMediaSrc(PNG)).toBe(SRC(PNG));
    expect(localMediaSrc("C:\\a b\\c.png")).toBe(
      `http://127.0.0.1:8787/creative/file-by-path?path=${encodeURIComponent("C:\\a b\\c.png")}&token=tok-1`,
    );
  });

  it("a picture is called by its file name; generic alt words add nothing", () => {
    expect(mediaName("C:\\Users\\x\\out.png")).toBe("out.png");
    expect(mediaName("file:///C:/x/y.png")).toBe("y.png");
    expect(mediaName("https://cdn.example/a/b%20c.png?x=1")).toBe("b c.png");
    expect(mediaName("data:image/png;base64,AAAA")).toBe("Picture");
    expect(altCaption("image", "out.png")).toBe("");
    expect(altCaption("generated media", "out.png")).toBe("");
    expect(altCaption("OUT.png", "out.png")).toBe("");
    expect(altCaption("out", "out.png")).toBe(""); // the name again, minus .png
    expect(altCaption("a red bicycle", "out.png")).toBe("a red bicycle");
  });

  it("Left/Right stop at the ends; Open knows a file from a web picture", () => {
    expect(stepIndex(0, 3, -1)).toBe(0);
    expect(stepIndex(0, 3, 1)).toBe(1);
    expect(stepIndex(2, 3, 1)).toBe(2);
    expect(openWayFor(PNG)).toEqual({ kind: "file", path: PNG });
    expect(openWayFor("file:///C:/x/y.png")).toEqual({ kind: "file", path: "/C:/x/y.png" });
    expect(openWayFor("https://cdn.example/a.png")).toEqual({ kind: "web", href: "https://cdn.example/a.png" });
    expect(openWayFor("data:image/png;base64,AAAA")).toBeNull();
  });
});

describe("a picture in a reply opens full screen", () => {
  it("looks exactly as before, but is a real button with a zoom-in cursor", () => {
    render(<Markdown content={`![out](${PNG})`} />);
    const img = document.querySelector("img")!;
    expect(img.getAttribute("src")).toBe(SRC(PNG));
    // The picture's own look is unchanged (the margin moved to the button).
    for (const cls of ["max-h-96", "w-auto", "max-w-full", "rounded-xl", "border", "border-white/10"]) {
      expect(img.classList.contains(cls), cls).toBe(true);
    }
    const btn = img.closest("button")!;
    expect(btn).not.toBeNull();
    expect(btn.getAttribute("type")).toBe("button");
    expect(btn.className).toContain("cursor-zoom-in");
    expect(btn.className).toContain("my-2");
    // Quiet: the button adds no box of its own.
    expect(btn.className).not.toMatch(/(^|\s)(border|shadow|bg-)[^\s]*/);
    expect(btn.getAttribute("aria-label")).toBe("View out.png full screen");
    expect(dialog()).toBeNull(); // nothing opens by itself
  });

  it("a click opens the lightbox: the same picture, its name, Close; focus goes back", async () => {
    render(<Markdown content={`![a red bicycle](${PNG})`} />);
    const btn = zoomButtons()[0];
    btn.focus();
    fireEvent.click(btn);
    const box = await screen.findByRole("dialog", { name: "Picture: a red bicycle" });
    const pic = within(box).getByTestId("media-lightbox-picture");
    expect(pic.getAttribute("src")).toBe(SRC(PNG));
    expect(pic.className).toContain("object-contain"); // contained to the window
    expect(within(box).getByTestId("media-lightbox-name").textContent).toBe("out.png");
    expect(within(box).getByTestId("media-lightbox-alt").textContent).toBe("a red bicycle");
    expect(within(box).queryByTestId("media-lightbox-count")).toBeNull(); // one picture: no count, no arrows
    expect(within(box).queryByRole("button", { name: "Next picture" })).toBeNull();
    // Close is where focus lands, and it closes.
    const close = within(box).getByRole("button", { name: "Close" });
    await waitFor(() => expect(document.activeElement).toBe(close));
    fireEvent.click(close);
    await waitFor(() => expect(dialog()).toBeNull());
    expect(document.activeElement).toBe(btn);
  });

  it("Escape closes it", async () => {
    render(<Markdown content={`![x](${PNG})`} />);
    fireEvent.click(zoomButtons()[0]);
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(dialog()).toBeNull());
  });

  it("the daemon's media line (an angle-bracket path with a space) opens by its file name", async () => {
    const p = "C:/Users/x/My Pictures/mj_1.png";
    render(<Markdown content={`Your image is ready.\n\n![image](<${p}>)`} />);
    const btn = zoomButtons()[0];
    expect(btn.getAttribute("aria-label")).toBe("View mj_1.png full screen");
    fireEvent.click(btn);
    const box = await screen.findByRole("dialog");
    expect(within(box).getByTestId("media-lightbox-picture").getAttribute("src")).toBe(SRC(p));
    expect(within(box).getByTestId("media-lightbox-name").textContent).toBe("mj_1.png");
    // "image" says nothing the name does not: no second line.
    expect(within(box).queryByTestId("media-lightbox-alt")).toBeNull();
  });

  it("Left/Right walk the pictures of the SAME message, stopping at the ends", async () => {
    const [a, b, c] = ["C:/m/a.png", "C:/m/b.jpg", "C:/m/c.webp"];
    render(<Markdown content={`one ![](${a})\n\ntwo ![](${b})\n\nthree ![](${c})`} />);
    expect(zoomButtons()).toHaveLength(3);
    fireEvent.click(zoomButtons()[1]);
    const box = await screen.findByRole("dialog");
    const count = () => within(box).getByTestId("media-lightbox-count").textContent;
    const shown = () => within(box).getByTestId("media-lightbox-picture").getAttribute("src");
    expect(count()).toBe("2 of 3");
    expect(shown()).toBe(SRC(b));
    fireEvent.keyDown(window, { key: "ArrowRight" });
    await waitFor(() => expect(count()).toBe("3 of 3"));
    expect(shown()).toBe(SRC(c));
    expect((within(box).getByRole("button", { name: "Next picture" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(window, { key: "ArrowRight" }); // the end: stays
    expect(count()).toBe("3 of 3");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    await waitFor(() => expect(count()).toBe("1 of 3"));
    expect(shown()).toBe(SRC(a));
    expect((within(box).getByRole("button", { name: "Previous picture" }) as HTMLButtonElement).disabled).toBe(true);
    // The buttons do the same as the keys.
    fireEvent.click(within(box).getByRole("button", { name: "Next picture" }));
    await waitFor(() => expect(count()).toBe("2 of 3"));
    // A modified arrow is the app's, never the lightbox's.
    fireEvent.keyDown(window, { key: "ArrowRight", altKey: true });
    expect(count()).toBe("2 of 3");
  });

  it("another message's pictures are not in this one's lightbox", async () => {
    render(
      <div>
        <Markdown content={"![](C:/m/first.png)"} />
        <Markdown content={"![](C:/m/second.png)"} />
      </div>,
    );
    expect(zoomButtons()).toHaveLength(2);
    fireEvent.click(zoomButtons()[0]);
    const box = await screen.findByRole("dialog");
    expect(within(box).queryByTestId("media-lightbox-count")).toBeNull();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(within(box).getByTestId("media-lightbox-name").textContent).toBe("first.png");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("Open opens the local file the way the files rail does, and says so when it cannot", async () => {
    render(<Markdown content={`![x](${PNG})`} />);
    fireEvent.click(zoomButtons()[0]);
    const box = await screen.findByRole("dialog");
    const open = within(box).getByTestId("media-lightbox-open");
    expect(open.tagName).toBe("BUTTON");
    expect(open.textContent).toBe("Open");
    fireEvent.click(open);
    await waitFor(() => expect(H.api.posts).toHaveLength(1));
    expect(H.api.posts[0]).toEqual({ path: "/documents/open", body: { path: PNG } });
    expect(within(box).queryByRole("alert")).toBeNull();
    // A refusal is said, never a pretend launch.
    H.api.postResponses["/documents/open"] = new H.FakeApiError("blocked: protected path", 403);
    fireEvent.click(within(box).getByTestId("media-lightbox-open"));
    const alert = await within(box).findByRole("alert");
    expect(alert.textContent).toContain("blocked: protected path");
  });

  it("a web picture loads only after its press; then it zooms, and Open is a new-tab link", async () => {
    const url = "https://cdn.example/pics/cat.png";
    render(<Markdown content={`![a cat](${url})`} />);
    expect(document.querySelector("img")).toBeNull(); // never auto-loaded
    expect(zoomButtons()).toHaveLength(0);
    fireEvent.click(screen.getByTestId("remote-media-gate"));
    expect(document.querySelector("img")?.getAttribute("src")).toBe(url);
    fireEvent.click(zoomButtons()[0]);
    const box = await screen.findByRole("dialog");
    expect(within(box).getByTestId("media-lightbox-picture").getAttribute("src")).toBe(url);
    expect(within(box).getByTestId("media-lightbox-name").textContent).toBe("cat.png");
    const open = within(box).getByTestId("media-lightbox-open") as HTMLAnchorElement;
    expect(open.tagName).toBe("A");
    expect(open.getAttribute("href")).toBe(url);
    expect(open.getAttribute("target")).toBe("_blank");
    expect(open.getAttribute("rel")).toContain("noreferrer");
    fireEvent.click(open);
    expect(H.api.posts).toHaveLength(0); // a web picture is never sent to /documents/open
  });

  it("video and audio keep their native players and never open the lightbox", () => {
    render(<Markdown content={"![v](C:/m/clip.mp4)\n\n![s](C:/m/song.mp3)"} />);
    const video = document.querySelector("video")!;
    const audio = document.querySelector("audio")!;
    expect(video.hasAttribute("controls")).toBe(true);
    expect(audio.hasAttribute("controls")).toBe(true);
    expect(video.closest("button")).toBeNull();
    expect(audio.closest("button")).toBeNull();
    expect(zoomButtons()).toHaveLength(0);
    fireEvent.click(video);
    expect(dialog()).toBeNull();
  });

  it("a picture in a reply on the chat page opens full screen", async () => {
    const p = "C:/Users/x/media/pixio/mj_7.png";
    H.stream.replies.push(`Your image is ready.\n\n![image](<${p}>)`);
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "make a picture of a lighthouse" } });
    fireEvent.keyDown(box, { key: "Enter" });
    const btn = await screen.findByRole("button", { name: "View mj_7.png full screen" });
    expect(btn.querySelector("img")?.getAttribute("src")).toBe(SRC(p));
    fireEvent.click(btn);
    const dlg = await screen.findByRole("dialog", { name: "Picture: mj_7.png" });
    expect(within(dlg).getByTestId("media-lightbox-picture").getAttribute("src")).toBe(SRC(p));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Picture: mj_7.png" })).toBeNull());
  });
});

describe("an attached picture shows a thumbnail in its composer chip", () => {
  const UPLOADS = "C:\\home\\uploads";
  function uploadsAttachAsIs() {
    H.api.postResponses["/documents/workfolder"] = new H.FakeApiError("no folder", 503);
    H.api.postResponses["/documents/upload"] = (body: Record<string, unknown>) => {
      const name = String(body.filename);
      return { name, path: `${UPLOADS}\\${name}` };
    };
  }
  async function attach(...files: Array<[string, string]>) {
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: files.map(([n, type]) => new File(["abc"], n, { type })) },
    });
  }
  const chipFor = (name: string) =>
    screen.getAllByTestId("attachment-chip").find((c) => c.textContent?.includes(name))!;

  it("a picture gets a small square thumbnail through the local-media route; a PDF keeps the paperclip", async () => {
    uploadsAttachAsIs();
    render(<ChatPage />);
    await attach(["shot.png", "image/png"], ["notes.pdf", "application/pdf"]);
    await waitFor(() => expect(screen.getAllByTestId("attachment-chip")).toHaveLength(2));

    const pic = chipFor("shot.png");
    const thumb = within(pic).getByTestId("attachment-thumb");
    expect(thumb.tagName).toBe("BUTTON");
    expect(thumb.getAttribute("aria-label")).toBe("View shot.png full screen");
    const img = thumb.querySelector("img")!;
    expect(img.getAttribute("src")).toBe(SRC(`${UPLOADS}\\shot.png`));
    // Small and square, cropped to fill.
    for (const cls of ["h-5", "w-5", "object-cover"]) expect(img.classList.contains(cls), cls).toBe(true);
    // The chip keeps its words and its Remove button.
    expect(pic.textContent).toContain("shot.png");
    expect(within(pic).getByRole("button", { name: "Remove shot.png" })).toBeTruthy();

    const doc = chipFor("notes.pdf");
    expect(within(doc).queryByTestId("attachment-thumb")).toBeNull();
    expect(doc.querySelector("img")).toBeNull();
    expect(doc.querySelector("svg")).not.toBeNull(); // the paperclip
    expect(within(doc).getByRole("button", { name: "Remove notes.pdf" })).toBeTruthy();

    // Remove still removes (the thumbnail goes with its chip).
    fireEvent.click(within(pic).getByRole("button", { name: "Remove shot.png" }));
    await waitFor(() => expect(screen.getAllByTestId("attachment-chip")).toHaveLength(1));
    expect(screen.queryByTestId("attachment-thumb")).toBeNull();
  });

  it("the thumbnail opens the picture full screen; Left/Right walk the attached pictures", async () => {
    uploadsAttachAsIs();
    render(<ChatPage />);
    await attach(["a.jpg", "image/jpeg"], ["b.webp", "image/webp"]);
    await waitFor(() => expect(screen.getAllByTestId("attachment-thumb")).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId("attachment-thumb")[0]);
    const box = await screen.findByRole("dialog", { name: "Picture: a.jpg" });
    expect(within(box).getByTestId("media-lightbox-count").textContent).toBe("1 of 2");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    await waitFor(() => expect(within(box).getByTestId("media-lightbox-name").textContent).toBe("b.webp"));
    expect(within(box).getByTestId("media-lightbox-picture").getAttribute("src")).toBe(
      SRC(`${UPLOADS}\\b.webp`),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(dialog()).toBeNull());
  });

  it("a thumbnail that will not load falls back to the paperclip; the chip stays", async () => {
    uploadsAttachAsIs();
    render(<ChatPage />);
    await attach(["gone.png", "image/png"]);
    const thumb = await screen.findByTestId("attachment-thumb");
    await act(async () => {
      fireEvent.error(thumb.querySelector("img")!);
    });
    await waitFor(() => expect(screen.queryByTestId("attachment-thumb")).toBeNull());
    const chip = chipFor("gone.png");
    expect(chip.querySelector("svg")).not.toBeNull();
    expect(within(chip).getByRole("button", { name: "Remove gone.png" })).toBeTruthy();
  });
});

describe("source guards for the new files", () => {
  const read = (rel: string) =>
    readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
  const FILES = ["components/MediaLightbox.tsx", "lib/mediaLightbox.ts"];

  it("theme tokens only, whole pixels only", () => {
    for (const f of FILES) {
      const src = read(f);
      expect(src.length, f).toBeGreaterThan(500); // anti-vacuity: the file was read
      expect(src.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [], f).toEqual([]);
      expect(src.match(/\b(rgb|rgba|hsl|hsla)\(/g) ?? [], f).toEqual([]);
      expect(src.match(/text-\[(?:\d*\.\d+(?:px|em|rem)|[\d.]+em)\]/g) ?? [], f).toEqual([]);
      // No literal Tailwind hues: tone-* and the zinc/ink/accent scale only.
      expect(
        src.match(/\b(?:text|bg|border|ring)-(?:red|rose|amber|emerald|green|sky|blue|violet|yellow|orange|cyan)-\d{2,3}\b/g) ?? [],
        f,
      ).toEqual([]);
    }
  });

  it("every animation and transition stops under reduced motion", () => {
    const src = read("components/MediaLightbox.tsx");
    const classes = src.match(/"[^"\n]*\b(?:animate|transition)-[^"\n]*"/g) ?? [];
    expect(classes.length).toBeGreaterThan(0);
    for (const c of classes) {
      if (/\banimate-/.test(c)) expect(c).toContain("motion-reduce:animate-none");
      if (/\btransition-/.test(c)) expect(c).toContain("motion-reduce:transition-none");
    }
  });
});
