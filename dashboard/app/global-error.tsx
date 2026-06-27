"use client";

import { useEffect } from "react";
import "./globals.css";

/**
 * Root error boundary. Next renders this *instead of* the root layout when an
 * error escapes the layout itself, so it must supply its own <html>/<body>.
 * Keeps the dark crimson branding and offers a recovery path via reset().
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface for debugging; the daemon/console can pick this up.
    console.error("Iron Jarvis crashed:", error);
  }, [error]);

  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen flex-col items-center justify-center bg-ink-950 px-6 text-zinc-300">
          <div className="app-aura pointer-events-none fixed inset-0 -z-10" />
          <div className="card-surface w-full max-w-md overflow-hidden">
            <header className="flex items-center gap-2 border-b hairline px-5 py-3.5">
              <span className="grid h-7 w-7 place-items-center rounded-lg border border-rose-500/25 bg-rose-500/[0.1] text-rose-300">
                {/* Inline glyph keeps global-error self-contained (no layout deps). */}
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
                  <path d="M12 9v4" />
                  <path d="M12 17h.01" />
                </svg>
              </span>
              <h1 className="text-[13px] font-semibold tracking-wide text-zinc-100">
                Iron Jarvis hit a snag
              </h1>
            </header>
            <div className="p-5">
              <div className="flex items-start gap-2.5 rounded-xl border border-rose-500/25 bg-rose-500/[0.07] px-3 py-2.5 text-sm text-rose-200">
                <span>
                  Something broke while rendering the dashboard. Your daemon and data are
                  unaffected — try reloading this view.
                </span>
              </div>
              {(error.message || error.digest) && (
                <pre className="mt-4 overflow-x-auto rounded-xl border border-white/[0.06] bg-ink-900/80 px-3 py-2.5 font-mono text-[11px] leading-relaxed text-zinc-500">
                  {error.message || "Unknown error"}
                  {error.digest ? `\ndigest: ${error.digest}` : ""}
                </pre>
              )}
              <div className="mt-5 flex items-center gap-3">
                <button type="button" onClick={() => reset()} className="btn-accent">
                  Try again
                </button>
                <a href="/" className="btn-ghost">
                  Go home
                </a>
              </div>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
