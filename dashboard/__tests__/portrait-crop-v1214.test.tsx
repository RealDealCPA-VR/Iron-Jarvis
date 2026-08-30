import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * PORTRAITS, FOR EVERY AGENT, CROPPED BY THE USER (v1.214.0).
 *
 * Reported: "every agent should be customizable including the predefined
 * agents … with the ability for the user to choose a custom image for any of
 * the agents. The agent images must either conform to a size suitable or
 * require user cropping."
 *
 * TWO THINGS WERE WRONG, and they are different in kind:
 *
 *   1. A portrait was something only an agent the user had CREATED could have.
 *      The daemon never had that limit — storage is `avatars/<slug>.png` keyed
 *      by the bare name and `POST /agents/{name}/avatar` says in its own
 *      comment that it "works for BUILTIN names and dynamic slugs alike". The
 *      restriction was that the Upload/Generate/Remove row was written inside
 *      `DynamicRow`. `AgentPortrait` is that row taking a NAME.
 *
 *   2. A picked file was posted as-is, and the daemon's `_normalize_avatar_png`
 *      calls `Image.thumbnail`, which PRESERVES aspect ratio. So a 1600×900
 *      photo was stored 512×288 and then center-cropped by CSS at render time
 *      (`rounded-full object-cover`), because every surface draws a portrait in
 *      a circle. The crop happened either way; the user just never saw it and
 *      could not move it. Now they choose it, and what is stored is already the
 *      square that will be drawn.
 *
 * THE GEOMETRY IS TESTED DIRECTLY. `coverScale` / `clampOffset` / `cropRect`
 * are pure functions over numbers precisely so the cropping rules do not have
 * to be inferred from a canvas jsdom does not implement.
 */

const hooks = vi.hoisted(() => ({
  posts: [] as Array<{ path: string; body: unknown }>,
  deletes: [] as string[],
  postFail: null as string | null,
  /** Filled by the mock factory below so a rejection can be a REAL ApiError —
   *  the daemon's message is only shown verbatim on that branch. */
  ApiError: null as unknown as new (m: string, s?: number) => Error,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  hooks.ApiError = ApiError;
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: (path: string) => {
      hooks.deletes.push(path);
      return Promise.resolve({});
    },
    post: (path: string, body: unknown) => {
      if (hooks.postFail) {
        return Promise.reject(new hooks.ApiError(hooks.postFail, 424));
      }
      hooks.posts.push({ path, body });
      return Promise.resolve({});
    },
  };
});

import {
  MAX_ZOOM,
  OUTPUT_PX,
  clampOffset,
  coverScale,
  cropRect,
} from "@/components/agents/PortraitCropper";
import { AgentPortrait } from "@/components/agents/AgentPortrait";

/** The cropper's on-screen square, in CSS px. Its exported helpers all default
 *  to it, so the expectations below are written against that same number. */
const VIEW = 288;

/* ------------------------------------------------------------- geometry --- */

describe("coverScale — the square is never allowed a gutter", () => {
  it("uses the SHORTER edge, so the image always covers", () => {
    // 800×600: the height is the tight one (288/600 = 0.48 > 288/800 = 0.36).
    expect(coverScale(800, 600)).toBeCloseTo(0.48, 10);
    // 600×800: now the width is.
    expect(coverScale(600, 800)).toBeCloseTo(0.48, 10);
    // A square meets the view exactly on both axes.
    expect(coverScale(600, 600)).toBeCloseTo(0.48, 10);
  });

  it("never divides by a bogus dimension", () => {
    // An <img> that failed to decode reports 0×0. Returning 1 keeps the
    // component drawing nothing rather than NaN-ing its transform.
    expect(coverScale(0, 600)).toBe(1);
    expect(coverScale(800, 0)).toBe(1);
    expect(coverScale(Number.NaN, 10)).toBe(1);
  });
});

describe("clampOffset — panning cannot open a hole", () => {
  const dispW = 384; // 800 × 0.48
  const dispH = 288; // 600 × 0.48

  it("pins the axis that exactly fits", () => {
    // At cover scale the short axis has a range of ONE point, which is why a
    // landscape photo cannot be dragged vertically until it is zoomed in.
    expect(clampOffset({ x: -48, y: 40 }, dispW, dispH).y).toBe(0);
    expect(clampOffset({ x: -48, y: -40 }, dispW, dispH).y).toBe(0);
  });

  it("holds the loose axis inside [view - displayed, 0]", () => {
    expect(clampOffset({ x: 25, y: 0 }, dispW, dispH).x).toBe(0);
    expect(clampOffset({ x: -1000, y: 0 }, dispW, dispH).x).toBe(VIEW - dispW);
    expect(clampOffset({ x: -50, y: 0 }, dispW, dispH).x).toBe(-50);
  });
});

