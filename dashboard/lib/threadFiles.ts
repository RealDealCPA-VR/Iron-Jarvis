"use client";

// The open conversation's files — the Files rail's own list — shared with
// components the chat page does not render directly (C-06).
//
// WHY A TINY STORE: the email draft card is rendered by the markdown renderer
// deep inside a message, and it needs to offer "this conversation's files" as
// attachments. The one component that already receives exactly that list is
// the Files rail (ArtifactsRail), so it PUBLISHES its items here while it is
// mounted and clears them when it goes away; the card READS them. One source
// of truth — the rail's list — and no second copy threaded through the page.

import { useSyncExternalStore } from "react";

let files: readonly string[] = [];
const listeners = new Set<() => void>();

/** Replace the current conversation's file list (the rail calls this). */
export function setThreadFiles(paths: readonly string[]): void {
  const next = Array.from(new Set(paths.filter((p) => typeof p === "string" && p)));
  if (next.length === files.length && next.every((p, i) => p === files[i])) return;
  files = next;
  for (const listener of listeners) listener();
}

export function getThreadFiles(): readonly string[] {
  return files;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The current conversation's files, re-rendering when the rail changes. */
export function useThreadFiles(): readonly string[] {
  return useSyncExternalStore(subscribe, getThreadFiles, getThreadFiles);
}
