"use client";

/**
 * Settings › System › Maintenance (calm UI redesign S10): back up now, the
 * backup/restore tools and a daemon restart — the same calls the old Settings
 * page's Maintenance card made, lifted out so the System group can show them.
 */

import { useState } from "react";
import { DatabaseBackup, Wrench } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import { Card, ConfirmButton, ErrorNote, LoaderInline, SectionLabel, SuccessNote } from "@/components/ui";
import { MaintenanceTools } from "@/components/settings/MaintenanceTools";

export function MaintenanceCard() {
  const { refresh } = useDaemon();
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
      } else setErr("The daemon reported the backup did not complete.");
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
    try {
      await post("/shutdown");
    } catch {
      /* the connection may reset as the daemon stops — expected */
    }
    await reconnect("Restart requested — reconnecting. Watch the status dot in the sidebar.");
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
          <SectionLabel>Restart daemon</SectionLabel>
          <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
            Applies “restart” settings and clears a stuck state. It briefly interrupts Iron Jarvis; the desktop app
            brings it right back.
          </p>
          <div className="mt-2.5">
            <ConfirmButton
              onConfirm={() => void restartDaemon()}
              label="Restart daemon"
              confirmLabel={restarting ? "Restarting…" : "Confirm restart"}
              className="w-full justify-center border-amber-500/30 py-1.5 text-amber-200 hover:border-amber-500/50 hover:text-amber-100"
              title="Gracefully stops the daemon; the desktop app restarts it within ~2s."
            />
          </div>
        </div>
        {ok && <SuccessNote>{ok}</SuccessNote>}
        {err && <ErrorNote>{err}</ErrorNote>}
      </div>
    </Card>
  );
}
