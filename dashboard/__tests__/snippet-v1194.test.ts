import { describe, it, expect } from "vitest";
import {
  CLI_IMAGE_BUDGET_BYTES,
  MAX_EDGE,
  MAX_SOURCE_BYTES,
  QUALITY_LADDER,
  encodeAttempts,
  imageFilesFromDrop,
  imageItemsFromPaste,
  hasImagePayload,
  shrinkToFit,
  type DecodedImage,
  type ShrinkDeps,
} from "@/lib/snippet";

// jsdom has neither createImageBitmap nor a canvas encoder, which is exactly
// why lib/snippet.ts takes both as injectable seams.

function sized<T extends Blob>(blob: T, size: number): T {
  Object.defineProperty(blob, "size", { value: size, configurable: true });
  return blob;
}

function fakeFile(name: string, type: string, size: number): File {
  return sized(new File([new Uint8Array(1)], name, { type }), size);
}

function fakeBlob(size: number, type = "image/jpeg"): Blob {
  return sized(new Blob([new Uint8Array(1)], { type }), size);
}

type Call = { width: number; height: number; quality: number; type: string };

/** Records every encode attempt; `sizeFor` decides which rung finally fits. */
function deps(
  image: { width: number; height: number },
  sizeFor: (c: Call) => number,
): { deps: ShrinkDeps; calls: Call[] } {
  const calls: Call[] = [];
  const decoded: DecodedImage = { ...image, source: {} };
  return {
    calls,
    deps: {
      decode: async () => decoded,
      encode: async (_img, width, height, type, quality) => {
        const call = { width, height, quality, type };
        calls.push(call);
        return fakeBlob(sizeFor(call), type);
      },
    },
  };
}

function item(type: string, file: File | null) {
  return { kind: file ? "file" : "string", type, getAsFile: () => file };
}

describe("paste / drop extraction", () => {
  it("takes an unnamed image/png snip and ignores the text item beside it", () => {
    // A Win+Shift+S snip: image/png, generic name, alongside nothing else.
    const snip = new File([new Uint8Array(4)], "image.png", { type: "image/png" });
    const e = {
      clipboardData: {
        items: [
          { kind: "string", type: "text/plain", getAsFile: () => null },
          item("image/png", snip),
        ],
        files: [],
      },
    } as unknown as ClipboardEvent;

    const files = imageItemsFromPaste(e);
    expect(files).toHaveLength(1);
    // Named by us: "image.png" is what the browser calls EVERY snip, so two
    // snips would collide on disk.
    expect(files[0].name).toMatch(/^snip-\d+-\d+\.png$/);
    expect(files[0].type).toBe("image/png");
    expect(hasImagePayload(e)).toBe(true);
  });

  it("leaves a plain text paste alone", () => {
    const e = {
      clipboardData: {
        items: [{ kind: "string", type: "text/plain", getAsFile: () => null }],
        files: [],
      },
    } as unknown as ClipboardEvent;
    expect(imageItemsFromPaste(e)).toEqual([]);
    expect(hasImagePayload(e)).toBe(false);
  });

  it("gives consecutive snips distinct names", () => {
    const mk = () =>
      ({
        clipboardData: {
          items: [item("image/png", new File([new Uint8Array(4)], "", { type: "image/png" }))],
          files: [],
        },
      }) as unknown as ClipboardEvent;
    const a = imageItemsFromPaste(mk())[0];
    const b = imageItemsFromPaste(mk())[0];
    expect(a.name).not.toBe(b.name);
  });

  it("keeps a real filename on a dropped file and drops non-images", () => {
    const e = {
      dataTransfer: {
        files: [
          new File([new Uint8Array(2)], "screenshot-of-bug.png", { type: "image/png" }),
          new File([new Uint8Array(2)], "notes.txt", { type: "text/plain" }),
        ],
        items: [],
      },
    } as unknown as DragEvent;
    const files = imageFilesFromDrop(e);
    expect(files.map((f) => f.name)).toEqual(["screenshot-of-bug.png"]);
  });
});

