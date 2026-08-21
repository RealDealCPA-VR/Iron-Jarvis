"use client";

// A single live terminal pane: an xterm.js terminal attached over a WebSocket
// to one daemon shell session. xterm itself is imported dynamically inside the
// effect so it never runs during SSR / `next build`.

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import "@xterm/xterm/css/xterm.css";
import {
  Check,
  ClipboardCopy,
  CornerDownLeft,
  ExternalLink,
  Image as ImageIcon,
  Layers,
  Loader2,
  Paperclip,
  Play,
  Plug,
  PlugZap,
  Rocket,
  Sparkles,
  Terminal as TerminalIcon,
  Workflow,
  X,
} from "lucide-react";
import { ApiError, get, post, wsUrl } from "@/lib/api";
import { waitForStableSize } from "@/lib/layout";
import {
  CLI_IMAGE_BUDGET_BYTES,
  fileToBase64,
  imageFilesFromDrop,
  imageItemsFromPaste,
  shrinkToFit,
} from "@/lib/snippet";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import type { AiCli, ModelOption, Skill, TerminalInfo } from "@/lib/types";

type AIResult = {
  reply: string;
  command: string;
  provider: string;
  model: string;
  /** Skill playbooks injected into this answer (names). */
  skills?: string[];
};

type ConnState = "connecting" | "open" | "reconnecting" | "closed";

// --- Screen snippets (v1.194.0) -------------------------------------------
// A ConPTY pane is a BYTE STREAM: there is no image channel to paste into. But
// every AI CLI we launch reads images OFF DISK from a path in the prompt, and
// the daemon runs on this same machine as the CLI child — so a path is genuinely
// shared. Capture → shrink to fit (lib/snippet) → POST the bytes → type the
// returned REFERENCE into the shell. No synthesized paste keystroke: Ctrl+V
// after Win+Shift+S is documented to do nothing in Claude Code on Windows.

/** One image waiting on the pane. It is ALWAYS visible as a chip — including
 *  when it failed, so a refusal can never read as "attached". */
type PendingSnip = {
  id: string;
  name: string;
  /** Byte size of what would actually be sent (post-shrink when ready). */
  bytes: number;
  /** Object URL for the thumbnail; "" until the shrink pipeline finishes. */
  url: string;
  /** True when the shrink pipeline re-encoded the image to fit the budget —
   *  surfaced on the chip, because quietly shrinking a screenshot is a lie. */
  recompressed: boolean;
  status: "preparing" | "ready" | "sending" | "failed";
  error?: string;
  /** The exact bytes that will be POSTed (post-shrink), once ready. */
  file?: File;
};

/** Compact human size for a chip. */
export function formatSnipBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 KB";
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** What the desktop clipboard bridge is telling us about the clipboard's image
 *  flavour. `unreadable` is a REPORTABLE outcome, not an absence — see
 *  `clipImageOutcome`. */
export type ClipImage =
  | { kind: "image"; file: File }
  | { kind: "unreadable" }
  | { kind: "none" };

/**
 * THE Ctrl+V DECISION, as a testable seam.
 *
 * TEXT WINS WHENEVER THE CLIPBOARD CARRIES TEXT. This ordering is the whole
 * point and it is NOT the obvious one: copying a range in Excel (or Word, or an
 * Outlook body) puts a BITMAP on the Windows clipboard ALONGSIDE the text —
 * that is exactly why Paste Special offers "Bitmap"/"Picture". Probing the
 * image side first would turn every "copy some cells, paste into the terminal"
 * into an image chip and the text would never arrive, which is a regression in
 * the thing people do all day. It would also force a synchronous multi-megapixel
 * `toPNG()` on the Electron MAIN process before any text could paste.
 *
 * A Win+Shift+S snip has NO text flavour, so the feature loses nothing: an
 * empty text read falls straight through to the image probe.
 *
 * A failure ANYWHERE (either probe throwing) must still leave the other path
 * working — a broken bridge that swallowed ordinary pasting would be far worse
 * than the missing feature.
 */
export async function resolvePaste(
  readText: () => Promise<string>,
  readImage: () => Promise<ClipImage>,
  onText: (text: string) => void,
  onImage: (file: File) => void,
  onUnreadable: () => void,
): Promise<"image" | "text" | "unreadable" | "nothing"> {
  let text = "";
  try {
    text = await readText();
  } catch {
    text = ""; // clipboard blocked — still worth probing the image side
  }
  if (text) {
    onText(text);
    return "text";
  }
  let image: ClipImage = { kind: "none" };
  try {
    image = await readImage();
  } catch {
    image = { kind: "none" }; // no image path available — nothing to paste
  }
  if (image.kind === "image") {
    onImage(image.file);
    return "image";
  }
  if (image.kind === "unreadable") {
    // The bridge told us there WAS an image and it could not encode it. Doing
    // nothing here is the silent failure this codebase refuses: the user pressed
    // Ctrl+V on a snip and would see no chip, no message, and no keystroke.
    onUnreadable();
    return "unreadable";
  }
  return "nothing";
}

/**
 * Decide whether a NATIVE paste event belongs to the snippet feature.
 *
 * Same rule as `resolvePaste`, enforced on the browser path: Chromium exposes an
 * Excel/Word copy as `text/plain` + `text/html` + the bitmap as a FILE item, so
 * claiming every paste that carries an image file would eat that text paste.
 * Only a paste with NO text flavour is ours.
 */
export function snipFilesFromPaste(e: ClipboardEvent): File[] {
  let text = "";
  try {
    text = e.clipboardData?.getData?.("text/plain") ?? "";
  } catch {
    text = ""; // hostile/absent clipboardData — fall through to the image check
  }
  if (text) return []; // TEXT WINS — xterm pastes it exactly as before
  return imageItemsFromPaste(e);
}

/** True when a drag carries files. ONE predicate shared by dragover and drop:
 *  the pane must never advertise a drop (ring + preventDefault) that it then
 *  hands back to the browser default — in the packaged app that reaches
 *  `will-navigate` → `shell.openExternal(file://…)` and the OS OPENS the file. */
export function dragCarriesFiles(dt: DataTransfer | null | undefined): boolean {
  return Array.from(dt?.types ?? []).includes("Files");
}

