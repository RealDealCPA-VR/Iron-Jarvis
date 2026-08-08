"use client";

/**
 * /train — "Train Jarvis on me" (v1.145.0).
 *
 * The brief asked for one place to add wikis, documents, notes, past
 * conversations, writing samples, and instructions. Iron Jarvis already had a
 * doorway for every one of those — they were just five unrelated pages, so
 * nobody found them as a set. This page is the ON-RAMP, not a sixth store:
 * each step either does its work inline (writing samples, which had no home)
 * or shows what is already connected and links to the page that owns it.
 *
 * Nothing here is fine-tuning. It is identity, memory, retrieval, and
 * preference context — the same substrate the rest of the app reads.
 */

import { useState } from "react";
import Link from "next/link";
import {
  GraduationCap,
  UserRound,
  PenLine,
  Library,
  MessagesSquare,
  FolderSearch,
  Plus,
  Trash2,
  Sparkles,
  Check,
  ArrowRight,
} from "lucide-react";
import { useApi } from "@/lib/useApi";
import { post, put, del, ApiError } from "@/lib/api";
import { PageShell, Reveal } from "@/components/motion";
import { PageHeader } from "@/components/PageHeader";
import { Card, ErrorNote, SuccessNote, Empty, SectionLabel } from "@/components/ui";

interface TrainingStatus {
  about: boolean;
  voice_card: boolean;
  samples: number;
  sample_chars: number;
  memory_bases: number;
  memory_items: number;
  search_roots: number;
  projects: number;
}

interface Sample {
  id: string;
  label: string;
  origin: string;
  chars: number;
  excerpt: string;
}

interface SamplesPayload {
  samples: Sample[];
  total_chars: number;
  max_samples: number;
  min_chars_to_derive: number;
}

interface DeriveResult {
  card: string;
  reason: string;
  samples_used: number;
  source: string;
}

