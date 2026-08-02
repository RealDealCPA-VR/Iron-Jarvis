"use client";

// Bring memories from another AI (v1.123.0; categorized export v1.129.0).
// No provider exposes a memory API, so this card powers the two honest
// lanes: hand your model the predefined export prompt (categorized —
// Instructions / Identity / Career / Projects / Preferences, dated lines)
// and paste the reply (deterministic parse, works offline), or drop the
// provider's official data export (distilled by a real model — never
// fabricated). Whatever the lane, extraction only produces CANDIDATES: a
// checkbox review — grouped by category when the export carries one —
// decides what actually becomes memory, with already-known facts flagged
// and pre-unchecked. Imports land in their own provenance base
// ("chatgpt-memories"), so recall and the graph always show where a fact
// came from — and one import stays deletable as a unit.

import { useEffect, useRef, useState } from "react";
import { Check, ClipboardCopy, FileUp, Import, Sparkles } from "lucide-react";

import { get, post, ApiError } from "@/lib/api";
import { Card, ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";
import { useFocusRef } from "@/lib/useFocusRef";

const PROVIDERS = [
  { key: "chatgpt", label: "ChatGPT" },
  { key: "claude", label: "Claude" },
  { key: "gemini", label: "Gemini" },
  { key: "grok", label: "Grok" },
  { key: "other", label: "Another AI" },
];

// Display fallback only — the daemon owns the canonical prompt
// (GET /memory/import/prompt, same module as its parser) and replaces
// this on mount so the ask and the read-back can never drift.
const FALLBACK_PROMPT =
  "Export all of my stored memories and any context you've learned about " +
  "me from past conversations, as a categorized list (Instructions, " +
  "Identity, Career, Projects, Preferences), one entry per line formatted " +
  "as [YYYY-MM-DD] - Entry ([unknown] when undated), wrapped in a single " +
  "code block.";

const CATEGORY_ORDER = [
  "Instructions",
  "Identity",
  "Career",
  "Projects",
  "Preferences",
];

const MAX_EXPORT_MB = 100;

/** Order candidates into labeled groups: known categories first (canonical
 *  order), stray labels next, uncategorized last (unlabeled). Indices are
 *  kept because `checked` is positional over the flat candidate list. */
function groupCandidates(cands: { category?: string }[]) {
  const byLabel = new Map<string, number[]>();
  cands.forEach((c, i) => {
    const label = c.category ?? "";
    byLabel.set(label, [...(byLabel.get(label) ?? []), i]);
  });
  const ordered: { label: string; indices: number[] }[] = [];
  for (const label of CATEGORY_ORDER) {
    const idx = byLabel.get(label);
    if (idx) {
      ordered.push({ label, indices: idx });
      byLabel.delete(label);
    }
  }
  for (const [label, indices] of byLabel) {
    if (label) ordered.push({ label, indices });
  }
  const rest = byLabel.get("");
  if (rest) ordered.push({ label: "", indices: rest });
  return ordered;
}

interface Candidate {
  text: string;
  duplicate: boolean;
  category?: string;
  date?: string;
}

interface PreviewResult {
  candidates: Candidate[];
  count: number;
  distilled: boolean;
  structured?: boolean;
  provider: string;
}

export function ImportFromAI({ onImported }: { onImported?: () => void }) {
  // Deep-link target: /memory?scope=longterm&focus=import-ai
  const focusRef = useFocusRef<HTMLDivElement>("import-ai");
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [provider, setProvider] = useState("chatgpt");
  const [pasteText, setPasteText] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [checked, setChecked] = useState<boolean[]>([]);
  const [distilled, setDistilled] = useState(false);
  const [prompt, setPrompt] = useState(FALLBACK_PROMPT);
  const [promptOpen, setPromptOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    get<{ prompt: string }>("/memory/import/prompt")
      .then((out) => {
        if (alive && out.prompt) setPrompt(out.prompt);
      })
      .catch(() => {
        /* fallback copy stays usable */
      });
    return () => {
      alive = false;
    };
  }, []);

  function applyPreview(out: PreviewResult) {
    setCandidates(out.candidates);
    // Already-known facts arrive pre-unchecked — importing them again would
    // only duplicate the store; the user can still tick them back on.
    setChecked(out.candidates.map((c) => !c.duplicate));
    setDistilled(out.distilled);
    if (out.provider) setProvider(out.provider);
  }

  async function copyPrompt() {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable — the text is visible to select manually */
    }
  }

  async function previewPaste() {
    if (!pasteText.trim() || busy) return;
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const out = await post<PreviewResult>("/memory/import/preview", {
        text: pasteText,
        provider,
      });
      applyPreview(out);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function previewFile(f: File) {
    if (f.size > MAX_EXPORT_MB * 1024 * 1024) {
      setError(`That file is over ${MAX_EXPORT_MB} MB — export archives should be far smaller.`);
      return;
    }
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const content_b64 = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onerror = () => reject(new Error("could not read the file"));
        r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
        r.readAsDataURL(f);
      });
      const up = await post<{ path: string }>("/documents/upload", {
        filename: f.name,
        content_b64,
      });
      const out = await post<PreviewResult>("/memory/import/preview", {
        path: up.path,
        provider,
      });
      applyPreview(out);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function importChecked() {
    if (!candidates || importing) return;
    const entries = candidates
      .filter((_, i) => checked[i])
      .map((c) => ({ text: c.text, category: c.category ?? "", date: c.date ?? "" }));
    if (entries.length === 0) return;
    setImporting(true);
    setError(null);
    try {
      const out = await post<{ added: number; source: string }>(
        "/memory/import/commit",
        { entries, provider },
      );
      setOk(
        `Imported ${out.added} memor${out.added === 1 ? "y" : "ies"} into ` +
          `“${out.source}” — recall and the memory graph see them now.`,
      );
      setCandidates(null);
      setChecked([]);
      setPasteText("");
      onImported?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  }

  const selectedCount = checked.filter(Boolean).length;

  return (
    <div ref={focusRef}>
      <Card title="Bring memories from another AI" icon={<Import size={15} />}>
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] uppercase tracking-[0.1em] text-zinc-400">
              From
            </span>
            {PROVIDERS.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => setProvider(p.key)}
                className={`rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
                  provider === p.key
                    ? "border-accent/40 bg-accent/[0.08] text-accent-soft"
                    : "border-white/[0.08] text-zinc-400 hover:bg-white/[0.03]"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
            <p className="text-[12px] text-zinc-400">
              1 · Ask it to export what it remembers — paste this prompt there
              (it returns memories by category: Instructions, Identity, Career,
              Projects, Preferences — dated, your words preserved):
            </p>
            <div className="mt-1.5 flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <pre
                  className={`whitespace-pre-wrap rounded-lg bg-black/30 px-2.5 py-2 font-mono text-[11.5px] leading-relaxed text-zinc-300 ${
                    promptOpen ? "max-h-80 overflow-y-auto" : "max-h-20 overflow-hidden"
                  }`}
                >
                  {prompt}
                </pre>
                <button
                  type="button"
                  onClick={() => setPromptOpen((v) => !v)}
                  className="mt-1 text-[11.5px] text-zinc-500 transition-colors hover:text-accent-soft"
                >
                  {promptOpen ? "Collapse the prompt" : "Show the full prompt"}
                </button>
              </div>
              <button
                type="button"
                onClick={() => void copyPrompt()}
                className="btn-ghost shrink-0 px-2.5 py-1.5 text-[12px]"
              >
                {copied ? (
                  <>
                    <Check size={13} className="text-emerald-300" /> Copied
                  </>
                ) : (
                  <>
                    <ClipboardCopy size={13} /> Copy
                  </>
                )}
              </button>
            </div>
            <p className="mt-3 text-[12px] text-zinc-400">
              2 · Paste its reply here:
            </p>
            <textarea
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              rows={4}
              placeholder={
                "## Identity\n[2024-11-02] - You are a CPA running a tax firm…\n\n## Preferences\n[unknown] - You prefer concise answers…"
              }
              aria-label="Pasted memory list"
              className="field mt-1.5 resize-y font-mono text-[12.5px]"
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void previewPaste()}
                disabled={busy || !pasteText.trim()}
                className="btn-accent px-3 py-1.5 text-[12.5px] disabled:opacity-50"
              >
                {busy ? <LoaderInline label="Reading…" /> : <>Preview memories</>}
              </button>
              <span className="text-[11.5px] text-zinc-600">or</span>
              <input
                ref={fileRef}
                type="file"
                accept=".zip,.json,.txt,.md"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void previewFile(f);
                }}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="btn-ghost px-2.5 py-1.5 text-[12px] disabled:opacity-50"
                title="ChatGPT/Claude data-export zip or Google Takeout — distilled by a real model"
              >
                <FileUp size={13} /> Upload a data export
              </button>
            </div>
          </div>

          {candidates && (
            <div className="rounded-xl border border-accent/20 bg-accent/[0.04] p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-[12.5px] text-zinc-300">
                  {candidates.length} memor{candidates.length === 1 ? "y" : "ies"} found
                  {distilled ? " (distilled from the export)" : ""} — untick anything
                  you don&apos;t want kept:
                </p>
                <div className="flex gap-2 text-[11.5px]">
                  <button
                    type="button"
                    onClick={() => setChecked(candidates.map(() => true))}
                    className="text-zinc-500 transition-colors hover:text-accent-soft"
                  >
                    all
                  </button>
                  <button
                    type="button"
                    onClick={() => setChecked(candidates.map(() => false))}
                    className="text-zinc-500 transition-colors hover:text-accent-soft"
                  >
                    none
                  </button>
                </div>
              </div>
              <div className="mt-2 max-h-72 space-y-2 overflow-y-auto pr-1">
                {groupCandidates(candidates).map((g) => (
                  <div key={g.label || "·uncategorized"}>
                    {g.label && (
                      <p className="mb-0.5 text-[10.5px] uppercase tracking-[0.12em] text-accent-soft/80">
                        {g.label}
                      </p>
                    )}
                    <ul className="space-y-1">
                      {g.indices.map((i) => {
                        const c = candidates[i];
                        return (
                          <li key={i}>
                            <label className="flex cursor-pointer items-start gap-2 rounded-lg px-1.5 py-1 transition-colors hover:bg-white/[0.03]">
                              <input
                                type="checkbox"
                                checked={checked[i] ?? false}
                                onChange={() =>
                                  setChecked((prev) =>
                                    prev.map((v, j) => (j === i ? !v : v)),
                                  )
                                }
                                className="mt-0.5 accent-accent"
                              />
                              <span className="min-w-0 flex-1 text-[12.5px] leading-snug text-zinc-300">
                                {c.date && (
                                  <span className="mr-1.5 font-mono text-[10.5px] text-zinc-500">
                                    {c.date}
                                  </span>
                                )}
                                {c.text}
                                {c.duplicate && (
                                  <span className="ml-2 rounded-full border border-amber-400/25 bg-amber-400/[0.08] px-1.5 py-px text-[10px] text-amber-200/90">
                                    already known
                                  </span>
                                )}
                              </span>
                            </label>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ))}
              </div>
              <button
                type="button"
                onClick={() => void importChecked()}
                disabled={importing || selectedCount === 0}
                className="btn-accent mt-2.5 px-3 py-1.5 text-[12.5px] disabled:opacity-50"
              >
                {importing ? (
                  <LoaderInline label="Importing…" />
                ) : (
                  <>
                    <Sparkles size={13} /> Import {selectedCount} memor
                    {selectedCount === 1 ? "y" : "ies"}
                  </>
                )}
              </button>
            </div>
          )}

          {ok && <SuccessNote>{ok}</SuccessNote>}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      </Card>
    </div>
  );
}