/**
 * Normalize whatever the desktop clipboard bridge hands back for an image into
 * a File, or null when the clipboard holds no image.
 *
 * Deliberately shape-tolerant: this crosses the Electron preload boundary
 * (`window.ironjarvis.clipboardReadImage`), which is untyped, and a mismatch
 * here must degrade to "no image, paste text as usual" rather than throw
 * inside a keydown handler and break ordinary pasting.
 */
export function snipFromClipboardImage(value: unknown, name?: string): File | null {
  if (!value) return null;
  const raw =
    typeof value === "string"
      ? value
      : ((value as Record<string, unknown>).png_b64 ??
        (value as Record<string, unknown>).base64 ??
        (value as Record<string, unknown>).b64 ??
        (value as Record<string, unknown>).data);
  if (typeof raw !== "string" || !raw) return null;
  // Tolerate a data: URL as well as bare base64.
  const b64 = raw.includes(",") ? raw.slice(raw.indexOf(",") + 1) : raw;
  try {
    const bin = atob(b64);
    if (!bin.length) return null;
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], name || `snip-${Date.now()}.png`, { type: "image/png" });
  } catch {
    return null; // not base64 — treat as "no image"
  }
}

/**
 * The bridge's answer, classified into the three things it can actually mean.
 *
 * `desktop/main.js` deliberately returns `{error:"unreadable"}` when the
 * clipboard HELD an image that would not encode, with a comment saying it must
 * never be reported as "nothing copied". Collapsing that to `null` (which is
 * what a bare `snipFromClipboardImage` does) throws away the only signal that
 * distinguishes "you copied nothing" from "I couldn't read what you copied".
 */
export function clipImageOutcome(value: unknown, name?: string): ClipImage {
  if (!value) return { kind: "none" };
  const file = snipFromClipboardImage(value, name);
  if (file) return { kind: "image", file };
  const err = typeof value === "object" ? (value as Record<string, unknown>).error : undefined;
  if (typeof err === "string" && err) return { kind: "unreadable" };
  return { kind: "none" };
}

