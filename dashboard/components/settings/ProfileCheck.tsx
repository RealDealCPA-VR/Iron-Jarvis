"use client";

/**
 * One memory profile's "Check" and "Try a test save" (v1.408.0), under its
 * row in Settings › Memory profile. Check asks the daemon to walk the
 * profile (reach the base, search it, load the guide, can it save) and shows
 * each step in a short list; a test save writes one note, in two presses.
 *
 * A 404 from either (an older Iron Jarvis) hides both buttons for every row
 * (`onGone`). The words and decoders live in lib/memoryProfileCheck (pure).
 */

import { useState } from "react";
import { Check, Minus, AlertTriangle } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { ConfirmButton, LoaderInline } from "@/components/ui";
import {
  CHECK_STATE_WORDS,
  decodeCheck,
  decodeTestSave,
  stepTone,
  type CheckStep,
  type TestSave,
} from "@/lib/memoryProfileCheck";

const msg = (e: unknown, fallback: string) => (e as { message?: string })?.message || fallback;

const TONE_CLASS = { ok: "text-tone-success", warn: "text-tone-warn", neutral: "text-zinc-500" } as const;

export function ProfileCheck({ id, onGone }: { id: string; onGone: () => void }) {
  const base = `/brain/profiles/${encodeURIComponent(id)}`;
  const [checking, setChecking] = useState(false);
  const [steps, setSteps] = useState<CheckStep[] | null>(null);
  const [checkError, setCheckError] = useState("");
  const [saved, setSaved] = useState<TestSave | null>(null);
  const [saveError, setSaveError] = useState("");

  async function check() {
    setChecking(true);
    setCheckError("");
    setSteps(null);
    try {
      const s = decodeCheck(await post<unknown>(`${base}/check`));
      if (s === null) setCheckError("The check gave no answer.");
      else setSteps(s);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) onGone();
      else setCheckError(msg(e, "Couldn't check the profile."));
    } finally {
      setChecking(false);
    }
  }

  async function testSave() {
    setSaveError("");
    setSaved(null);
    try {
      const r = decodeTestSave(await post<unknown>(`${base}/test-save`));
      if (r === null) setSaveError("The test save gave no answer.");
      else setSaved(r);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) onGone();
      else setSaveError(msg(e, "Couldn't try a test save."));
    }
  }

  return (
    <div className="w-full space-y-1.5" data-testid={`brain-check-${id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid={`brain-check-run-${id}`}
          onClick={() => void check()}
          disabled={checking}
          className="btn-ghost btn-sm"
        >
          {checking ? <LoaderInline label="Checking…" /> : "Check"}
        </button>
        <ConfirmButton
          variant="calm"
          label="Try a test save"
          confirmLabel="Save a test note?"
          onConfirm={testSave}
        />
      </div>
      {steps && (
        <ul className="space-y-0.5 text-[12px]" data-testid={`brain-check-steps-${id}`}>
          {steps.length === 0 && <li className="text-zinc-500">Nothing was checked.</li>}
          {steps.map((s) => {
            const tone = stepTone(s.ok);
            const Icon = tone === "ok" ? Check : tone === "warn" ? AlertTriangle : Minus;
            return (
              <li
                key={s.step}
                data-testid={`brain-check-step-${s.step}`}
                data-tone={tone}
                className={`flex items-start gap-1.5 ${TONE_CLASS[tone]}`}
              >
                <Icon size={12} className="mt-[3px] shrink-0" aria-hidden="true" />
                <span>
                  <span className="sr-only">
                    {tone === "ok" ? CHECK_STATE_WORDS.pass : tone === "warn" ? CHECK_STATE_WORDS.fail : CHECK_STATE_WORDS.skip}
                    :{" "}
                  </span>
                  {s.words}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {checkError && (
        <p className="text-[12px] text-tone-warn" role="alert" data-testid={`brain-check-error-${id}`}>
          {checkError}
        </p>
      )}
      {saved && (
        <p
          className={`text-[12px] ${saved.ok ? "text-tone-success" : "text-tone-warn"}`}
          data-testid={`brain-test-save-result-${id}`}
          data-tone={saved.ok ? "ok" : "warn"}
          role="status"
        >
          {saved.words}
        </p>
      )}
      {saveError && (
        <p className="text-[12px] text-tone-warn" role="alert" data-testid={`brain-test-save-error-${id}`}>
          {saveError}
        </p>
      )}
    </div>
  );
}
