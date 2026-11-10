"use client";

/**
 * v1.464.0: "Starter skills" on the Templates page.
 *
 * Six bundles ship with the app (business, accounting, marketing, coding,
 * project management, social media). Nothing is loaded until the user presses
 * Load; a loaded skill is an ordinary skill (Skills page, "/" in Chat). The
 * last row loads skills from a GitHub repository: look first, read the
 * warning, then a second press loads them.
 *
 * One read (GET /templates/skill-bundles) when the card mounts and after each
 * press; never polled.
 */

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button, Card, ConfirmButton, WarnNote } from "@/components/ui";
import {
  GITHUB_ARMED,
  GITHUB_HEADING,
  GITHUB_PLACEHOLDER,
  bundleSummary,
  canTick,
  decodeBundles,
  decodeGithubPreview,
  decodeLoad,
  decodeRemove,
  githubLoadLabel,
  loadWords,
  otherFilesWords,
  removeWords,
  stateWords,
  type GithubPreview,
  type SkillBundle,
} from "@/lib/skillBundles";

const errorWords = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "That did not work. Try again.";

function BundleRow({
  bundle,
  onChanged,
}: {
  bundle: SkillBundle;
  onChanged: (words: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const tickable = bundle.skills.filter(canTick);
  const chosen = tickable.filter((s) => picked[s.name] ?? true).map((s) => s.name);

  const load = async (skills: string[] | null) => {
    if (busy) return;
    setBusy(true);
    try {
      const raw = await post(`/templates/skill-bundles/${bundle.id}/load`, skills ? { skills } : {});
      onChanged(loadWords(decodeLoad(raw)));
    } catch (e) {
      onChanged(errorWords(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    try {
      const raw = await post(`/templates/skill-bundles/${bundle.id}/remove`, {});
      onChanged(removeWords(decodeRemove(raw)));
    } catch (e) {
      onChanged(errorWords(e));
    }
  };

  return (
    <li className="border-t border-white/[0.06] py-2 first:border-t-0" data-testid={`skill-bundle-${bundle.id}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`skill-bundle-list-${bundle.id}`}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 grow basis-[14rem] items-start gap-2 rounded-lg px-1 py-1 text-left hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
        >
          {open ? (
            <ChevronDown size={16} className="mt-0.5 shrink-0 text-zinc-500" aria-hidden="true" />
          ) : (
            <ChevronRight size={16} className="mt-0.5 shrink-0 text-zinc-500" aria-hidden="true" />
          )}
          <span className="min-w-0">
            <span className="block text-sm font-medium text-zinc-200">{bundle.name}</span>
            <span className="block text-xs text-zinc-400">{bundle.blurb}</span>
            <span className="block text-xs text-zinc-500" data-testid={`skill-bundle-summary-${bundle.id}`}>
              {bundleSummary(bundle)}
            </span>
          </span>
        </button>
        <div className="flex shrink-0 items-center gap-2">
          {bundle.loaded > 0 && (
            <ConfirmButton
              variant="calm"
              label="Remove"
              confirmLabel="Remove these skills?"
              title="Moves this bundle's skills to the trash folder. A skill you changed is kept."
              testId={`skill-bundle-remove-${bundle.id}`}
              onConfirm={remove}
            />
          )}
          {tickable.length > 0 && (
            <Button
              size="sm"
              disabled={busy}
              data-testid={`skill-bundle-load-${bundle.id}`}
              onClick={() => load(null)}
            >
              {bundle.loaded > 0 ? "Load the rest" : "Load all"}
            </Button>
          )}
        </div>
      </div>
      {open && (
        <div id={`skill-bundle-list-${bundle.id}`} className="mt-2 pl-7">
          <ul className="space-y-1.5">
            {bundle.skills.map((s) => {
              const words = stateWords(s.state);
              const tick = canTick(s);
              return (
                <li key={s.name} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1 shrink-0"
                    aria-label={`Load ${s.name}`}
                    disabled={!tick}
                    checked={tick ? picked[s.name] ?? true : false}
                    onChange={(e) => setPicked((p) => ({ ...p, [s.name]: e.target.checked }))}
                  />
                  <span className="min-w-0">
                    <span className="font-medium text-zinc-300">{s.name}</span>
                    {words && <span className="ml-2 text-xs text-zinc-500">{words}</span>}
                    <span className="block text-xs text-zinc-400">{s.description}</span>
                  </span>
                </li>
              );
            })}
          </ul>
          {tickable.length > 0 && (
            <div className="mt-2">
              <Button
                size="sm"
                disabled={busy || chosen.length === 0}
                data-testid={`skill-bundle-load-chosen-${bundle.id}`}
                onClick={() => load(chosen)}
              >
                Load the ticked ones
              </Button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function GithubRow({ warning, onChanged }: { warning: string; onChanged: (words: string) => void }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [preview, setPreview] = useState<GithubPreview | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [armed, setArmed] = useState(false);

  const chosen = (preview?.skills ?? []).filter((s) => s.canLoad && (picked[s.path] ?? true)).map((s) => s.path);

  const look = async () => {
    if (busy || !url.trim()) return;
    setBusy(true);
    setProblem("");
    setPreview(null);
    setArmed(false);
    setPicked({});
    try {
      const got = decodeGithubPreview(await post("/templates/skill-bundles/github/preview", { url: url.trim() }));
      if (got) setPreview(got);
      else setProblem("GitHub's answer could not be read. Try again.");
    } catch (e) {
      setProblem(errorWords(e));
    } finally {
      setBusy(false);
    }
  };

  const load = async () => {
    if (busy || !preview || chosen.length === 0) return;
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    try {
      const raw = await post("/templates/skill-bundles/github/load", {
        url: url.trim(),
        commit: preview.commit,
        paths: chosen,
        confirm: true,
      });
      onChanged(loadWords(decodeLoad(raw)));
      setPreview(null);
      setArmed(false);
    } catch (e) {
      setProblem(errorWords(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="border-t border-white/[0.06] py-2" data-testid="skill-bundle-github">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="skill-bundle-github-body"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 rounded-lg px-1 py-1 text-left hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
      >
        {open ? (
          <ChevronDown size={16} className="mt-0.5 shrink-0 text-zinc-500" aria-hidden="true" />
        ) : (
          <ChevronRight size={16} className="mt-0.5 shrink-0 text-zinc-500" aria-hidden="true" />
        )}
        <span className="min-w-0">
          <span className="block text-sm font-medium text-zinc-200">{GITHUB_HEADING}</span>
          <span className="block text-xs text-zinc-400">
            Skills someone else wrote and shared in a public repository.
          </span>
        </span>
      </button>
      {open && (
        <div id="skill-bundle-github-body" className="mt-2 space-y-2 pl-7">
          <WarnNote>
            <span data-testid="skill-bundle-github-warning">{preview?.warning || warning}</span>
          </WarnNote>
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void look();
            }}
          >
            <input
              className="field min-w-0 grow basis-[16rem]"
              aria-label="GitHub repository address"
              placeholder={GITHUB_PLACEHOLDER}
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setArmed(false);
              }}
            />
            <Button size="sm" type="submit" disabled={busy || !url.trim()} data-testid="skill-bundle-github-look">
              {busy && !preview ? "Looking…" : "Look"}
            </Button>
          </form>
          {problem && (
            <p role="alert" className="text-sm text-tone-danger" data-testid="skill-bundle-github-problem">
              {problem}
            </p>
          )}
          {preview && (
            <div data-testid="skill-bundle-github-list">
              <p className="text-xs text-zinc-500">
                {preview.repo}, version {preview.commit.slice(0, 7)}
                {preview.more > 0 ? `. ${preview.more} more were not listed.` : ""}
              </p>
              <ul className="mt-1.5 space-y-1.5">
                {preview.skills.map((s) => (
                  <li key={s.path} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1 shrink-0"
                      aria-label={`Load ${s.name}`}
                      disabled={!s.canLoad}
                      checked={s.canLoad ? picked[s.path] ?? true : false}
                      onChange={(e) => {
                        setPicked((p) => ({ ...p, [s.path]: e.target.checked }));
                        setArmed(false);
                      }}
                    />
                    <span className="min-w-0">
                      <span className="font-medium text-zinc-300">{s.name}</span>
                      <span className="block text-xs text-zinc-400">{s.description}</span>
                      {!s.canLoad && s.why && (
                        <span className="block text-xs text-tone-warn">Cannot be loaded: {s.why}.</span>
                      )}
                      {s.canLoad && s.otherFiles > 0 && (
                        <span className="block text-xs text-zinc-500">{otherFilesWords(s.otherFiles)}.</span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  disabled={busy || chosen.length === 0}
                  data-testid="skill-bundle-github-load"
                  onClick={() => void load()}
                >
                  {githubLoadLabel(chosen.length, armed)}
                </Button>
                {armed && (
                  <span className="text-xs text-tone-warn" data-testid="skill-bundle-github-armed">
                    {GITHUB_ARMED}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function SkillBundles({ onLoaded }: { onLoaded?: () => void }) {
  const [bundles, setBundles] = useState<SkillBundle[] | null>(null);
  const [warning, setWarning] = useState("");
  const [note, setNote] = useState("");

  const read = useCallback(async () => {
    try {
      const raw = await get<unknown>("/templates/skill-bundles");
      setBundles(decodeBundles(raw));
      const w = (raw as { github_warning?: unknown } | null)?.github_warning;
      setWarning(typeof w === "string" ? w : "");
    } catch {
      // An older Iron Jarvis, or no answer: the card is simply not drawn.
      setBundles((b) => b ?? []);
    }
  }, []);

  useEffect(() => {
    void read();
  }, [read]);

  const changed = useCallback(
    (words: string) => {
      setNote(words);
      void read();
      onLoaded?.();
    },
    [read, onLoaded],
  );

  if (!bundles || bundles.length === 0) return null;

  return (
    <Card>
      <div data-testid="skill-bundles">
        <h2 className="text-sm font-semibold text-zinc-200">Starter skills</h2>
        <p className="mt-1 text-sm text-zinc-400">
          A skill is a set of instructions Jarvis follows for one kind of task. Load the bundles that
          fit your work. Nothing is loaded until you press Load, and you can change or remove a skill
          afterwards on the Skills page.
        </p>
        <ul className="mt-2">
          {bundles.map((b) => (
            <BundleRow key={b.id} bundle={b} onChanged={changed} />
          ))}
          <GithubRow warning={warning} onChanged={changed} />
        </ul>
        <p role="status" className="mt-2 min-h-5 text-sm text-zinc-300" data-testid="skill-bundles-note">
          {note}
        </p>
      </div>
    </Card>
  );
}
