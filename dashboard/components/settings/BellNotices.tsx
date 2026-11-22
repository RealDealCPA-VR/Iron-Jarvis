"use client";

/**
 * 17-a (build-out plan R1.8; "Every new notice: its cap and its off switch":
 * the night review's off switch is a Notifications row). The bell rows a kind
 * of notice can be stopped from, drawn on the Notifications card. It is the
 * Settings page's ONE control for each key below (SettingsHome
 * CONTROLLED_IN_SECTION), so Settings › Connections shows no second
 * "Notifications" heading beside the card; the schema row still serves chat
 * ("stop telling me about the night review"), Settings search and
 * `?focus=<key>`, which open this card. A press saves through THE writer
 * (`PUT /settings/values`), so the change has Undo in the ledger like one
 * made in chat.
 */
import { useCallback, useEffect, useState } from "react";
import { Bell } from "lucide-react";
import { get, put } from "@/lib/api";
import { Card, ErrorNote } from "@/components/ui";

export interface BellSwitch {
  key: string;
  label: string;
  hint: string;
}

/** The schema rows' own words (`settings/schema.py`). */
export const BELL_SWITCHES: readonly BellSwitch[] = [
  {
    key: "night_review_notice",
    label: "Tell me in the bell what the night review found",
    hint:
      "One row in the bell the morning after the night review made suggestions. Off: they still wait " +
      "in Improvements, and nothing rings.",
  },
];

export const BELL_CARD_TITLE = "In the bell";
export const BELL_SAVE_FAILED = "Couldn't change the setting. Try again.";

export function BellNotices() {
  // null = not read yet, or unreadable: a switch whose state is unknown is
  // not drawn (it could show the wrong side).
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState("");
  const [problem, setProblem] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await get<{ values?: Record<string, unknown> }>("/settings/values");
      setValues(r?.values ?? {});
    } catch {
      setValues(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function press(key: string, on: boolean) {
    setBusy(key);
    setProblem("");
    try {
      await put("/settings/values", { values: { [key]: on } });
      setValues((v) => ({ ...(v ?? {}), [key]: on }));
    } catch {
      setProblem(BELL_SAVE_FAILED);
    } finally {
      setBusy("");
    }
  }

  if (values === null) return null;
  return (
    <Card title={BELL_CARD_TITLE} icon={<Bell size={15} />}>
      <ul className="space-y-3" data-testid="bell-notices">
        {BELL_SWITCHES.map((sw) => {
          // Not set reads as on (the setting's default).
          const on = values[sw.key] !== false;
          return (
            <li key={sw.key} className="flex items-start gap-3">
              <button
                type="button"
                role="switch"
                aria-checked={on}
                aria-label={sw.label}
                data-testid={`bell-switch-${sw.key}`}
                disabled={busy === sw.key}
                onClick={() => void press(sw.key, !on)}
                className={`relative mt-0.5 h-[18px] w-8 shrink-0 rounded-full border transition-colors disabled:opacity-60 ${
                  on ? "border-accent/40 bg-accent/30" : "border-white/10 bg-white/[0.05]"
                }`}
              >
                <span
                  className={`absolute top-1/2 h-3 w-3 -translate-y-1/2 rounded-full transition-all ${
                    on ? "left-[0.95rem] bg-accent" : "left-[3px] bg-zinc-400"
                  }`}
                />
              </button>
              <span className="min-w-0">
                <span className="block text-[13px] text-zinc-200">{sw.label}</span>
                <span className="block text-[12px] leading-relaxed text-zinc-500">{sw.hint}</span>
              </span>
            </li>
          );
        })}
      </ul>
      {problem && (
        <div className="mt-3">
          <ErrorNote>{problem}</ErrorNote>
        </div>
      )}
    </Card>
  );
}