describe("cropRect — what actually gets copied out of the original", () => {
  it("takes the centred square of a landscape image at cover scale", () => {
    const scale = coverScale(800, 600);
    const offset = { x: (VIEW - 800 * scale) / 2, y: (VIEW - 600 * scale) / 2 };
    expect(cropRect(800, 600, scale, offset)).toEqual({
      sx: 100, // (800 - 600) / 2 — the middle 600px of the width
      sy: 0,
      sw: 600, // the full height: the square can be no larger
      sh: 600,
    });
  });

  it("takes a SMALLER square as the user zooms in", () => {
    const scale = coverScale(800, 600) * 2;
    const rect = cropRect(800, 600, scale, { x: -48, y: 0 });
    expect(rect.sw).toBeCloseTo(300, 6);
    expect(rect.sh).toBeCloseTo(300, 6);
    expect(rect.sx).toBeCloseTo(50, 6);
  });

  it("never asks for a strip outside the bitmap", () => {
    // Float drift at maximum zoom would otherwise hand `drawImage` a rect that
    // starts past the right edge, which some engines render as a transparent
    // band down the portrait.
    const scale = coverScale(800, 600);
    const rect = cropRect(800, 600, scale, { x: -100000, y: -100000 });
    expect(rect.sx + rect.sw).toBeLessThanOrEqual(800);
    expect(rect.sy + rect.sh).toBeLessThanOrEqual(600);
    expect(rect.sx).toBeGreaterThanOrEqual(0);
    expect(rect.sy).toBeGreaterThanOrEqual(0);
  });

  it("is always SQUARE, whatever the source shape", () => {
    for (const [w, h] of [
      [1600, 900],
      [900, 1600],
      [512, 512],
      [4000, 100],
    ] as const) {
      const scale = coverScale(w, h);
      const r = cropRect(w, h, scale, { x: 0, y: 0 });
      expect(r.sw).toBeCloseTo(r.sh, 6);
    }
  });
});

/* --------------------------------------------------------- the component --- */

/** A decodable image of a chosen size. jsdom loads nothing, so the decode is
 *  the thing that has to be stood in for — everything downstream of it is the
 *  component's own arithmetic. */
function stubImage(w: number, h: number) {
  class FakeImage {
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    naturalWidth = w;
    naturalHeight = h;
    width = w;
    height = h;
    #src = "";
    get src() {
      return this.#src;
    }
    set src(v: string) {
      this.#src = v;
      queueMicrotask(() => this.onload?.());
    }
  }
  vi.stubGlobal("Image", FakeImage);
}

const drawn: Array<unknown[]> = [];
/** The EXPORT draw only. The live preview also calls `drawImage`, with the
 *  5-argument form (image, dx, dy, dw, dh); the export uses the 9-argument
 *  source-rect form, and that is the call these tests are about. */
const exported = () => drawn.filter((args) => args.length === 9);

beforeEach(() => {
  hooks.posts = [];
  hooks.deletes = [];
  hooks.postFail = null;
  drawn.length = 0;
  stubImage(1600, 900);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: () => "blob:portrait",
    revokeObjectURL: () => {},
  });
  // jsdom implements neither; the cropper only needs them to be callable.
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
    clearRect: () => {},
    drawImage: (...args: unknown[]) => drawn.push(args),
  })) as unknown as HTMLCanvasElement["getContext"];
  HTMLCanvasElement.prototype.toDataURL = vi.fn(
    () => "data:image/png;base64,QUJD",
  ) as unknown as HTMLCanvasElement["toDataURL"];
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const pngFile = (name = "face.png", bytes = 4) =>
  new File([new Uint8Array(bytes)], name, { type: "image/png" });

