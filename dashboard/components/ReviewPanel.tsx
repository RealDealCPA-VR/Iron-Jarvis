"use client";

import { useState } from "react";
import { Check, X, GitBranch, ShieldCheck } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import type { Review } from "@/lib/types";
import { Card, Badge, ErrorNote, SuccessNote, SectionLabel, LoaderInline } from "./ui";

export function ReviewPanel({
  sessionId,
  review,
  onAction,
}: {
  sessionId: string;
  review: Review;
  onAction?: () => void;
}) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "approve" | "reject") {
    setBusy(kind);
    setError(null);
    try {
      const res = await post<Record<string, unknown>>(`/reviews/${sessionId}/${kind}`);
      setResult(
        kind === "approve" ? `Approved — merged ${res?.merged ?? ""}`.trim() : "Rejected.",
      );
      onAction?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="Review required"
      icon={<ShieldCheck size={15} />}
      right={
        <span className="flex items-center gap-3 text-xs text-zinc-500">
          {review.branch && (
            <span className="flex items-center gap-1.5 font-mono text-zinc-400">
              <GitBranch size={13} /> {review.branch}
            </span>
          )}
          <span className="flex items-center gap-1.5">
            risk <Badge value={review.risk} />
          </span>
        </span>
      }
    >
      <div className="space-y-4">
        <div>
          <SectionLabel>Changed files ({review.changed_files?.length ?? 0})</SectionLabel>
          {review.changed_files?.length ? (
            <ul className="mt-1.5 space-y-1 font-mono text-xs text-zinc-300">
              {review.changed_files.map((f) => (
                <li key={f} className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-2.5 py-1.5">
                  {f}
                </li>
              ))}
            </ul>
          ) : (
            <div className="mt-1.5 text-xs text-zinc-500">No file changes.</div>
          )}
        </div>

        <div>
          <SectionLabel>Diff</SectionLabel>
          <pre className="mt-1.5 max-h-80 overflow-auto rounded-xl border border-white/[0.06] bg-ink-950 p-3 text-xs leading-relaxed">
            <code>{renderDiff(review.diff)}</code>
          </pre>
        </div>

        {result ? (
          <SuccessNote>{result}</SuccessNote>
        ) : (
          <div className="flex items-center gap-3">
            <button
              onClick={() => act("approve")}
              disabled={busy !== null}
              className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
            >
              {busy === "approve" ? <LoaderInline label="Approving…" /> : <><Check size={15} /> Approve & merge</>}
            </button>
            <button
              onClick={() => act("reject")}
              disabled={busy !== null}
              className="inline-flex items-center gap-2 rounded-xl border border-rose-500/40 px-4 py-2 text-sm font-semibold text-rose-200 transition-colors hover:bg-rose-500/10 disabled:opacity-40"
            >
              {busy === "reject" ? <LoaderInline label="Rejecting…" /> : <><X size={15} /> Reject</>}
            </button>
          </div>
        )}

        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

function renderDiff(diff: string) {
  if (!diff) return "(empty diff)";
  return diff.split("\n").map((line, i) => {
    let color = "text-zinc-400";
    if (line.startsWith("+") && !line.startsWith("+++")) color = "text-emerald-300";
    else if (line.startsWith("-") && !line.startsWith("---")) color = "text-rose-300";
    else if (line.startsWith("@@")) color = "text-accent-soft";
    else if (line.startsWith("diff ") || line.startsWith("index ")) color = "text-zinc-500";
    return (
      <div key={i} className={color}>
        {line || " "}
      </div>
    );
  });
}
