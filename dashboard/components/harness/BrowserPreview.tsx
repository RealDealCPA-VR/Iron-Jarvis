"use client";

/**
 * The IJ harness session's BROWSER pane (v1.369.0, plan Phase 6).
 *
 * The plan's decision: "a preview of a page on this PC or the user's network
 * (a dev server), with an address box; the paired browser stays the agent's
 * tool for real browsing and is named in the pane when it is paired."
 *
 * HONEST BY CONSTRUCTION: nothing is previewed until there is an address —
 * typed by the user, or one the session's own output printed (a dev server's
 * "Local: http://localhost:5173/", read off the transcript, lib/buildSessions
 * `addressesIn`), offered as a press, never opened on its own. Only an
 * address on this PC or the user's own network opens here (isPreviewAddress);
 * a page that refuses to be framed shows blank, so "Open in a new tab" is
 * always beside it. The paired browser is read from GET /browser/status (a
 * route documented never to fail); when nothing is paired it says so.
 */

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { ExternalLink, Globe, RefreshCw } from "lucide-react";
import { get } from "@/lib/api";
import { isPreviewAddress } from "@/lib/buildSessions";

const PREVIEW_PREFIX = "ij.build.preview.";

/** The address this window last previewed for the session — re-checked on
 *  read: it loads in a frame with no press, so a saved value that is not a
 *  page on this PC or the user's network (storage is writable by anything
 *  on this origin) never loads on its own (v1.369.0 review). */
function readAddress(threadId: string): string {
  try {
    const saved = window.localStorage.getItem(PREVIEW_PREFIX + threadId) ?? "";
    return saved && isPreviewAddress(saved) ? saved : "";
  } catch {
    return "";
  }
}

/** v1.371.0: the address this window's Browser pane shows for a session —
 *  sent with every message (`preview_url`) so the agent knows which page the
 *  user is looking at, and may screenshot that dev server without asking
 *  (harness/page_shot). Re-checked like every read: only a page on this PC
 *  or the user's network ever leaves. */
export function previewAddressFor(threadId: string): string {
  return readAddress(threadId);
}

function writeAddress(threadId: string, url: string): void {
  try {
    if (url) window.localStorage.setItem(PREVIEW_PREFIX + threadId, url);
    else window.localStorage.removeItem(PREVIEW_PREFIX + threadId);
  } catch {
    /* private mode — the address lasts this visit */
  }
}

interface PairedBrowser {
  paired: boolean;
  name: string;
}

export function BrowserPreview({ threadId, seen }: { threadId: string; seen: readonly string[] }) {
  const [address, setAddress] = useState("");
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [browser, setBrowser] = useState<PairedBrowser | null>(null);

  useEffect(() => {
    const saved = readAddress(threadId);
    setAddress(saved);
    setDraft(saved);
    setProblem(null);
  }, [threadId]);

  useEffect(() => {
    let live = true;
    get<{ paired?: unknown; connected?: unknown; browser_name?: unknown; browser_version?: unknown }>("/browser/status")
      .then((s) => {
        if (!live) return;
        const name = [s.browser_name, s.browser_version].filter((x) => typeof x === "string" && x).join(" ");
        setBrowser({ paired: s.paired === true, name });
      })
      .catch(() => {
        if (live) setBrowser(null); // unknown: say nothing rather than guess
      });
    return () => {
      live = false;
    };
  }, []);

  function open(url: string) {
    const clean = url.trim();
    if (!clean) {
      setAddress("");
      writeAddress(threadId, "");
      setProblem(null);
      return;
    }
    if (!isPreviewAddress(clean)) {
      setProblem("Only a page on this PC or your own network opens here, such as http://localhost:3000.");
      return;
    }
    setProblem(null);
    setAddress(clean);
    setDraft(clean);
    writeAddress(threadId, clean);
    setReload((n) => n + 1);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    open(draft);
  }

  return (
    <div data-testid="browser-preview" className="flex h-full min-h-0 flex-col gap-2">
      <form onSubmit={onSubmit} className="flex min-w-0 items-center gap-1.5">
        <label htmlFor={`preview-address-${threadId}`} className="sr-only">
          Address to preview
        </label>
        <input
          id={`preview-address-${threadId}`}
          data-testid="preview-address"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="http://localhost:3000"
          inputMode="url"
          className="min-w-0 flex-1 rounded-lg border hairline bg-transparent px-2.5 py-1.5 font-mono text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/50"
        />
        <button
          type="submit"
          data-testid="preview-open"
          className="inline-flex h-8 shrink-0 items-center rounded-lg px-2.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
        >
          Open
        </button>
        {address ? (
          <button
            type="button"
            aria-label="Reload the preview"
            title="Reload the preview"
            onClick={() => setReload((n) => n + 1)}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <RefreshCw size={13} />
          </button>
        ) : null}
      </form>
      {problem ? (
        <p role="alert" className="text-[12px] text-tone-warn">
          {problem}
        </p>
      ) : null}
      {address ? (
        <>
          <a
            href={address}
            target="_blank"
            rel="noreferrer"
            className="inline-flex w-fit items-center gap-1 text-[12px] text-zinc-500 hover:text-accent-soft"
          >
            <ExternalLink size={12} aria-hidden /> Open in a new tab
          </a>
          <iframe
            key={`${address}#${reload}`}
            data-testid="preview-frame"
            title={`Preview of ${address}`}
            src={address}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            className="min-h-[240px] w-full flex-1 rounded-lg border hairline"
          />
        </>
      ) : (
        <div data-testid="preview-empty" className="space-y-2 py-2">
          <p className="text-[13px] text-zinc-400">
            Nothing to preview yet. When a dev server runs for this folder, open its address here.
          </p>
          {seen.length ? (
            <div className="space-y-1">
              <p className="text-[12px] text-zinc-500">Seen in this session:</p>
              {seen.map((url) => (
                <button
                  key={url}
                  type="button"
                  data-testid="preview-seen"
                  onClick={() => open(url)}
                  className="block max-w-full truncate rounded-md px-1.5 py-0.5 text-left font-mono text-[12px] text-accent-soft hover:bg-white/[0.06]"
                >
                  {url}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      )}
      {browser ? (
        <p data-testid="preview-browser" className="flex items-center gap-1.5 border-t hairline pt-2 text-[12px] text-zinc-500">
          <Globe size={12} aria-hidden className="shrink-0" />
          {browser.paired ? (
            <span>
              Your browser{browser.name ? ` (${browser.name})` : ""} is paired: the session can browse the web with it.
            </span>
          ) : (
            <span>
              No browser is paired, so the session cannot browse the web.{" "}
              <Link href="/computeruse" className="text-accent-soft hover:underline">
                Set up Your browser
              </Link>
            </span>
          )}
        </p>
      ) : null}
    </div>
  );
}