describe("shrinkToFit", () => {
  it("passes an in-budget PNG through UNTOUCHED and says it was not recompressed", async () => {
    const f = fakeFile("small.png", "image/png", 900_000);
    const { deps: d, calls } = deps({ width: 1200, height: 800 }, () => 1);
    const r = await shrinkToFit(f, CLI_IMAGE_BUDGET_BYTES, d);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.file).toBe(f); // same object: bytes are the original
    expect(r.recompressed).toBe(false);
    expect(r.width).toBeNull(); // never decoded, so never guessed
    expect(calls).toHaveLength(0);
  });

  it("brings a 6MB snip back UNDER budget", async () => {
    const f = fakeFile("snip.png", "image/png", 6_000_000);
    // Bytes fall with quality; the top rung still overflows.
    const { deps: d } = deps({ width: 2560, height: 1440 }, (c) =>
      Math.round(6_000_000 * c.quality * (c.width / 2048)),
    );
    const r = await shrinkToFit(f, CLI_IMAGE_BUDGET_BYTES, d);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.bytes).toBeLessThanOrEqual(CLI_IMAGE_BUDGET_BYTES);
    expect(r.recompressed).toBe(true);
    expect(r.originalBytes).toBe(6_000_000);
    expect(r.file.type).toBe("image/jpeg");
  });

  it("spends the QUALITY ladder before it sacrifices any resolution", async () => {
    const f = fakeFile("snip.png", "image/png", 6_000_000);
    // Nothing at full size fits; the first downscale rung does.
    const { deps: d, calls } = deps({ width: 2560, height: 1440 }, (c) =>
      c.width === MAX_EDGE ? 9_000_000 : 1_000_000,
    );
    const r = await shrinkToFit(f, CLI_IMAGE_BUDGET_BYTES, d);
    expect(r.ok).toBe(true);
    if (!r.ok) return;

    // THE ORDER IS THE WHOLE ARGUMENT: four full-size attempts, one per quality
    // rung, in descending order, all at the same pixel size — and only then a
    // smaller image.
    expect(calls.slice(0, 4).map((c) => c.quality)).toEqual([...QUALITY_LADDER]);
    expect(new Set(calls.slice(0, 4).map((c) => c.width))).toEqual(new Set([MAX_EDGE]));
    expect(calls[4].width).toBeLessThan(calls[0].width);
    expect(calls[4].quality).toBe(QUALITY_LADDER[0]); // the ladder restarts at the top
    expect(r.scale).toBeLessThan(1);
    expect(r.quality).toBe(QUALITY_LADDER[0]);
  });

  it("stops at the first rung that fits, without downscaling at all", async () => {
    const f = fakeFile("snip.png", "image/png", 6_000_000);
    const { deps: d, calls } = deps({ width: 2560, height: 1440 }, (c) =>
      c.quality >= 0.9 ? 9_000_000 : 2_000_000,
    );
    const r = await shrinkToFit(f, CLI_IMAGE_BUDGET_BYTES, d);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(calls).toHaveLength(2);
    expect(r.quality).toBe(QUALITY_LADDER[1]);
    expect(r.scale).toBe(1);
    expect(r.width).toBe(MAX_EDGE); // legibility cap, not a sacrifice
  });

  it("caps the longest edge near 2048 so retina text stays legible", () => {
    const plan = encodeAttempts(3840, 2160);
    expect(plan[0].width).toBe(MAX_EDGE);
    expect(plan[0].height).toBe(Math.round(2160 * (MAX_EDGE / 3840)));
    // A source already under the cap is never upscaled.
    expect(encodeAttempts(800, 600)[0].width).toBe(800);
  });

  it("reports 'too-large' — not a crash — when even the last rung overflows", async () => {
    const f = fakeFile("huge.png", "image/png", 20_000_000);
    const { deps: d, calls } = deps({ width: 2560, height: 1440 }, () => 9_000_000);
    const r = await shrinkToFit(f, CLI_IMAGE_BUDGET_BYTES, d);
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.reason).toBe("too-large");
    if (r.reason !== "too-large") return;
    expect(r.attempts).toBe(calls.length);
    expect(calls.length).toBeGreaterThan(QUALITY_LADDER.length);
  });

  it("refuses above the source cap BEFORE decoding (an OOM guard, reported honestly)", async () => {
    const f = fakeFile("enormous.png", "image/png", MAX_SOURCE_BYTES + 1);
    let decoded = false;
    const r = await shrinkToFit(f, CLI_IMAGE_BUDGET_BYTES, {
      decode: async () => {
        decoded = true;
        throw new Error("should never run");
      },
      encode: async () => fakeBlob(1),
    });
    expect(decoded).toBe(false);
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.reason).toBe("too-large");
    if (r.reason !== "too-large") return;
    expect(r.attempts).toBe(0);
  });

  it("distinguishes a decode failure ('unreadable') from a budget failure", async () => {
    const f = fakeFile("broken.png", "image/png", 6_000_000);
    const r = await shrinkToFit(f, CLI_IMAGE_BUDGET_BYTES, {
      decode: async () => {
        throw new Error("bad PNG header");
      },
      encode: async () => fakeBlob(1),
    });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.reason).toBe("unreadable");
    expect(r.detail).toContain("bad PNG header");
  });

  it("re-encodes an in-budget format no CLI accepts (BMP) and says so", async () => {
    const f = fakeFile("clip.bmp", "image/bmp", 500_000);
    const { deps: d } = deps({ width: 800, height: 600 }, () => 120_000);
    const r = await shrinkToFit(f, CLI_IMAGE_BUDGET_BYTES, d);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.recompressed).toBe(true);
    expect(r.file.name).toBe("clip.jpg");
  });
});