/** One wizard step: a numbered card with a "done" state and a way onward. */
function Step({
  n,
  title,
  done,
  blurb,
  icon,
  children,
}: {
  n: number;
  title: string;
  done: boolean;
  blurb: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <span
            className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
              done
                ? "bg-emerald-500/20 text-emerald-300"
                : "bg-white/[0.06] text-zinc-400"
            }`}
          >
            {done ? <Check size={12} /> : n}
          </span>
          {title}
        </span>
      }
      icon={icon}
    >
      <p className="mb-3 text-[12.5px] leading-relaxed text-zinc-500">{blurb}</p>
      {children}
    </Card>
  );
}

/** A link out to the page that already owns this kind of input. */
function Doorway({ href, label, note }: { href: string; label: string; note: string }) {
  return (
    <Link
      href={href}
      className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.08] px-3 py-2 transition-colors hover:border-accent/40"
    >
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-zinc-200">{label}</div>
        <div className="truncate text-[11.5px] text-zinc-500">{note}</div>
      </div>
      <ArrowRight size={14} className="shrink-0 text-zinc-500" />
    </Link>
  );
}

export default function TrainPage() {
  const status = useApi<TrainingStatus>("/profile/training");
  const samples = useApi<SamplesPayload>("/profile/samples");

  const [label, setLabel] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<DeriveResult | null>(null);
  const [draftCard, setDraftCard] = useState("");
  const [savedCard, setSavedCard] = useState(false);

  function refresh() {
    status.reload();
    samples.reload();
  }

  async function addSample() {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await post("/profile/samples", { label, text });
      setLabel("");
      setText("");
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function removeSample(id: string) {
    try {
      await del(`/profile/samples/${id}`);
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function derive() {
    setBusy(true);
    setError(null);
    setSavedCard(false);
    try {
      const res = await post<DeriveResult>("/profile/voice/derive", {});
      setProposal(res);
      setDraftCard(res.card);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  /** Suggest-don't-act: the derived card is only stored when you save it. */
  async function saveCard() {
    setBusy(true);
    setError(null);
    try {
      await put("/profile", {
        values: { voice_card: draftCard, voice_source: proposal?.source ?? "" },
      });
      setSavedCard(true);
      setProposal(null);
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const st = status.data;
  const rows = samples.data?.samples ?? [];
  const enough = (samples.data?.total_chars ?? 0) >= (samples.data?.min_chars_to_derive ?? 400);
  const full = rows.length >= (samples.data?.max_samples ?? 20);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Train Jarvis on me"
          subtitle="Give it your writing, your notes, and your past conversations. Not fine-tuning — memory, retrieval, and preferences it reads on every request."
        />
      </Reveal>

      {error && <ErrorNote>{error}</ErrorNote>}
      {savedCard && <SuccessNote>Saved — your voice applies from the next message.</SuccessNote>}

      <Reveal>
        <Step
          n={1}
          title="Tell it who you are"
          icon={<UserRound size={15} />}
          done={Boolean(st?.about)}
          blurb="A few lines about your role and your work. This is the single highest-value thing on this page — it rides in every prompt."
        >
          <Doorway
            href="/you"
            label={st?.about ? "About you — written" : "About you — empty"}
            note="Your profile: about you, answer length, reading level, language"
          />
        </Step>
      </Reveal>

      <Reveal>
        <Step
          n={2}
          title="Show it how you write"
          icon={<PenLine size={15} />}
          done={Boolean(st?.voice_card)}
          blurb="Paste things you wrote — emails, posts, notes. Iron Jarvis reads them for STYLE only: it describes your rhythm and vocabulary and never keeps what they were about."
        >
          <div className="space-y-3">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="What is this? (client email, blog post…)"
              className="field py-1.5 text-[13px]"
            />
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={5}
              placeholder="Paste something you wrote…"
              className="field resize-y text-[13px] leading-relaxed"
            />
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void addSample()}
                disabled={busy || !text.trim() || full}
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-[12.5px] font-medium text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-40"
              >
                <Plus size={14} /> Add sample
              </button>
              <button
                type="button"
                onClick={() => void derive()}
                disabled={busy || !enough}
                title={enough ? undefined : "Add more writing first"}
                className="inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-[12.5px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.15] disabled:opacity-40"
              >
                <Sparkles size={14} /> {busy ? "Reading…" : "Read my voice"}
              </button>
              <span className="text-[11.5px] text-zinc-500">
                {samples.data?.total_chars ?? 0} characters
                {!enough && ` — ${samples.data?.min_chars_to_derive ?? 400} needed`}
                {full && " — sample limit reached"}
              </span>
            </div>

            {rows.length > 0 && (
              <div className="space-y-1.5">
                {rows.map((s) => (
                  <div
                    key={s.id}
                    className="flex items-start justify-between gap-3 rounded-lg border border-white/[0.06] px-3 py-2"
                  >
                    <div className="min-w-0">
                      <div className="text-[12.5px] font-medium text-zinc-300">
                        {s.label}{" "}
                        <span className="font-normal text-zinc-600">
                          · {s.chars} chars
                        </span>
                      </div>
                      <div className="truncate text-[11.5px] text-zinc-500">
                        {s.excerpt}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => void removeSample(s.id)}
                      title="Remove this sample"
                      className="shrink-0 rounded-md p-1 text-zinc-500 transition-colors hover:text-rose-400"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {proposal && !proposal.card && (
              <ErrorNote>{proposal.reason}</ErrorNote>
            )}
            {proposal && proposal.card && (
              <div className="rounded-lg border border-accent/25 bg-accent/[0.04] p-3">
                <SectionLabel>
                  Proposed — from {proposal.source}. Edit anything before saving.
                </SectionLabel>
                <textarea
                  value={draftCard}
                  onChange={(e) => setDraftCard(e.target.value)}
                  rows={7}
                  className="mt-2 w-full resize-y rounded-lg border border-white/10 bg-ink-850/60 px-2.5 py-2 text-[12.5px] leading-relaxed text-zinc-200 outline-none focus:border-accent/40"
                />
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void saveCard()}
                    disabled={busy || !draftCard.trim()}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-[12.5px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.15] disabled:opacity-40"
                  >
                    <Check size={14} /> Use this
                  </button>
                  <button
                    type="button"
                    onClick={() => setProposal(null)}
                    className="rounded-lg border border-white/10 px-3 py-1.5 text-[12.5px] font-medium text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-100"
                  >
                    Discard
                  </button>
                </div>
              </div>
            )}
            {rows.length === 0 && !proposal && (
              <Empty>No samples yet — paste one above.</Empty>
            )}
          </div>
        </Step>
      </Reveal>

      <Reveal>
        <Step
          n={3}
          title="Connect your notes and wiki"
          icon={<Library size={15} />}
          done={(st?.memory_bases ?? 0) > 1}
          blurb="An Obsidian vault, a Notion database, a shared drive, or an MCP brain. Once connected, Iron Jarvis retrieves from it on every relevant question — no tool to arm, no folder to name."
        >
          <div className="space-y-2">
            <Doorway
              href="/ltm"
              label={`Memory bases — ${st?.memory_bases ?? 0} connected`}
              note="Obsidian, Notion, Dropbox, Google Drive, OneDrive, SSH, an MCP brain"
            />
            <Doorway
              href="/documents"
              label="Turn a document into a note"
              note="A PDF or Word file becomes searchable knowledge, not one-off chat context"
            />
          </div>
        </Step>
      </Reveal>

      <Reveal>
        <Step
          n={4}
          title="Bring your past conversations"
          icon={<MessagesSquare size={15} />}
          done={(st?.memory_items ?? 0) > 0}
          blurb="Everything another assistant already learned about you. Paste a summary, or import a ChatGPT / Claude / Takeout export — you review every item before it is kept."
        >
          <Doorway
            href="/memory"
            label={`Memory — ${st?.memory_items ?? 0} items`}
            note="Import from another AI, review what is stored, browse the memory graph"
          />
        </Step>
      </Reveal>

      <Reveal>
        <Step
          n={5}
          title="Point it at your files"
          icon={<FolderSearch size={15} />}
          done={(st?.search_roots ?? 0) > 0 || (st?.projects ?? 0) > 0}
          blurb="Folders it may search, and projects that carry their own instructions and knowledge for the work you do repeatedly."
        >
          <div className="space-y-2">
            <Doorway
              href="/filesearch"
              label={`Searchable folders — ${st?.search_roots ?? 0} configured`}
              note="Search your drives by name, contents, or meaning"
            />
            <Doorway
              href="/projects"
              label={`Projects — ${st?.projects ?? 0}`}
              note="Per-project instructions, knowledge, and its own memory bases"
            />
          </div>
        </Step>
      </Reveal>

      <Reveal>
        <Card title="What this is not" icon={<GraduationCap size={15} />}>
          <p className="text-[12.5px] leading-relaxed text-zinc-500">
            None of this trains or fine-tunes a model. Your writing and notes stay on this
            machine; what changes is the context Iron Jarvis assembles before it asks a
            model anything — so the same knowledge applies whether the answer comes from a
            local model or a cloud one, and you can delete any of it at any time.
          </p>
        </Card>
      </Reveal>
    </PageShell>
  );
}
