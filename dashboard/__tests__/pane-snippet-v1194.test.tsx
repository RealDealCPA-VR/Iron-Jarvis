/**
 * v1.194.0 — a screen snippet lands in a Build pane.
 *
 * A ConPTY pane is a byte stream; the image travels as a PATH. This file covers
 * the pane side of that: the Ctrl+V ordering (TEXT WINS — the regression that
 * would matter most on an Excel-all-day machine), the clipboard-bridge
 * normalisation incl. the honest "unreadable" report, the drop contract, and the
 * call sites a rendered test cannot reach.
 *
 * jsdom cannot render xterm, so the split is the house idiom (v1.163.0,
 * v1.190.0): unit-test the pure seams, SOURCE-PIN the ordering/call-site.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  clipImageOutcome,
  dragCarriesFiles,
  formatSnipBytes,
  resolvePaste,
  snipFilesFromPaste,
  snipFromClipboardImage,
  type ClipImage,
} from "@/components/terminal/TerminalPane";

// A 1x1 PNG.
const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

const img = () => new File([new Uint8Array([1, 2, 3])], "snip.png", { type: "image/png" });

describe("resolvePaste — TEXT wins whenever the clipboard carries text", () => {
  it("pastes the TEXT (and never probes the image side) when both flavours are present", async () => {
    // THE EXCEL CASE. Copying a range in Excel/Word/Outlook puts a bitmap on the
    // Windows clipboard ALONGSIDE CF_UNICODETEXT — that is what Paste Special's
    // "Bitmap" entry is. Probing the image first turns every such paste into an
    // image chip and the text never arrives.
    const readImage = vi.fn(async (): Promise<ClipImage> => ({ kind: "image", file: img() }));
    const onText = vi.fn();
    const onImage = vi.fn();
    const outcome = await resolvePaste(
      async () => "=SUM(A1:A9)\t1234",
      readImage,
      onText,
      onImage,
      vi.fn(),
    );
    expect(outcome).toBe("text");
    expect(onText).toHaveBeenCalledWith("=SUM(A1:A9)\t1234");
    expect(onImage).not.toHaveBeenCalled();
    // Not merely "not preferred": never even asked. A toPNG() of a multi-megapixel
    // bitmap on the Electron MAIN process is the other cost of asking.
    expect(readImage).not.toHaveBeenCalled();
  });

  it("takes the image when the clipboard has NO text flavour (a Win+Shift+S snip)", async () => {
    const onText = vi.fn();
    const onImage = vi.fn();
    const outcome = await resolvePaste(
      async () => "",
      async () => ({ kind: "image", file: img() }),
      onText,
      onImage,
      vi.fn(),
    );
    expect(outcome).toBe("image");
    expect(onImage).toHaveBeenCalledTimes(1);
    expect(onText).not.toHaveBeenCalled();
  });

  it("REPORTS an image the bridge could not encode instead of doing nothing", async () => {
    const onUnreadable = vi.fn();
    const outcome = await resolvePaste(
      async () => "",
      async () => ({ kind: "unreadable" }),
      vi.fn(),
      vi.fn(),
      onUnreadable,
    );
    expect(outcome).toBe("unreadable");
    expect(onUnreadable).toHaveBeenCalledTimes(1);
  });

  it("carries the bridge's OWN unreadable payload through to a report", async () => {
    // End to end over the real classifier: `{error:"unreadable"}` is exactly what
    // desktop/main.js returns when the clipboard held an image it could not
    // encode. Before this, it collapsed to null and Ctrl+V produced NOTHING —
    // no chip, no message, no keystroke.
    const onUnreadable = vi.fn();
    const onText = vi.fn();
    const outcome = await resolvePaste(
      async () => "", // an image-only clipboard has no text flavour
      async () => clipImageOutcome({ error: "unreadable", bytes: 0, width: 1920, height: 1080 }),
      onText,
      vi.fn(),
      onUnreadable,
    );
    expect(outcome).toBe("unreadable");
    expect(onUnreadable).toHaveBeenCalledTimes(1);
    expect(onText).not.toHaveBeenCalled(); // NOT a fall-through to an empty paste
  });

  it("still takes the image when the TEXT probe throws", async () => {
    const onImage = vi.fn();
    const outcome = await resolvePaste(
      async () => {
        throw new Error("clipboard blocked");
      },
      async () => ({ kind: "image", file: img() }),
      vi.fn(),
      onImage,
      vi.fn(),
    );
    expect(outcome).toBe("image");
    expect(onImage).toHaveBeenCalledTimes(1);
  });

  it("still pastes text when the IMAGE probe throws", async () => {
    // A broken/absent image bridge must not swallow ordinary pasting.
    const onText = vi.fn();
    const outcome = await resolvePaste(
      async () => "npm test",
      async () => {
        throw new Error("no IPC");
      },
      onText,
      vi.fn(),
      vi.fn(),
    );
    expect(outcome).toBe("text");
    expect(onText).toHaveBeenCalledWith("npm test");
  });

  it("does nothing (and never throws) on an empty clipboard", async () => {
    const onText = vi.fn();
    const onUnreadable = vi.fn();
    await expect(
      resolvePaste(
        async () => "",
        async () => ({ kind: "none" }),
        onText,
        vi.fn(),
        onUnreadable,
      ),
    ).resolves.toBe("nothing");
    expect(onText).not.toHaveBeenCalled();
    expect(onUnreadable).not.toHaveBeenCalled();
  });
});

describe("snipFilesFromPaste — the same rule on the browser paste event", () => {
  const pasteEvent = (opts: { text?: string; image?: boolean }) => {
    const file = new File([new Uint8Array([1, 2, 3])], "image.png", { type: "image/png" });
    return {
      clipboardData: {
        getData: (type: string) => (type === "text/plain" ? (opts.text ?? "") : ""),
        items: opts.image ? [{ kind: "file", type: "image/png", getAsFile: () => file }] : [],
        files: [],
      },
    } as unknown as ClipboardEvent;
  };

  it("leaves an Excel/Word paste alone even though Chromium exposes its bitmap", () => {
    // text/plain + text/html + the bitmap as a FILE item is exactly what Chromium
    // hands over for a spreadsheet copy. Claiming it would eat the text paste.
    expect(snipFilesFromPaste(pasteEvent({ text: "Q1\t1234", image: true })).length).toBe(0);
  });

  it("claims a paste that carries an image and NO text", () => {
    const files = snipFilesFromPaste(pasteEvent({ image: true }));
    expect(files.length).toBe(1);
    expect(files[0].type).toBe("image/png");
  });

  it("ignores an ordinary text paste", () => {
    expect(snipFilesFromPaste(pasteEvent({ text: "git status" })).length).toBe(0);
    expect(snipFilesFromPaste(pasteEvent({})).length).toBe(0);
  });
});

describe("clipImageOutcome — the untyped preload boundary, honestly classified", () => {
  it("turns base64 into an image", () => {
    const out = clipImageOutcome({ base64: PNG_B64 });
    expect(out.kind).toBe("image");
    expect(out.kind === "image" && out.file.type).toBe("image/png");
  });

  it("reports {error:'unreadable'} as UNREADABLE, not as an empty clipboard", () => {
    // desktop/main.js returns this shape on purpose: there WAS an image and it
    // would not encode. Mapping it to "none" makes Ctrl+V do nothing at all.
    expect(clipImageOutcome({ error: "unreadable", bytes: 0 }).kind).toBe("unreadable");
  });

  it("says 'none' when the clipboard genuinely holds no image", () => {
    expect(clipImageOutcome(null).kind).toBe("none");
    expect(clipImageOutcome(undefined).kind).toBe("none");
    expect(clipImageOutcome({ width: 10 }).kind).toBe("none");
  });
});

describe("snipFromClipboardImage — shape tolerance", () => {
  it("accepts bare base64 and a data: URL, as a PNG File", () => {
    const bare = snipFromClipboardImage(PNG_B64);
    const dataUrl = snipFromClipboardImage(`data:image/png;base64,${PNG_B64}`);
    expect(bare?.type).toBe("image/png");
    expect(bare!.size).toBeGreaterThan(0);
    expect(dataUrl!.size).toBe(bare!.size);
    expect(bare!.name).toMatch(/\.png$/);
  });

  it("accepts the object shapes the bridge might return", () => {
    for (const key of ["png_b64", "base64", "b64", "data"]) {
      expect(snipFromClipboardImage({ [key]: PNG_B64 })?.size).toBeGreaterThan(0);
    }
  });

  it("returns null — never throws — when there is no image", () => {
    expect(snipFromClipboardImage(null)).toBeNull();
    expect(snipFromClipboardImage(undefined)).toBeNull();
    expect(snipFromClipboardImage("")).toBeNull();
    expect(snipFromClipboardImage({ width: 10 })).toBeNull();
    expect(snipFromClipboardImage("not base64 at all !!!")).toBeNull();
  });
});

describe("dragCarriesFiles — ONE predicate for advertising and consuming a drop", () => {
  it("is true for a file drag and false for everything else", () => {
    expect(dragCarriesFiles({ types: ["Files"] } as unknown as DataTransfer)).toBe(true);
    expect(dragCarriesFiles({ types: ["text/plain"] } as unknown as DataTransfer)).toBe(false);
    expect(dragCarriesFiles(null)).toBe(false);
    expect(dragCarriesFiles(undefined)).toBe(false);
  });
});

describe("formatSnipBytes", () => {
  it("reads as a size a human can judge", () => {
    expect(formatSnipBytes(900)).toBe("900 B");
    expect(formatSnipBytes(2048)).toBe("2 KB");
    expect(formatSnipBytes(4_800_000)).toBe("4.6 MB");
    expect(formatSnipBytes(0)).toBe("0 KB");
    expect(formatSnipBytes(Number.NaN)).toBe("0 KB");
  });
});

describe("the pane's snippet call sites (source-pinned)", () => {
  const pane = readFileSync(
    join(process.cwd(), "components", "terminal", "TerminalPane.tsx"),
    "utf8",
  );

  it("the Ctrl+V handler reaches the image probe at all, and never emits ^V", () => {
    const handler = pane.slice(pane.indexOf("attachCustomKeyEventHandler"));
    const vBranch = handler.indexOf('e.key === "v"');
    const imageGuard = handler.indexOf("ijBridge?.clipboardReadImage", vBranch);
    const textPaste = handler.indexOf("pasteFromClipboard()", vBranch);
    expect(vBranch).toBeGreaterThan(-1);
    expect(imageGuard).toBeGreaterThan(vBranch);
    expect(imageGuard).toBeLessThan(textPaste);
    // The unconditional preventDefault is gone: in a plain browser the native
    // paste event is the only thing that carries image bytes.
    const branch = handler.slice(vBranch, handler.indexOf("shiftKey", textPaste));
    expect(branch).toContain("return false");
  });

  it("hands resolvePaste the TEXT reader first — the daily-driver ordering", () => {
    // Swap these two arguments and an Excel copy becomes an image chip.
    expect(pane).toMatch(/resolvePaste\(\s*readClip,\s*readClipImage,/);
    // ...and the unreadable report reaches the user.
    expect(pane).toContain("couldn't be read — nothing was attached.");
  });

  it("reports a REJECTED image probe instead of calling it an empty clipboard", () => {
    // A rejected bridge call (handler unregistered, or the main process
    // refusing an untrusted sender) is not "you didn't copy an image".
    // Mapping it to "none" makes a broken bridge indistinguishable from an
    // empty clipboard: Ctrl+V does nothing and says nothing. It must land on
    // the same honest chip a corrupt image gets.
    const probe = pane.slice(
      pane.indexOf("const readClipImage"),
      pane.indexOf("const pasteFromClipboard"),
    );
    expect(probe).toContain("clipboardReadImage");
    expect(probe).toMatch(/\.catch\(\(\) => \(\{ kind: "unreadable" \}\)/);
    // The no-bridge case is still a genuine "none" — nothing was attempted.
    expect(probe).toMatch(/Promise\.resolve\(\{ kind: "none" \}/);
  });

  it("handles a native paste event carrying an image, in CAPTURE phase", () => {
    expect(pane).toContain("snipFilesFromPaste(e)");
    expect(pane).toContain('holder.addEventListener("paste", onPaste, true)');
    expect(pane).toContain('holder.removeEventListener("paste", onPaste, true)');
    // A paste we do not claim is returned on, untouched.
    expect(pane).toMatch(/if \(!imgs\.length\) return;/);
  });

  it("consumes every drop it advertised, BEFORE the non-image bail-out", () => {
    const drop = pane.slice(pane.indexOf("onDrop={(e) =>"), pane.indexOf("className={`group"));
    const guard = drop.indexOf("dragCarriesFiles(e.dataTransfer)");
    const prevent = drop.indexOf("e.preventDefault()");
    const bail = drop.indexOf("if (!files.length)");
    expect(guard).toBeGreaterThan(-1);
    expect(prevent).toBeGreaterThan(guard);
    // Handing an advertised drop back to the browser reaches Electron's
    // will-navigate → shell.openExternal(file://…): the OS opens the file.
    expect(prevent).toBeLessThan(bail);
    expect(drop).toContain("imageFilesFromDrop(e.nativeEvent)");
    // The same predicate gates the ring, so the two can never drift apart.
    const over = pane.slice(pane.indexOf("onDragOver={(e) =>"), pane.indexOf("onDragLeave="));
    expect(over).toContain("dragCarriesFiles(e.dataTransfer)");
  });

  it("clears the accept ring when the drag leaves over a CHILD", () => {
    const leave = pane.slice(pane.indexOf("onDragLeave={(e) =>"), pane.indexOf("onDrop={(e) =>"));
    expect(leave).toContain("e.currentTarget.contains(to)");
    expect(leave).not.toContain("e.target === e.currentTarget");
  });

  it("TYPES the returned reference over the pane's WebSocket, with NO trailing \\r", () => {
    const send = pane.indexOf("live.send(reference.endsWith");
    expect(send).toBeGreaterThan(-1);
    // Auto-submitting would take the flow away from the user: they must be able
    // to type their sentence around the path.
    expect(pane.slice(send, send + 200)).not.toContain("\\r");
    expect(pane).toContain("`/terminals/${info.id}/snippet`");
    expect(pane).toContain("content_b64");
  });

  it("sends the CLI this pane launched, so the server can format per CLI", () => {
    const launch = pane.indexOf("function launchCli");
    expect(pane.slice(launch, launch + 500)).toContain("setPaneCli(cli.id)");
    expect(pane).toMatch(/cli: paneCli,/);
  });

  it("says so when an image was recompressed, and stays invisible when unused", () => {
    expect(pane).toContain("recompressed to fit");
    // No pending snippet ⇒ no chip strip at all: an unused pane is unchanged.
    expect(pane).toMatch(/\{snips\.length > 0 && \(/);
  });

  it("keeps every refusal on screen instead of losing it", () => {
    expect(pane).toContain('res.reason === "too-large"');
    expect(pane).toContain("Couldn't read this image");
    expect(pane).toContain("isn't an image — nothing was attached.");
    expect(pane.match(/[Nn]othing was attached\./g)?.length).toBeGreaterThanOrEqual(4);
  });
});
