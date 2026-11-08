"use client";

import { useEffect } from "react";
import { RotateCcw, Home } from "lucide-react";
import Link from "next/link";
import { Card, ErrorNote } from "@/components/ui";
import { PRESS_DETAILS_WORDS as DETAILS_WORDS } from "@/lib/pressFailure";

/**
 * Route-level error boundary. Unlike global-error, this renders *inside* the
 * root layout (sidebar/topbar stay put), so it only needs the branded card.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Iron Jarvis page error:", error);
  }, [error]);

  return (
    <div className="mx-auto max-w-md py-10">
      <Card title="This view ran into a problem">
        <ErrorNote>
          Something broke while loading this page. The rest of Iron Jarvis is still running.
          Try again, or go home.
        </ErrorNote>
        {/* v1.455.0 (Wave F, item 17): the raw text is for whoever is
            troubleshooting — folded under Details, never forced on the user. */}
        {(error.message || error.digest) && (
          <details data-testid="page-error-details" className="mt-4 text-[12px] text-zinc-500">
            <summary className="cursor-pointer select-none">{DETAILS_WORDS}</summary>
            <pre className="mt-2 overflow-x-auto rounded-xl border border-white/[0.06] bg-ink-900/80 px-3 py-2.5 font-mono text-[11px] leading-relaxed text-zinc-500">
              {error.message || "Unknown error"}
              {error.digest ? `\ndigest: ${error.digest}` : ""}
            </pre>
          </details>
        )}
        <div className="mt-5 flex items-center gap-3">
          <button type="button" onClick={() => reset()} className="btn-accent">
            <RotateCcw size={15} />
            Try again
          </button>
          <Link href="/" className="btn-ghost">
            <Home size={15} />
            Go home
          </Link>
        </div>
      </Card>
    </div>
  );
}