// Terminal REPORT/answerback replies xterm auto-generates for control QUERIES
// (Primary/Secondary Device Attributes "\x1b[?1;2c", cursor-position reports
// "\x1b[..R", device-status "\x1b[..n", window reports "\x1b[..t"). On (re)connect
// the daemon replays saved scrollback that can contain such a query; xterm
// answers it and the answer would be injected into the shell as fake input
// (visible as "[?1;2c" at a fresh prompt). We drop these ONLY during the brief
// post-connect replay window — a real user keystroke never matches this shape.
const TERM_REPORT_RE = /^\x1b\[[?>=0-9;]*[cnRt]/;

/** xterm theme tuned to the arc-reactor cyan / near-black aesthetic. */
const XTERM_THEME = {
  background: "#0a0c11",
  foreground: "#cdd3df",
  cursor: "#22d3ee",
  cursorAccent: "#0a0c11",
  selectionBackground: "rgba(34,211,238,0.28)",
  black: "#0b0d11",
  red: "#fb7185",
  green: "#34d399",
  yellow: "#fbbf24",
  blue: "#38bdf8",
  magenta: "#a78bfa",
  cyan: "#22d3ee",
  white: "#cdd3df",
  brightBlack: "#475569",
  brightRed: "#fda4af",
  brightGreen: "#6ee7b7",
  brightYellow: "#fcd34d",
  brightBlue: "#7dd3fc",
  brightMagenta: "#c4b5fd",
  brightCyan: "#67e8f9",
  brightWhite: "#f4f4f5",
} as const;

export function TerminalPane({
  info,
  focused,
  onFocus,
  onClose,
  models = [],
  aiClis = [],
  skills = [],
  otherTerminals = [],
}: {
  info: TerminalInfo;
  focused: boolean;
  onFocus: () => void;
  onClose: () => void;
  /** Model catalog for the PER-PANE AI assist picker (from /models). */
  models?: ModelOption[];
  /** AI CLIs detected on this machine, for the "Launch" dropdown. */
  aiClis?: AiCli[];
  /** The discovered skill library — usable by ANY provider via the AI assist. */
  skills?: Skill[];
  /** All live terminals (self included; filtered here) — lets THIS pane's AI
   *  see what's happening in other panes when the user opts in. */
  otherTerminals?: { id: string; shell: string; cwd: string }[];
}) {
  const router = useRouter();
  const holderRef = useRef<HTMLDivElement | null>(null);
  // The live xterm instance, so we can refocus it after typing a launch command.
  const termRef = useRef<{ focus: () => void } | null>(null);
  const [state, setState] = useState<ConnState>("connecting");
  // The live WS, exposed to the AI bar so "Run" can type into THIS shell.
  const wsRef = useRef<WebSocket | null>(null);

  // --- Per-pane AI assist (suggest-only; Run is an explicit click) ---------
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResult, setAiResult] = useState<AIResult | null>(null);
  const [choice, setChoice] = useState(""); // "" = the app's default model
  // Skill for the assist: "" = Auto (search the library), "none" = off,
  // anything else = that exact skill. Works with EVERY provider (prompt-side).
  const [skillChoice, setSkillChoice] = useState("");
  // Cross-terminal sharing: other pane ids whose output THIS ask should see.
  const [ctxIds, setCtxIds] = useState<string[]>([]);
  const [ctxOpen, setCtxOpen] = useState(false);
  const [ctxCopied, setCtxCopied] = useState(false);
  const peers = otherTerminals.filter((t) => t.id !== info.id);

  function toggleCtx(id: string) {
    setCtxIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(-3),
    );
  }

  // Copy this pane's CLEAN context (ANSI-stripped) for pasting into any other
  // AI — a claude/codex CLI in another pane, or anything else.
  async function copyContext(e: React.MouseEvent) {
    e.stopPropagation();
    try {
      const res = await get<{ text: string }>(`/terminals/${info.id}/context`);
      const bridge = (
        window as unknown as {
          ironjarvis?: { clipboardWriteText?: (t: string) => Promise<unknown> };
        }
      ).ironjarvis;
      if (bridge?.clipboardWriteText) await bridge.clipboardWriteText(res.text);
      else await navigator.clipboard?.writeText?.(res.text);
      setCtxCopied(true);
      window.setTimeout(() => setCtxCopied(false), 2500);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.message : String(err));
      setAiOpen(true);
    }
  }

  async function askAI(e: React.FormEvent) {
    e.preventDefault();
    if (!aiPrompt.trim() || aiBusy) return;
    setAiBusy(true);
    setAiError(null);
    setAiResult(null);
    try {
      const [provider, model] = choice ? choice.split("::") : ["", ""];
      const res = await post<AIResult>(`/terminals/${info.id}/ai`, {
        prompt: aiPrompt.trim(),
        provider,
        model,
        skill: skillChoice,
        include_terminals: ctxIds,
      });
      setAiResult(res);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAiBusy(false);
    }
  }

  // Turn THIS session's transcript into a repeatable workflow: the agent builds
  // it server-side, we stash it, then hop to the Workflows editor which loads it.
  const [wfBusy, setWfBusy] = useState(false);
  async function makeWorkflow(e: React.MouseEvent) {
    e.stopPropagation();
    if (wfBusy) return;
    setWfBusy(true);
    setAiError(null);
    try {
      const [provider, model] = choice ? choice.split("::") : ["", ""];
      const def = await post<{ name: string; description: string; steps: unknown[] }>(
        `/terminals/${info.id}/workflow`,
        { provider, model },
      );
      try {
        sessionStorage.setItem("ij_pending_workflow", JSON.stringify(def));
      } catch {
        /* private mode — the editor just won't auto-load */
      }
      router.push("/workflows");
    } catch (err) {
      // Surface the reason in the assist bar (e.g. "no output yet").
      setAiError(err instanceof ApiError ? err.message : String(err));
      setAiOpen(true);
    } finally {
      setWfBusy(false);
    }
  }

  // --- Launch an installed AI CLI (claude / codex / …) in THIS shell --------
  const [launchOpen, setLaunchOpen] = useState(false);
  const [launchHint, setLaunchHint] = useState<string | null>(null);
  // WHICH CLI THIS PANE IS RUNNING. launchCli has always known it and threw it
  // away after a 5s toast; the snippet route needs it to format the image
  // reference the way that CLI wants to be handed a path (mirrors Creative
  // Studio's `_studio_cli`). "" = a plain shell — the server falls back to a
  // bare quoted path.
  const [paneCli, setPaneCli] = useState("");
  const installedClis = aiClis.filter((c) => c.installed);
  const notInstalledClis = aiClis.filter((c) => !c.installed);

  function launchCli(cli: AiCli) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    // Type the launch command WITHOUT a newline — the user presses Enter to
    // actually start it (a last look, same as the AI "Run" suggestion).
    ws.send(cli.command);
    setPaneCli(cli.id); // remember it for snippet delivery
    termRef.current?.focus();
    setLaunchHint(cli.label);
    window.setTimeout(() => setLaunchHint(null), 5000);
  }

  // --- Pending screen snippets ---------------------------------------------
  const [snips, setSnips] = useState<PendingSnip[]>([]);
  const [expandedSnip, setExpandedSnip] = useState<string | null>(null);
  const [snipSending, setSnipSending] = useState(false);
  const [snipNote, setSnipNote] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  // Every object URL we minted, so unmount can revoke them all.
  const snipUrls = useRef<Set<string>>(new Set());
  useEffect(
    () => () => {
      snipUrls.current.forEach((u) => URL.revokeObjectURL(u));
      snipUrls.current.clear();
    },
    [],
  );

  const dropSnipUrl = (url: string) => {
    if (!url) return;
    snipUrls.current.delete(url);
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* already gone */
    }
  };

  /** Put a FAILED chip on the strip for something that never became a snippet
   *  at all (a clipboard image the app could not read, a non-image drop). The
   *  chip is the honest half of the feature: an attempt that produced nothing
   *  must still be visible and dismissable, not silence. */
  const pushFailedSnip = useCallback((name: string, error: string) => {
    const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    setSnips((prev) => [
      ...prev,
      { id, name, bytes: 0, url: "", recompressed: false, status: "failed", error },
    ]);
  }, []);

  /** Take images the user pasted or dropped: chip them immediately (so nothing
   *  happens invisibly), then shrink each to fit the CLI budget. A refusal
   *  stays on screen as a FAILED chip — never a silent disappearance. */
  const acceptSnips = useCallback((files: File[]) => {
    if (!files.length) return;
    for (const file of files) {
      const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      const name = file.name || `snip-${Date.now()}.png`;
      setSnips((prev) => [
        ...prev,
        { id, name, bytes: file.size, url: "", recompressed: false, status: "preparing" },
      ]);
      void (async () => {
        try {
          const res = await shrinkToFit(file, CLI_IMAGE_BUDGET_BYTES);
          if (!res.ok) {
            // The two outcomes are different facts and are reported differently:
            // a budget refusal names the limit, a decode failure says it could
            // not READ the image. Either way nothing was attached, and we say so.
            const why =
              res.reason === "too-large"
                ? `Too big to attach — ${res.detail} Nothing was attached.`
                : `Couldn't read this image — ${res.detail} Nothing was attached.`;
            setSnips((prev) =>
              prev.map((s) => (s.id === id ? { ...s, status: "failed", error: why } : s)),
            );
            return;
          }
          const url = URL.createObjectURL(res.file);
          snipUrls.current.add(url);
          setSnips((prev) =>
            prev.map((s) =>
              s.id === id
                ? {
                    ...s,
                    status: "ready",
                    file: res.file,
                    name: res.file.name || s.name,
                    url,
                    bytes: res.bytes,
                    recompressed: res.recompressed,
                  }
                : s,
            ),
          );
        } catch (err) {
          setSnips((prev) =>
            prev.map((s) =>
              s.id === id
                ? { ...s, status: "failed", error: `Couldn't prepare this image: ${String(err)}` }
                : s,
            ),
          );
        }
      })();
    }
  }, []);

  function removeSnip(id: string) {
    setSnips((prev) => {
      const hit = prev.find((s) => s.id === id);
      if (hit) dropSnipUrl(hit.url);
      return prev.filter((s) => s.id !== id);
    });
    setExpandedSnip((cur) => (cur === id ? null : cur));
  }

  /** Send the ready snippets: POST the bytes to the daemon (which writes them
   *  next to this pane's work and formats a per-CLI reference), then TYPE that
   *  reference into the shell over the pane's own WebSocket. Deliberately NO
   *  trailing "\r" — the user types their sentence around the path. */
  async function sendSnips() {
    if (snipSending) return;
    const ready = snips.filter((s) => s.status === "ready" && s.file);
    if (!ready.length) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setSnips((prev) =>
        prev.map((s) =>
          s.status === "ready"
            ? { ...s, status: "failed", error: "Terminal isn't connected — nothing was attached." }
            : s,
        ),
      );
      return;
    }
    setSnipSending(true);
    try {
      for (const snip of ready) {
        setSnips((prev) =>
          prev.map((s) => (s.id === snip.id ? { ...s, status: "sending", error: undefined } : s)),
        );
        try {
          const content_b64 = await fileToBase64(snip.file as File);
          const res = await post<{
            path: string;
            name: string;
            bytes: number;
            reference: string;
            location?: string;
            note?: string;
          }>(`/terminals/${info.id}/snippet`, {
            filename: snip.name,
            content_b64,
            cli: paneCli,
          });
          const live = wsRef.current;
          if (!live || live.readyState !== WebSocket.OPEN) {
            setSnips((prev) =>
              prev.map((s) =>
                s.id === snip.id
                  ? {
                      ...s,
                      status: "failed",
                      error: `Saved to ${res.path}, but the terminal dropped — the path was NOT typed in.`,
                    }
                  : s,
              ),
            );
            continue;
          }
          const reference = res.reference || res.path;
          // NO trailing carriage return: typed in, not submitted.
          live.send(reference.endsWith(" ") ? reference : `${reference} `);
          // The daemon prefers the pane's own folder and SAYS SO when it had to
          // fall back to the uploads dir — a CLI confined to its workspace may
          // not be able to read that file, so the user has to know.
          if (res.note) {
            setSnipNote(res.note);
            window.setTimeout(() => setSnipNote(null), 10000);
          }
          dropSnipUrl(snip.url);
          setSnips((prev) => prev.filter((s) => s.id !== snip.id));
          termRef.current?.focus();
        } catch (err) {
          setSnips((prev) =>
            prev.map((s) =>
              s.id === snip.id
                ? {
                    ...s,
                    status: "failed",
                    error: err instanceof ApiError ? err.message : String(err),
                  }
                : s,
            ),
          );
        }
      }
    } finally {
      setSnipSending(false);
    }
  }

  function runSuggested() {
    const ws = wsRef.current;
    if (!aiResult?.command || !ws || ws.readyState !== WebSocket.OPEN) return;
    // Type the command into the shell WITHOUT submitting it — the user presses
    // Enter themselves (a last look before anything executes).
    ws.send(aiResult.command);
    setAiResult(null);
    setAiPrompt("");
  }

  useEffect(() => {
    const holder = holderRef.current;
    if (!holder || typeof window === "undefined") return;

    let disposed = false;
    let term: import("@xterm/xterm").Terminal | null = null;
    let fit: import("@xterm/addon-fit").FitAddon | null = null;
    let ws: WebSocket | null = null;
    let ro: ResizeObserver | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    // Drop xterm's auto-answers to queries embedded in replayed scrollback until
    // this timestamp (set on every (re)connect). See TERM_REPORT_RE.
    let replayGuardUntil = 0;
    let focusedOnce = false; // steal focus on FIRST connect only — a reconnect
    // mid-interaction would close an open dropdown/popup out from under the user

    // Paste support. A terminal treats Ctrl+V as a control char (0x16), NOT
    // paste — so pasting looks broken. Wire it explicitly. term.paste() respects
    // bracketed-paste mode, so a multi-line prompt inserts as ONE block instead
    // of running line-by-line.
    // Prefer the desktop app's NATIVE clipboard (via the preload IPC bridge) —
    // it's never permission-gated; fall back to the Web Clipboard API in a plain
    // browser.
    const ijBridge = (
      window as unknown as {
        ironjarvis?: {
          clipboardReadText?: () => Promise<string>;
          clipboardWriteText?: (t: string) => Promise<unknown>;
          /** PNG bytes of a clipboard IMAGE, or null (desktop app only). */
          clipboardReadImage?: () => Promise<unknown>;
        };
      }
    ).ironjarvis;
    const readClip = (): Promise<string> =>
      ijBridge?.clipboardReadText
        ? ijBridge.clipboardReadText()
        : navigator.clipboard?.readText?.() ?? Promise.resolve("");
    const writeClip = (t: string): Promise<unknown> =>
      ijBridge?.clipboardWriteText
        ? ijBridge.clipboardWriteText(t)
        : navigator.clipboard?.writeText?.(t) ?? Promise.resolve();
    // Image side of the same bridge. Absent in a plain browser tab (there the
    // native paste EVENT carries the bytes, with no permission prompt), and it
    // must never reject — a throw here would take ordinary text paste with it.
    // Only reached when the clipboard has NO text (see `resolvePaste`), so an
    // Excel copy never pays for a multi-megapixel toPNG() on the main process.
    const readClipImage = (): Promise<ClipImage> =>
      ijBridge?.clipboardReadImage
        ? ijBridge
            .clipboardReadImage()
            .then((v) => clipImageOutcome(v))
            // A REJECTED probe is not an empty clipboard. Mapping it to "none"
            // would make a broken bridge (handler unregistered, or the main
            // process refusing an untrusted sender) look exactly like "you
            // didn't copy an image" — the user presses Ctrl+V, nothing happens,
            // and nothing says why. "unreadable" routes it to the same honest
            // chip a corrupt image gets. Text paste is unaffected either way:
            // this probe only runs on a text-LESS clipboard.
            .catch(() => ({ kind: "unreadable" }) as ClipImage)
        : Promise.resolve({ kind: "none" } as ClipImage);

    const pasteFromClipboard = () => {
      // TEXT FIRST, image only on a TEXT-LESS clipboard (v1.194.0) — see
      // `resolvePaste`. Ordinary pasting is byte-for-byte what it always was.
      void resolvePaste(
        readClip,
        readClipImage,
        (t) => {
          if (term) term.paste(t);
        },
        (img) => acceptSnips([img]),
        () =>
          pushFailedSnip(
            "clipboard image",
            "There was an image on the clipboard but it couldn't be read — nothing was attached.",
          ),
      );
    };
    // A native paste that DOES carry image data (plain browser, right-click
    // paste, Shift+Insert). Capture phase on the holder so we run before
    // xterm's own textarea handler; a paste carrying ANY text is left completely
    // alone — `snipFilesFromPaste` enforces that.
    const onPaste = (e: ClipboardEvent) => {
      const imgs = snipFilesFromPaste(e);
      if (!imgs.length) return; // text (or no image) — xterm handles it as before
      e.preventDefault();
      e.stopPropagation();
      acceptSnips(imgs);
    };
    const onContextMenu = (e: MouseEvent) => {
      // Right-click copies a selection if you have one, else pastes — the
      // familiar Windows-terminal gesture (no browser context menu here).
      e.preventDefault();
      const sel = term?.getSelection();
      if (sel) {
        writeClip(sel).catch(() => {});
        term?.clearSelection();
      } else {
        pasteFromClipboard();
      }
    };
    const onWheel = (e: WheelEvent) => {
      // Guarantee scrollback scrolling on the mouse wheel — capture it here so
      // it can't be swallowed by the container/react-rnd. Only in the NORMAL
      // buffer; full-screen TUI apps (alt-screen) own the wheel themselves.
      if (!term || term.buffer.active.type !== "normal") return;
      e.preventDefault();
      e.stopPropagation();
      const amount = e.deltaMode === 1 ? e.deltaY : e.deltaY / 40; // lines vs px
      const n = Math.trunc(amount);
      term.scrollLines(n !== 0 ? n : e.deltaY > 0 ? 1 : -1);
    };

    const doFit = () => {
      try {
        fit?.fit();
      } catch {
        /* container not measurable yet */
      }
    };

    const sendResize = () => {
      if (ws && ws.readyState === WebSocket.OPEN && term) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    };

    const onWinResize = () => {
      doFit();
      sendResize();
    };

    const connect = () => {
      ws = new WebSocket(wsUrl(`/terminals/${info.id}/ws`));
      wsRef.current = ws; // the AI bar's "Run" types through this socket
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        attempts = 0;
        // Every (re)attach replays the session's full scrollback — reset so it
        // lands on a clean screen instead of appending to a buffer that already
        // holds the same history (doubled output after a silent reconnect), and
        // so a full-screen app's stale modes don't corrupt the replay.
        term?.reset();
        // Guard the scrollback-replay window so echoed query answers ("[?1;2c")
        // don't get injected into the shell as fake input.
        replayGuardUntil = Date.now() + 800;
        setState("open");
        doFit();
        sendResize();
        // Full repaint once the replay window closes (v1.190.0): the replay
        // lands as one burst and any cell painted from transitional metrics
        // stays stale until SOMETHING repaints it — which used to be "the
        // user drags the box". Timed just past the replay guard so it sweeps
        // the whole viewport exactly once per (re)attach.
        setTimeout(() => {
          try {
            term?.refresh(0, Math.max(0, (term?.rows ?? 1) - 1));
          } catch {
            /* disposed mid-wait — nothing to repaint */
          }
        }, 850);
        if (!focusedOnce) {
          focusedOnce = true;
          term?.focus();
        }
      };
      ws.onmessage = (ev: MessageEvent) => {
        if (!term) return;
        // Server -> client: PTY output as binary (ArrayBuffer); text just in case.
        if (typeof ev.data === "string") term.write(ev.data);
        else term.write(new Uint8Array(ev.data as ArrayBuffer));
      };
      ws.onclose = (ev: CloseEvent) => {
        if (disposed) return;
        // 4000 = the SHELL ITSELF exited (daemon's explicit signal). There is
        // nothing to reconnect to — retrying just re-attached to a dead PTY in
        // a crash loop that also stole focus every cycle.
        if (ev.code === 4000) {
          setState("closed");
          return;
        }
        if (attempts < 4) {
          attempts += 1;
          setState("reconnecting");
          reconnectTimer = setTimeout(connect, 500 * attempts);
        } else {
          setState("closed");
        }
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* noop */
        }
      };
    };

    (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (disposed) return;

      term = new Terminal({
        cursorBlink: true,
        cursorStyle: "bar",
        fontSize: 12.5,
        lineHeight: 1.15,
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
        theme: { ...XTERM_THEME },
        scrollback: 5000,
        allowProposedApi: true,
      });
      fit = new FitAddon();
      term.loadAddon(fit);
      term.open(holder);
      termRef.current = term; // expose for launch-command refocus
      doFit();

      // Client -> server: raw keystrokes as text. Suppress xterm's auto-answers
      // to queries in replayed scrollback during the brief post-connect window.
      term.onData((d: string) => {
        if (Date.now() < replayGuardUntil && TERM_REPORT_RE.test(d)) return;
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(d);
      });

      // Clipboard shortcuts: Ctrl/Cmd+V and Ctrl+Shift+V paste; Ctrl+Shift+C
      // copies a selection (plain Ctrl+C stays as the interrupt signal).
      term.attachCustomKeyEventHandler((e) => {
        if (e.type !== "keydown") return true;
        const mod = e.ctrlKey || e.metaKey;
        if (mod && (e.key === "v" || e.key === "V")) {
          // THE IMAGE CASE HAS TO BE REACHABLE AT ALL (v1.194.0). This handler
          // used to preventDefault() unconditionally, which suppresses the
          // browser's native `paste` event entirely — so image bytes never
          // reached the pane no matter what else we wired up. (Which flavour
          // WINS is decided in `resolvePaste`/`snipFilesFromPaste`: text does.)
          if (ijBridge?.clipboardReadImage) {
            // Desktop app: the native clipboard is only reachable over IPC
            // (navigator.clipboard is permission-gated here). Text first, image
            // only when there is no text — see `resolvePaste`.
            e.preventDefault();
            pasteFromClipboard();
            return false; // don't also send the literal control char
          }
          // Plain browser: let the default paste proceed so `onPaste` above
          // sees the image bytes; a text-only paste falls through to xterm's
          // own paste handling (same term.paste, same bracketed-paste mode).
          return false; // xterm must still not emit the literal ^V
        }
        if (mod && e.shiftKey && (e.key === "c" || e.key === "C")) {
          const sel = term?.getSelection();
          if (sel) {
            e.preventDefault();
            writeClip(sel).catch(() => {});
            return false;
          }
        }
        // Keyboard scrollback — Shift+PageUp / Shift+PageDown.
        if (e.shiftKey && e.key === "PageUp") {
          e.preventDefault();
          term?.scrollPages(-1);
          return false;
        }
        if (e.shiftKey && e.key === "PageDown") {
          e.preventDefault();
          term?.scrollPages(1);
          return false;
        }
        return true;
      });
      holder.addEventListener("contextmenu", onContextMenu);
      holder.addEventListener("wheel", onWheel, { passive: false, capture: true });
      holder.addEventListener("paste", onPaste, true);

      ro = new ResizeObserver(() => {
        doFit();
        sendResize();
      });
      ro.observe(holder);
      window.addEventListener("resize", onWinResize);

      // FIT BEFORE CONNECT (v1.190.0). The server replays the session's whole
      // scrollback the moment the socket opens, at whatever size this terminal
      // has RIGHT THEN — and on a RETURN visit the xterm module is cached, so
      // this code used to win the race against the pane's own layout: the
      // replay wrapped into a default 80×24 buffer, the history came back
      // malformed, and no later fit could re-wrap it (dragging re-fits and
      // triggers the server's repaint wiggle, which fixes only the live
      // screen — the user's exact report). On the FIRST visit the module
      // download gave layout time to settle, which is why the original pane
      // looked right. Capped wait: a hidden pane proceeds rather than hangs.
      await waitForStableSize(holder);
      if (disposed) return;
      doFit();

      setState("connecting");
      connect();
    })();

    return () => {
      disposed = true;
      wsRef.current = null;
      termRef.current = null;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      window.removeEventListener("resize", onWinResize);
      holder.removeEventListener("contextmenu", onContextMenu);
      holder.removeEventListener("wheel", onWheel, { capture: true } as EventListenerOptions);
      holder.removeEventListener("paste", onPaste, true);
      ro?.disconnect();
      try {
        ws?.close();
      } catch {
        /* noop */
      }
      try {
        term?.dispose();
      } catch {
        /* noop */
      }
    };
    // Re-wire only when the session id changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info.id]);

  return (
    <div
      onMouseDown={onFocus}
      // Drop a screenshot anywhere on the pane. A drag carrying no FILES is left
      // entirely alone (no preventDefault), so nothing else changes.
      onDragOver={(e) => {
        if (!dragCarriesFiles(e.dataTransfer)) return;
        e.preventDefault();
        e.stopPropagation();
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        // Only when the pointer actually LEAVES the pane. Keying on target-vs-
        // currentTarget identity left the accept ring stuck on forever whenever
        // the drag exited over a CHILD — and the terminal fills the pane, so
        // that is the normal case, not the edge one.
        const to = e.relatedTarget as Node | null;
        if (!to || !e.currentTarget.contains(to)) setDragOver(false);
      }}
      onDrop={(e) => {
        setDragOver(false);
        // WE ADVERTISED THIS DROP, SO WE CONSUME IT. Same predicate as
        // onDragOver above: it preventDefaulted and rang the pane for any drag
        // carrying files, and handing such a drop back to the browser default
        // reaches Electron's `will-navigate` → `shell.openExternal(file://…)`,
        // i.e. a dropped PDF/exe gets OPENED by the OS. preventDefault comes
        // BEFORE the non-image bail-out for exactly that reason.
        if (!dragCarriesFiles(e.dataTransfer)) return;
        e.preventDefault();
        e.stopPropagation();
        const files = imageFilesFromDrop(e.nativeEvent);
        if (!files.length) {
          const dropped = Array.from(e.dataTransfer?.files ?? []);
          pushFailedSnip(
            dropped[0]?.name || "dropped file",
            dropped.length > 1
              ? "Those aren't images — nothing was attached."
              : "That isn't an image — nothing was attached.",
          );
          return;
        }
        acceptSnips(files);
      }}
      className={`group relative flex h-full flex-col overflow-hidden rounded-2xl border bg-[#0a0c11] shadow-card transition-colors ${
        dragOver
          ? "border-accent shadow-glow-sm ring-2 ring-accent/40"
          : focused
            ? "border-accent/50 shadow-glow-sm ring-1 ring-accent/30"
            : "border-white/[0.07] hover:border-white/[0.14]"
      }`}
    >
      {/* Pane header: shell · cwd · connection state · close. The `ij-term-drag`
          class marks this as the drag handle for react-rnd on the Terminals
          page (buttons/selects inside are excluded via react-rnd's `cancel`). */}
      <header className="ij-term-drag flex shrink-0 cursor-move items-center gap-2 border-b border-white/[0.06] bg-ink-900/60 px-3 py-2">
        <TerminalIcon
          size={13}
          className={focused ? "text-accent" : "text-zinc-500"}
        />
        <span className="shrink-0 font-mono text-[11px] font-semibold text-zinc-200">
          {info.shell}
        </span>
        <span
          className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500"
          title={info.cwd}
        >
          {info.cwd}
        </span>
        {/* Per-pane AI model — THIS terminal's assist uses THIS model. */}
        <select
          aria-label="AI model for this terminal"
          value={choice}
          onChange={(e) => setChoice(e.target.value)}
          onMouseDown={(e) => e.stopPropagation()}
          className="field w-auto max-w-[10rem] shrink-0 py-0.5 text-[10px]"
        >
          <option value="">default model</option>
          {models.map((m) => (
            <option key={`${m.provider}::${m.model}`} value={`${m.provider}::${m.model}`}>
              {m.provider} · {m.model}
            </option>
          ))}
        </select>
        <button
          onClick={(e) => {
            e.stopPropagation();
            setAiOpen((v) => !v);
          }}
          title="Ask AI about this terminal"
          className={`grid h-5 w-5 shrink-0 place-items-center rounded-md transition-colors ${
            aiOpen
              ? "bg-accent/15 text-accent"
              : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
          }`}
        >
          <Sparkles size={13} />
        </button>
        <button
          onClick={makeWorkflow}
          disabled={wfBusy}
          title="Turn this session into a repeatable workflow"
          className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-accent/15 hover:text-accent-soft disabled:opacity-50"
        >
          {wfBusy ? <Loader2 size={13} className="animate-spin" /> : <Workflow size={13} />}
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            setLaunchOpen((v) => !v);
          }}
          title="Launch an AI CLI in this terminal (Claude, Codex, …)"
          className={`grid h-5 w-5 shrink-0 place-items-center rounded-md transition-colors ${
            launchOpen
              ? "bg-accent/15 text-accent"
              : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
          }`}
        >
          <Rocket size={13} />
        </button>
        {info.degraded && (
          <span
            title="Basic shell (no full TTY) — commands run, but interactive TUI apps may not render. The full terminal returns after the next app update."
            className="inline-flex shrink-0 items-center rounded-full border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-300"
          >
            basic
          </span>
        )}
        <ConnPill state={state} />
        <button
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          title="Close terminal"
          className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-rose-500/15 hover:text-rose-300"
        >
          <X size={13} />
        </button>
      </header>

      {/* Launch dropdown — the AI CLIs actually installed on this machine.
          Picking one TYPES its command into the shell; the user presses Enter. */}
      {launchOpen && (
        <>
          <button
            aria-hidden
            tabIndex={-1}
            onClick={() => setLaunchOpen(false)}
            className="fixed inset-0 z-30 cursor-default"
          />
          <div className="absolute right-2 top-11 z-40 max-h-[70%] w-60 overflow-auto rounded-xl border border-white/10 bg-ink-900/95 p-1 shadow-card backdrop-blur">
            {installedClis.length === 0 && notInstalledClis.length === 0 && (
              <div className="px-2 py-2 text-[11px] text-zinc-500">Detecting…</div>
            )}
            {installedClis.length > 0 && (
              <div className="px-2 pb-0.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                Installed — click to type, then Enter
              </div>
            )}
            {installedClis.map((c) => (
              <button
                key={c.id}
                onClick={() => {
                  launchCli(c);
                  setLaunchOpen(false);
                }}
                className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] text-zinc-200 transition-colors hover:bg-accent/10 hover:text-accent-soft"
              >
                <span className="flex items-center gap-2">
                  <Rocket size={12} className="text-accent-soft/80" />
                  <span className="font-medium">{c.label}</span>
                </span>
                <span className="font-mono text-[10px] text-zinc-500">{c.command.trim()}</span>
              </button>
            ))}
            {notInstalledClis.length > 0 && (
              <div className="px-2 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-wide text-zinc-600">
                Not installed
              </div>
            )}
            {notInstalledClis.map((c) => (
              <a
                key={c.id}
                href={c.url}
                target="_blank"
                rel="noreferrer"
                title={`${c.label} isn't on your PATH — get it`}
                className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.04]"
              >
                <span>{c.label}</span>
                <ExternalLink size={11} />
              </a>
            ))}
          </div>
        </>
      )}
      {launchHint && (
        <div className="flex shrink-0 items-center gap-2 border-b border-accent/20 bg-accent/[0.06] px-3 py-1 text-[11px] text-accent-soft">
          <CornerDownLeft size={12} /> Press <span className="font-semibold">Enter</span> in the
          terminal to start {launchHint}.
        </div>
      )}

      {snipNote && (
        <div
          role="status"
          className="flex shrink-0 items-center gap-2 border-b border-amber-500/20 bg-amber-500/[0.06] px-3 py-1 text-[11px] text-amber-200"
        >
          <ImageIcon size={12} /> Saved outside this folder — {snipNote}
        </div>
      )}

      {/* Pending snippets. Renders ONLY when something is pending — a pane with
          no snippet looks exactly as it did before this feature existed. */}
      {snips.length > 0 && (
        <div
          data-testid="snip-strip"
          className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-white/[0.06] bg-ink-900/40 px-3 py-1.5"
        >
          {snips.map((s) => (
            <div
              key={s.id}
              className={`flex items-center gap-1.5 rounded-lg border px-1.5 py-1 ${
                s.status === "failed"
                  ? "border-rose-500/30 bg-rose-500/[0.07]"
                  : "border-white/10 bg-white/[0.03]"
              }`}
            >
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  if (s.url) setExpandedSnip(s.id);
                }}
                title={s.url ? "See it full size before you send it" : s.name}
                aria-label={`Preview ${s.name}`}
                className="shrink-0 overflow-hidden rounded border border-white/10"
              >
                {s.url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={s.url} alt={s.name} className="h-8 w-12 object-cover" />
                ) : (
                  <span className="grid h-8 w-12 place-items-center bg-black/40">
                    <ImageIcon size={12} className="text-zinc-600" />
                  </span>
                )}
              </button>
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="max-w-[12rem] truncate font-mono text-[10px] text-zinc-300">
                  {s.name}
                </span>
                <span className="text-[9px] text-zinc-500">
                  {s.status === "preparing"
                    ? "preparing…"
                    : s.status === "sending"
                      ? "attaching…"
                      : formatSnipBytes(s.bytes)}
                  {s.status === "ready" && s.recompressed && (
                    <span
                      title={`Re-encoded to fit under ${formatSnipBytes(CLI_IMAGE_BUDGET_BYTES)} — the CLI would reject the original.`}
                      className="ml-1 text-amber-300"
                    >
                      · recompressed to fit
                    </span>
                  )}
                </span>
                {s.error && (
                  <span role="alert" className="max-w-[16rem] text-[9px] text-rose-300">
                    {s.error}
                  </span>
                )}
              </span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  removeSnip(s.id);
                }}
                title="Remove this snippet"
                aria-label={`Remove ${s.name}`}
                className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-rose-500/15 hover:text-rose-300"
              >
                <X size={11} />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              void sendSnips();
            }}
            disabled={snipSending || state !== "open" || !snips.some((s) => s.status === "ready")}
            title="Saves the image next to this terminal's work and TYPES its path in — press Enter yourself"
            className="btn-accent shrink-0 px-2 py-1 text-[11px]"
          >
            {snipSending ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <Paperclip size={11} />
            )}
            {snipSending ? "Attaching…" : "Insert path"}
          </button>
          {paneCli && (
            <span className="text-[9px] text-zinc-600">for {paneCli}</span>
          )}
        </div>
      )}

      {/* AI assist bar — asks about THIS terminal's recent output; the answer's
          command is only ever TYPED into the shell (never auto-submitted). */}
      {aiOpen && (
        <div className="shrink-0 border-b border-white/[0.06] bg-ink-900/40 px-3 py-2">
          <form onSubmit={askAI} className="flex items-center gap-2">
            <Sparkles size={12} className="shrink-0 text-accent-soft" />
            <input
              type="text"
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              placeholder="Ask about this terminal — e.g. “why did that fail?” or “command to list the 5 biggest files”"
              aria-label="Ask AI about this terminal"
              className="field flex-1 py-1 text-[12px]"
            />
            {/* Dictate the request (offline Vosk in the desktop app, Web Speech in
                a browser). Speaking a plain-English ask beats typing shell syntax;
                the AI turns it into a command you still review + Run. */}
            <VoiceInput
              size="sm"
              onTranscript={(chunk) => setAiPrompt((p) => appendDictation(p, chunk))}
            />
            {/* Skill for this ask: Auto searches the whole discovered library
                (Claude + Codex + yours) — works with ANY provider. */}
            {skills.length > 0 && (
              <select
                aria-label="Skill for this ask"
                title="Apply a skill playbook from your library (Auto picks the best match)"
                value={skillChoice}
                onChange={(e) => setSkillChoice(e.target.value)}
                onMouseDown={(e) => e.stopPropagation()}
                className="field w-auto max-w-[9rem] shrink-0 py-1 text-[10px]"
              >
                <option value="">skill: auto</option>
                <option value="none">skill: none</option>
                {skills.map((s) => (
                  <option key={s.name} value={s.name} title={s.description}>
                    {s.name}
                  </option>
                ))}
              </select>
            )}
            {/* Share ANOTHER terminal's work into this ask (cross-pane context). */}
            {peers.length > 0 && (
              <div className="relative shrink-0">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setCtxOpen((v) => !v);
                  }}
                  title="Include another terminal's recent output in this ask"
                  className={`flex items-center gap-1 rounded-md border px-1.5 py-1 text-[10px] transition-colors ${
                    ctxIds.length
                      ? "border-accent/40 bg-accent/10 text-accent-soft"
                      : "border-white/10 text-zinc-400 hover:border-accent/30"
                  }`}
                >
                  <Layers size={11} />
                  {ctxIds.length ? `+${ctxIds.length} ctx` : "+ctx"}
                </button>
                {ctxOpen && (
                  <div className="absolute right-0 top-7 z-40 w-56 rounded-xl border border-white/10 bg-ink-900/95 p-1 shadow-card backdrop-blur">
                    <div className="px-2 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                      Share context from…
                    </div>
                    {peers.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => toggleCtx(t.id)}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] text-zinc-300 transition-colors hover:bg-accent/10"
                      >
                        <span
                          className={`grid h-3.5 w-3.5 shrink-0 place-items-center rounded border ${
                            ctxIds.includes(t.id)
                              ? "border-accent bg-accent/20 text-accent"
                              : "border-white/20 text-transparent"
                          }`}
                        >
                          <Check size={10} />
                        </span>
                        <span className="min-w-0">
                          <span className="block font-mono text-[10px] text-zinc-200">
                            {t.shell}
                          </span>
                          <span className="block truncate text-[10px] text-zinc-500">
                            {t.cwd}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {/* Copy this pane's clean context — paste it into claude/codex/anything. */}
            <button
              type="button"
              onClick={copyContext}
              title="Copy this terminal's context — paste it into another terminal's AI CLI (claude, codex…) or anywhere else"
              className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-accent/15 hover:text-accent-soft"
            >
              {ctxCopied ? <Check size={12} className="text-emerald-300" /> : <ClipboardCopy size={12} />}
            </button>
            <button
              type="submit"
              disabled={aiBusy || !aiPrompt.trim()}
              className="btn-accent shrink-0 px-2 py-1 text-[11px]"
            >
              {aiBusy ? <Loader2 size={12} className="animate-spin" /> : <CornerDownLeft size={12} />}
              Ask
            </button>
          </form>
          {aiError && (
            <p role="alert" className="mt-1.5 text-[11px] leading-relaxed text-rose-300">
              {aiError}
            </p>
          )}
          {aiResult && (
            <div className="mt-1.5 space-y-1.5">
              <p className="max-h-24 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-zinc-300">
                {aiResult.reply}
              </p>
              <div className="flex items-center gap-2">
                {aiResult.command && (
                  <button
                    onClick={runSuggested}
                    disabled={state !== "open"}
                    className="btn-accent px-2 py-1 text-[11px]"
                    title="Types the command into the shell — press Enter yourself to run it"
                  >
                    <Play size={11} /> Type it in
                  </button>
                )}
                <span className="text-[10px] text-zinc-600">
                  {aiResult.provider} · {aiResult.model}
                </span>
                {(aiResult.skills ?? []).map((s) => (
                  <span
                    key={s}
                    title="Skill playbook applied to this answer"
                    className="inline-flex items-center rounded-full border border-accent/25 bg-accent/[0.08] px-1.5 py-0.5 text-[9px] font-medium text-accent-soft"
                  >
                    skill: {s}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Terminal surface */}
      <div className="relative flex-1 overflow-hidden px-2 py-1.5">
        <div ref={holderRef} className="h-full w-full" />
        {(state === "reconnecting" || state === "closed") && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center bg-[#0a0c11]/70 backdrop-blur-[1px]">
            <div
              className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium ${
                state === "reconnecting"
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                  : "border-rose-500/30 bg-rose-500/10 text-rose-200"
              }`}
            >
              {state === "reconnecting" ? (
                <>
                  <Loader2 size={13} className="animate-spin" /> Reconnecting…
                </>
              ) : (
                <>
                  <Plug size={13} /> Session closed
                </>
              )}
            </div>
          </div>
        )}
        {/* Full-size look before you send it. */}
        {(() => {
          const shown = snips.find((s) => s.id === expandedSnip && s.url);
          if (!shown) return null;
          return (
            <div
              role="dialog"
              aria-label={`Snippet ${shown.name}`}
              onClick={(e) => {
                e.stopPropagation();
                setExpandedSnip(null);
              }}
              className="absolute inset-0 z-50 grid cursor-zoom-out place-items-center bg-black/85 p-3 backdrop-blur-sm"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={shown.url}
                alt={shown.name}
                className="max-h-full max-w-full rounded-lg border border-white/10 object-contain"
              />
            </div>
          );
        })()}
      </div>
    </div>
  );
}

function ConnPill({ state }: { state: ConnState }) {
  if (state === "open") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-medium text-emerald-300">
        <PlugZap size={9} /> live
      </span>
    );
  }
  if (state === "closed") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-rose-500/25 bg-rose-500/10 px-1.5 py-0.5 text-[9px] font-medium text-rose-300">
        <Plug size={9} /> closed
      </span>
    );
  }
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-300">
      <Loader2 size={9} className="animate-spin" />
      {state === "reconnecting" ? "reconnecting" : "connecting"}
    </span>
  );
}
