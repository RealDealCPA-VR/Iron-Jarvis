"use client";

/**
 * /you — the identity spine's control surface (v1.144.0).
 *
 * This is not a settings page for one feature: what is saved here is injected
 * into EVERY system prompt Iron Jarvis builds — chat, the streamed chat, your
 * phone, agent sessions, and the round table — so it is what makes the app
 * sound like one assistant regardless of which model answered.
 *
 * The preview panel is load-bearing, not decoration. It renders the EXACT
 * string the daemon appends (GET /profile returns it, produced by the same
 * renderer the seams call), because a preferences page whose effect you cannot
 * see is one people fill in once and then quietly stop trusting.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  UserRound,
  Save,
  RotateCcw,
  Accessibility,
  Languages,
  Eye,
  MessageSquareQuote,
} from "lucide-react";
import { useApi } from "@/lib/useApi";
import { post, put, ApiError } from "@/lib/api";
import { PageShell, Reveal } from "@/components/motion";
import { PageHeader } from "@/components/PageHeader";
import { Card, ErrorNote, SuccessNote, Spinner, SectionLabel } from "@/components/ui";

interface Profile {
  enabled: boolean;
  about: string;
  tone: string;
  writing_style: string;
  formatting: string;
  formatting_rules: string;
  reading_level: string;
  response_length: string;
  accessibility: string;
  language: string;
  enforce_language: boolean;
  voice_card: string;
  voice_source: string;
}

interface ProfilePayload {
  profile: Profile;
  preview: string;
  preview_chars: number;
  preview_limit: number;
}

interface PresetOption {
  key: string;
  label: string;
}

interface ProfileOptions {
  tone: PresetOption[];
  writing_style: PresetOption[];
  formatting: PresetOption[];
  reading_level: PresetOption[];
  response_length: PresetOption[];
  accessibility: PresetOption[];
  language: { code: string; label: string }[];
}

const EMPTY: Profile = {
  enabled: true,
  about: "",
  tone: "",
  writing_style: "",
  formatting: "",
  formatting_rules: "",
  reading_level: "",
  response_length: "",
  accessibility: "",
  language: "",
  enforce_language: true,
  voice_card: "",
  voice_source: "",
};

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full border transition-colors ${
        checked ? "border-accent/40 bg-accent/30" : "border-white/10 bg-white/[0.05]"
      }`}
    >
      <span
        className={`absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full transition-all ${
          checked ? "left-[1.6rem] bg-accent shadow-glow-sm" : "left-1 bg-zinc-400"
        }`}
      />
    </button>
  );
}

/** A labelled select over one preset vocabulary. "" is always "No preference". */
function PresetSelect({
  label,
  hint,
  value,
  options,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  options: PresetOption[];
  onChange: (v: string) => void;
}) {
  // A saved FREE-TEXT value (the store accepts any string) must stay visible
  // and selected instead of silently reverting to "No preference".
  const custom = value && !options.some((o) => o.key === value) ? value : "";
  return (
    <label className="block">
      <span className="text-[12.5px] font-medium text-zinc-300">{label}</span>
      {hint && <span className="mt-0.5 block text-[11.5px] text-zinc-500">{hint}</span>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="field mt-1.5 py-1.5 text-[13px]"
      >
        <option value="">No preference</option>
        {options.map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
        {custom && <option value={custom}>{custom} (yours)</option>}
      </select>
    </label>
  );
}

function TextArea({
  label,
  hint,
  value,
  rows = 4,
  placeholder,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  rows?: number;
  placeholder?: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-[12.5px] font-medium text-zinc-300">{label}</span>
      {hint && <span className="mt-0.5 block text-[11.5px] text-zinc-500">{hint}</span>}
      <textarea
        value={value}
        rows={rows}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="field mt-1.5 resize-y text-[13px] leading-relaxed"
      />
    </label>
  );
}

export default function YouPage() {
  const loaded = useApi<ProfilePayload>("/profile");
  const options = useApi<ProfileOptions>("/profile/options");

  const [draft, setDraft] = useState<Profile>(EMPTY);
  const [preview, setPreview] = useState<ProfilePayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (loaded.data) {
      setDraft(loaded.data.profile);
      setPreview(loaded.data);
    }
  }, [loaded.data]);

  const dirty = useMemo(() => {
    if (!preview) return false;
    return (Object.keys(EMPTY) as (keyof Profile)[]).some(
      (k) => draft[k] !== preview.profile[k],
    );
  }, [draft, preview]);

  function set<K extends keyof Profile>(key: K, value: Profile[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    setSaved(false);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await put<ProfilePayload>("/profile", { values: draft });
      setPreview(res);
      setDraft(res.profile);
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  /** Accessibility modes go through their own endpoint: turning one on SEEDS
   *  the companion fields below (only the ones you left empty), which is why
   *  it saves immediately and refreshes the draft rather than staying local. */
  async function applyAccessibility(mode: string) {
    setSaving(true);
    setError(null);
    try {
      const res = await post<ProfilePayload>("/profile/accessibility", { mode });
      setPreview(res);
      setDraft(res.profile);
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const opts = options.data;
  const overBudget = (preview?.preview_chars ?? 0) > (preview?.preview_limit ?? 1);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="You"
          subtitle="How Iron Jarvis writes to you — applied to every model it uses, local or cloud, in chat and in agent runs."
          actions={
            <div className="flex items-center gap-2">
              {dirty && (
                <button
                  type="button"
                  onClick={() => preview && setDraft(preview.profile)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-100"
                >
                  <RotateCcw size={14} /> Discard
                </button>
              )}
              <button
                type="button"
                onClick={() => void save()}
                disabled={saving || !dirty}
                className="inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-sm font-medium text-accent-soft transition-colors hover:bg-accent/[0.15] disabled:opacity-40"
              >
                <Save size={14} /> {saving ? "Saving…" : "Save"}
              </button>
            </div>
          }
        />
      </Reveal>

      {loaded.loading && !preview && <Spinner label="Loading your profile…" />}
      {error && <ErrorNote>{error}</ErrorNote>}
      {saved && !dirty && <SuccessNote>Saved — it applies from your next message.</SuccessNote>}

      <Reveal>
        <Card
          title="Use my profile"
          icon={<UserRound size={15} />}
          right={
            <Toggle
              checked={draft.enabled}
              onChange={(v) => set("enabled", v)}
              label="Use my profile"
            />
          }
        >
          <p className="text-[12.5px] leading-relaxed text-zinc-500">
            Off sends nothing at all — the models answer exactly as they did before you
            filled this in. Nothing here is deleted while it is off.
          </p>
        </Card>
      </Reveal>

      <Reveal>
        <Card title="About you" icon={<UserRound size={15} />}>
          <TextArea
            label="Who you are and what you work on"
            hint="Written into every prompt, so keep it to what changes an answer: your role, your field, what you are usually doing."
            rows={5}
            placeholder="I run a small CPA firm. Most questions are tax, bookkeeping, or the software I build for the practice."
            value={draft.about}
            onChange={(v) => set("about", v)}
          />
        </Card>
      </Reveal>

      <Reveal>
        <Card title="How you want answers" icon={<MessageSquareQuote size={15} />}>
          <div className="grid gap-4 sm:grid-cols-2">
            <PresetSelect
              label="Length"
              value={draft.response_length}
              options={opts?.response_length ?? []}
              onChange={(v) => set("response_length", v)}
            />
            <PresetSelect
              label="Reading level"
              value={draft.reading_level}
              options={opts?.reading_level ?? []}
              onChange={(v) => set("reading_level", v)}
            />
            <PresetSelect
              label="Formatting"
              value={draft.formatting}
              options={opts?.formatting ?? []}
              onChange={(v) => set("formatting", v)}
            />
            <PresetSelect
              label="Tone"
              hint="Not on the list? Type your own below — it is used word for word."
              value={draft.tone}
              options={opts?.tone ?? []}
              onChange={(v) => set("tone", v)}
            />
            <PresetSelect
              label="Writing style"
              value={draft.writing_style}
              options={opts?.writing_style ?? []}
              onChange={(v) => set("writing_style", v)}
            />
          </div>
          <div className="mt-4">
            <TextArea
              label="Your own rules"
              hint="One per line. Each becomes an instruction, exactly as written."
              rows={3}
              placeholder={"No emoji.\nPut the answer first, then the reasoning."}
              value={draft.formatting_rules}
              onChange={(v) => set("formatting_rules", v)}
            />
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <Card title="Accessibility" icon={<Accessibility size={15} />}>
          <p className="mb-3 text-[12.5px] leading-relaxed text-zinc-500">
            Turning a mode on also fills in the settings above that you left empty — so you
            can start from the mode and then change anything you want. Your existing choices
            are never overwritten.
          </p>
          <div className="flex flex-wrap gap-2">
            {[{ key: "", label: "Off" }, ...(opts?.accessibility ?? [])].map((m) => (
              <button
                key={m.key || "off"}
                type="button"
                onClick={() => void applyAccessibility(m.key)}
                className={`rounded-lg border px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
                  draft.accessibility === m.key
                    ? "border-accent/40 bg-accent/[0.10] text-accent-soft"
                    : "border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-100"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <Card title="Language" icon={<Languages size={15} />}>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="text-[12.5px] font-medium text-zinc-300">Answer in</span>
              <select
                value={draft.language}
                onChange={(e) => set("language", e.target.value)}
                className="field mt-1.5 py-1.5 text-[13px]"
              >
                <option value="">Whatever I write in</option>
                {(opts?.language ?? []).map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex items-start gap-3 pt-1">
              <Toggle
                checked={draft.enforce_language}
                onChange={(v) => set("enforce_language", v)}
                label="Check and fix the reply"
              />
              <div>
                <div className="text-[12.5px] font-medium text-zinc-300">
                  Check and fix the reply
                </div>
                <p className="mt-0.5 text-[11.5px] leading-relaxed text-zinc-500">
                  When a reply comes back in another writing system, ask that model once to
                  rewrite it. If it still won&apos;t, you get the original with a note —
                  never a silent second guess. Off leaves it as an instruction only.
                </p>
              </div>
            </div>
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <Card title="Your voice" icon={<MessageSquareQuote size={15} />}>
          <TextArea
            label="How you write"
            hint="Describe your own rhythm and vocabulary and Iron Jarvis will match it — without ever answering less of your question because of it."
            rows={4}
            placeholder="Short sentences. Plain words. No throat-clearing before the point."
            value={draft.voice_card}
            onChange={(v) => set("voice_card", v)}
          />
          {draft.voice_source && (
            <p className="mt-2 text-[11.5px] text-zinc-500">From: {draft.voice_source}</p>
          )}
          <p className="mt-2 text-[11.5px] text-zinc-500">
            Rather not describe it yourself?{" "}
            <Link href="/train" className="text-accent-soft hover:underline">
              Paste a few things you wrote
            </Link>{" "}
            and Iron Jarvis will work it out — you edit the result before it is saved.
          </p>
        </Card>
      </Reveal>

      <Reveal>
        <Card title="What the model sees" icon={<Eye size={15} />}>
          <SectionLabel>
            {preview?.preview_chars ?? 0} of {preview?.preview_limit ?? 0} characters
            {overBudget && " — trimmed to fit"}
          </SectionLabel>
          {preview?.preview ? (
            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-white/[0.06] bg-ink-850/60 p-3 text-[12px] leading-relaxed text-zinc-400">
              {preview.preview}
            </pre>
          ) : (
            <p className="mt-2 text-[12.5px] text-zinc-500">
              Nothing yet — an empty profile adds nothing to the prompt, so answers are
              exactly what the model would give on its own.
            </p>
          )}
          {dirty && (
            <p className="mt-2 text-[11.5px] text-amber-400/80">
              Unsaved changes — this preview updates when you save.
            </p>
          )}
        </Card>
      </Reveal>
    </PageShell>
  );
}
