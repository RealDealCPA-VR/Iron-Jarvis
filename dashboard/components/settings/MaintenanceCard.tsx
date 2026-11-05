"use client";

/**
 * Settings › System › Maintenance (calm UI redesign S10): back up now, the
 * backup/restore tools and a daemon restart — the same calls the old Settings
 * page's Maintenance card made, lifted out so the System group can show them.
 *
 * v1.354.0 (G59): the words name the app, never its engine: "Restart Iron
 * Jarvis", which stops tidily and the desktop app starts it again.
 */

import { useEffect, useState } from "react";
import { DatabaseBackup, Wrench } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import { isDesktopShell, requestTidyRestart } from "@/lib/desktopShell";
import { Card, ConfirmButton, ErrorNote, LoaderInline, SectionLabel, SuccessNote } from "@/components/ui";
import { MaintenanceTools } from "@/components/settings/MaintenanceTools";
import { RESTART_WORDS } from "@/lib/restartWords";

export function MaintenanceCard() {
  const { refresh } = useDaemon();
  // v1.433.0: read after mount (the server render has no window), so the
  // first paint and the hydrated one agree.
  const [desktop, setDesktop] = useState(false);
  useEffect(() => setDesktop(isDesktopShell()), []);
  const [backupBusy, setBackupBusy] = useState(false);
  const [backupsVersion, setBackupsVersion] = useState(0);
  const [restarting, setRestarting] = useState(false);
  const [ok, setOk] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function backupNow() {
    setOk(null);
    setErr(null);
    setBackupBusy(true);
    try {
      const r = await post<{
        ok: boolean;
        result: string;
        mirror?: { configured?: boolean; dir?: string; last?: { ok?: boolean } | null };
      }>("/diagnostics/repair", { action: "backup_now" });
      if (r.ok) {
        const m = r.mirror;
        const copy = m?.configured
          ? m.last?.ok
            ? ` — and copied to ${m.dir}`
            : ` — but the copy to ${m.dir} did not complete (see below)`
          : "";
        setOk(`Backup written to ${r.result}${copy}`);
        setBackupsVersion((v) => v + 1);
      } else setErr("Iron Jarvis says the backup did not finish.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBackupBusy(false);
    }
  }

  async function reconnect(note: string) {
    setRestarting(true);
    await new Promise((r) => setTimeout(r, 2500));
    refresh();
    setRestarting(false);
    setOk(note);
  }

  async function restartDaemon() {
    setOk(null);
    setErr(null);
    // v1.433.0 (A8): inside the desktop app the SHELL restarts it — it knows
    // it asked, so the stop is never counted as a crash, and Build › Code's
    // coding tools keep running. A plain POST /shutdown is a QUIT.
    const tidy = await requestTidyRestart();
    if (tidy === "restarting") {
      await reconnect(RESTART_WORDS.restartingNote);
      return;
    }
    if (tidy === "refused") {
      setErr(RESTART_WORDS.refused);
      return;
    }
    // A browser tab (or an older desktop app): today's stop, in honest words.
    try {
      await post("/shutdown");
    } catch {
      /* the connection may reset as the daemon stops — expected */
    }
    await reconnect(desktop ? RESTART_WORDS.restartingNote : RESTART_WORDS.stoppingNote);
  }

  return (
    <Card title="Maintenance" icon={<Wrench size={15} />}>
      <div className="space-y-4">
        <div>
          <SectionLabel>Back up now</SectionLabel>
          <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
            Save a snapshot of your database and settings right now. Backups also run automatically in the
            background.
          </p>
          <button
            type="button"
            onClick={() => void backupNow()}
            disabled={backupBusy || restarting}
            className="btn-accent btn-sm mt-2.5 w-full justify-center"
          >
            {backupBusy ? (
              <LoaderInline label="Backing up…" />
            ) : (
              <>
                <DatabaseBackup size={14} /> Back up now
              </>
            )}
          </button>
        </div>
        <MaintenanceTools
          disabled={backupBusy || restarting}
          onRestartRequested={(note: string) => void reconnect(note)}
          refreshKey={backupsVersion}
        />
        <div className="border-t hairline pt-4">
          <SectionLabel>{RESTART_WORDS.label}</SectionLabel>
          <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
            {desktop ? RESTART_WORDS.blurb : RESTART_WORDS.browserBlurb}
          </p>
          <div className="mt-2.5">
            {/* v1.356.0 integrate (G80): the restart keeps its deliberate amber
                outline - the calm default has no border for it to colour. */}
            <ConfirmButton
              variant="bordered"
              onConfirm={() => void restartDaemon()}
              label={RESTART_WORDS.label}
              confirmLabel={restarting ? "Restarting…" : "Confirm restart"}
              className="w-full justify-center border-amber-500/30 py-1.5 text-amber-200 hover:border-amber-500/50 hover:text-amber-100"
              title={desktop ? RESTART_WORDS.title : RESTART_WORDS.browserTitle}
            />
          </div>
        </div>
        {ok && <SuccessNote>{ok}</SuccessNote>}
        {err && <ErrorNote>{err}</ErrorNote>}
      </div>
    </Card>
  );
}