function pick(agent: string, file: File) {
  const input = screen.getByLabelText(
    `Upload a portrait for ${agent}`,
  ) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("AgentPortrait — the same row for every kind of agent", () => {
  it("stores under the NAME it is given, built-in or not", async () => {
    // The whole point: `builder` is a built-in and the route is identical.
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    await waitFor(() => expect(screen.getByTestId("portrait-cropper")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Use this" }));
    await waitFor(() =>
      expect(hooks.posts).toEqual([
        { path: "/agents/builder/avatar", body: { image_b64: "QUJD" } },
      ]),
    );
  });

  it("exports at the daemon's own stored size, so nothing is resized twice", async () => {
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    await waitFor(() => expect(screen.getByTestId("portrait-cropper")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Use this" }));
    await waitFor(() => expect(exported()).toHaveLength(1));
    // drawImage(img, sx, sy, sw, sh, 0, 0, OUTPUT, OUTPUT)
    const [, , , sw, sh, dx, dy, dw, dh] = exported()[0] as number[];
    expect(sw).toBeCloseTo(sh, 6); // a square out of a 1600×900 source
    expect([dx, dy, dw, dh]).toEqual([0, 0, OUTPUT_PX, OUTPUT_PX]);
    // OUTPUT_PX IS the daemon's `_AVATAR_MAX_DIM`, so its `thumbnail` is a
    // no-op and only ONE place decides what the picture looks like.
    expect(OUTPUT_PX).toBe(512);
  });

  it("refuses an oversized pick before decoding anything", async () => {
    render(<AgentPortrait name="analyst" onChanged={vi.fn()} />);
    pick("analyst", pngFile("huge.png", 2 * 1024 * 1024 + 1));
    await waitFor(() =>
      expect(screen.getByText("portrait too large — 2 MB max")).toBeTruthy(),
    );
    // No cropper, no POST — a 40MB photo must not be read into a canvas first.
    expect(screen.queryByTestId("portrait-cropper")).toBeNull();
    expect(hooks.posts).toHaveLength(0);
  });

  it("Generate and Remove do NOT go through the cropper", async () => {
    // There is nothing to frame: generation is square by construction (the
    // daemon's prompt asks for a square avatar) and removal has no image at
    // all. Sending either through a crop dialog would be ceremony.
    const onChanged = vi.fn();
    render(
      <AgentPortrait
        name="analyst"
        avatar="/agents/analyst/avatar"
        onChanged={onChanged}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));
    await waitFor(() =>
      expect(hooks.posts).toEqual([
        { path: "/agents/analyst/avatar", body: { generate: true } },
      ]),
    );
    expect(screen.queryByTestId("portrait-cropper")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Remove/ }));
    await waitFor(() =>
      expect(hooks.deletes).toEqual(["/agents/analyst/avatar"]),
    );
  });

  it("Remove is offered only once a portrait exists", () => {
    const { rerender } = render(<AgentPortrait name="analyst" onChanged={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    rerender(
      <AgentPortrait
        name="analyst"
        avatar="/agents/analyst/avatar"
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /Remove/ })).toBeTruthy();
  });

  it("a failed store never claims a portrait landed", async () => {
    hooks.postFail = "boom";
    const onChanged = vi.fn();
    render(<AgentPortrait name="builder" onChanged={onChanged} />);
    pick("builder", pngFile());
    await waitFor(() => expect(screen.getByTestId("portrait-cropper")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Use this" }));
    await waitFor(() => expect(screen.getByText("boom")).toBeTruthy());
    // Whether one EXISTS is daemon truth, so the row must not refetch on a
    // failure and must not draw one either (the v1.171.0 rule).
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
  });
});

describe("PortraitCropper — the user sees the square before it is stored", () => {
  it("says what will happen to a non-square picture", async () => {
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    await waitFor(() =>
      expect(screen.getByText(/1600×900 · will be cropped square/)).toBeTruthy(),
    );
  });

  it("says nothing alarming about one that is already square", async () => {
    stubImage(512, 512);
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    await waitFor(() =>
      expect(screen.getByText(/512×512 · already square/)).toBeTruthy(),
    );
  });

  it("cannot be confirmed before the image has decoded", () => {
    // `Image` left un-stubbed: jsdom never fires load, so there is no geometry
    // and "Use this" would export an empty canvas.
    vi.stubGlobal(
      "Image",
      class {
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        naturalWidth = 0;
        naturalHeight = 0;
        src = "";
      },
    );
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    const button = screen.getByRole("button", { name: "Use this" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("offers a zoom that starts at cover and only goes IN", async () => {
    // Zooming out past cover is the one move that could open a transparent
    // gutter, so the control cannot express it at all.
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    await waitFor(() => expect(screen.getByTestId("portrait-cropper")).toBeTruthy());
    const zoom = screen.getByLabelText(/Zoom builder/) as HTMLInputElement;
    expect(zoom.min).toBe("1");
    expect(zoom.max).toBe(String(MAX_ZOOM));
    expect(zoom.value).toBe("1");
  });

  it("dragging changes which part of the picture is exported", async () => {
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    await waitFor(() => expect(screen.getByTestId("portrait-cropper")).toBeTruthy());
    const canvas = screen.getByTestId("cropper-canvas");
    // jsdom has no pointer capture.
    (canvas as HTMLCanvasElement).setPointerCapture = () => {};
    (canvas as HTMLCanvasElement).releasePointerCapture = () => {};
    fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 40, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Use this" }));
    await waitFor(() => expect(exported()).toHaveLength(1));
    const [, sx] = exported()[0] as number[];
    // Dragged LEFT ⇒ a square further RIGHT in the source than the centred
    // one (which is (1600 - 900) / 2 = 350).
    expect(sx).toBeGreaterThan(350);
  });

  it("an undecodable file says so and stores nothing", async () => {
    vi.stubGlobal(
      "Image",
      class {
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        naturalWidth = 0;
        naturalHeight = 0;
        #src = "";
        get src() {
          return this.#src;
        }
        set src(v: string) {
          this.#src = v;
          queueMicrotask(() => this.onerror?.());
        }
      },
    );
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    await waitFor(() =>
      expect(screen.getByText(/could not be decoded as an image/)).toBeTruthy(),
    );
    expect(hooks.posts).toHaveLength(0);
  });

  it("is itself a portalled dialog, so nothing can clip it either", async () => {
    render(<AgentPortrait name="builder" onChanged={vi.fn()} />);
    pick("builder", pngFile());
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Fit the portrait")).toBeTruthy();
    expect(dialog.parentElement?.parentElement).toBe(document.body);
  });
});
