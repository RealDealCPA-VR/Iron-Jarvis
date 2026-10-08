"use client";

// v1.306.0 — "Share with Build", the quiet row at the foot of "What Jarvis
// knows about you" (idea from agent-personalizer, MIT — no code taken).
//
// A Build pane runs Claude Code or Codex, which never see Jarvis's own
// prompts — so the profile stops at the pane's edge. One switch per CLI found
// on this PC keeps a generated block (the profile + the preferences the user
// said or kept) in that CLI's own instructions file. OFF by default; the
// sentence under each switch says what is written, to which files, and that
// the CLI's maker sees it — before the press, because the press IS consent.
//
// When the user hand-edits (or removes) the block, Jarvis stops writing that
// file and says so here, with Overwrite / Keep yours. An older daemon (404)
// or any failed read hides the row; no CLI found hides it too.
import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { get, post, put } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { decodeShareView, fileLine, SHARE_EXPLAIN, shareWhere, type ShareCli } from "@/lib/profileShare";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const ACTION =
  "rounded px-1 py-px text-[11px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft disabled:opacity-40";
const LABEL = "text-[10.5px] font-semibold uppercase tracking-[0.12em] text-zinc-500";

export function ProfileShareRow({ onSettled }: { onSettled?: () => void } = {}) {
  const [clis, setClis] = useState<ShareCli[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const load = useCallback(async (live: () => boolean = () => true) => {
    try {
      const view = decodeShareView(await get<unknown>("/profile/share"));
      if (live()) setClis(view);
    } catch {
      /* 404 on an older daemon, or a failed read: no row */
    }
  }, []);

  useEffect(() => {
    let live = true;
    // v1.316.0: the card above the Memory tabs says when it stopped growing.
    void load(() => live).finally(() => {
      if (live) onSettled?.();
    });
    return () => {
      live = false;
    };
  }, [load]);

  async function act(cli: string, run: () => Promise<unknown>) {
    if (busy) return;
    setBusy(cli);
    setErrors((e) => {
      const next = { ...e };
      delete next[cli];
      return next;
    });
    try {
      const view = decodeShareView(await run());
      if (view) setClis(view);
      else await load();
    } catch (e) {
      setErrors((prev) => ({ ...prev, [cli]: errText(e) }));
    } finally {
      setBusy(null);
    }
  }

  const shown = (clis ?? []).filter((c) => c.available);
  if (shown.length === 0) return null;

  return (
    <div data-testid="profile-share" className="space-y-2 border-t border-white/[0.05] pt-2.5">
      <p className={LABEL}>Share with Build</p>
      {/* v1.316.0: what is written is the same for every switch, so it is said
          ONCE; each switch's own line keeps its file(s) and names the company
          that sees it — that part is a privacy disclosure and is never
          shared or shortened. */}
      <p data-testid="profile-share-explain" className="text-xs leading-relaxed text-zinc-500">
        {SHARE_EXPLAIN}
      </p>
      {shown.map((c) => {
        const isBusy = busy === c.cli;
        // Off still lists a file whose block could NOT be taken out (locked,
        // a link, damaged markers): turning off must never read as "removed"
        // while the block is still there. Overwrite / Keep yours need it on.
        const needs = c.files.map((f) => ({ f, line: fileLine(f) })).filter((x) => x.line);
        return (
          <div key={c.cli} id={`profile-share-${c.cli}`} className="space-y-1">
            <div className="flex items-center gap-2">
              <button
                type="button"
                role="switch"
                aria-checked={c.on}
                aria-label={`Share my profile with ${c.label} in Build`}
                data-testid={`profile-share-switch-${c.cli}`}
                disabled={isBusy}
                onClick={() => void act(c.cli, () => put("/profile/share", { cli: c.cli, on: !c.on }))}
                className={`relative h-[18px] w-8 shrink-0 rounded-full border transition-colors disabled:opacity-60 ${
                  c.on ? "border-accent/40 bg-accent/30" : "border-white/10 bg-white/[0.05]"
                }`}
              >
                <span
                  className={`absolute top-1/2 h-3 w-3 -translate-y-1/2 rounded-full transition-all ${
                    c.on ? "left-[0.95rem] bg-accent" : "left-[3px] bg-zinc-400"
                  }`}
                />
              </button>
              <span className="text-[12.5px] text-zinc-300">Share my profile with {c.label} in Build</span>
              {isBusy && <Loader2 size={11} className="animate-spin text-zinc-500" />}
              {c.on && c.lastWritten && (
                <span className="text-[10.5px] text-zinc-600">written {timeAgo(c.lastWritten)}</span>
              )}
            </div>
            <p data-testid={`profile-share-sentence-${c.cli}`} className="pl-10 text-[11.5px] leading-relaxed text-zinc-500">
              {shareWhere(c)}
              {c.on && c.omitted > 0 && (
                <> {c.omitted} preference{c.omitted === 1 ? " was" : "s were"} left out to keep it under 4,000 characters.</>
              )}
            </p>
            {needs.map(({ f, line }) => (
              <p
                key={f.path}
                data-testid={`profile-share-drift-${c.cli}`}
                className="pl-10 text-[11.5px] leading-relaxed text-amber-300/90"
              >
                {line}
                {c.on && (f.drift === "edited" || f.drift === "removed" || f.held) && (
                  <span className="ml-1.5 inline-flex items-center gap-0.5">
                    <button
                      type="button"
                      data-testid={`profile-share-overwrite-${c.cli}`}
                      disabled={isBusy}
                      onClick={() => void act(c.cli, () => post(`/profile/share/${c.cli}/overwrite`, { path: f.path }))}
                      title="Put Jarvis's block back in this file"
                      className={ACTION}
                    >
                      Overwrite
                    </button>
                    {!f.held && (
                      <>
                        <span aria-hidden="true" className="text-zinc-600">·</span>
                        <button
                          type="button"
                          data-testid={`profile-share-keep-${c.cli}`}
                          disabled={isBusy}
                          onClick={() => void act(c.cli, () => post(`/profile/share/${c.cli}/keep`, { path: f.path }))}
                          title="Leave your version; Jarvis stops updating this file"
                          className={ACTION}
                        >
                          Keep yours
                        </button>
                      </>
                    )}
                  </span>
                )}
              </p>
            ))}
            {errors[c.cli] && (
              <p data-testid={`profile-share-error-${c.cli}`} className="pl-10 text-[11px] text-rose-300/90">
                {errors[c.cli]}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
