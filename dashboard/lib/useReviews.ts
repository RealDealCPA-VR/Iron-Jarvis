"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get } from "./api";
import type { Review, SessionView } from "./types";

export interface ReviewsState {
  /** session_id -> Review for every session that currently has one. */
  reviews: Record<string, Review>;
  loading: boolean;
  reload: () => void;
}

/**
 * Pending reviews come from ONE call — `GET /reviews` — instead of probing
 * `/sessions/{id}/review` per session (which fanned out N requests and, worse,
 * was keyed only on the candidate ID SET: a session flipping active→completed
 * kept the same ids, so a freshly-created review was never fetched and its card
 * skipped the In-Review lane). Refetches are keyed on `id:status` pairs so any
 * status flip re-checks, plus the caller's manual reload.
 */
export function useReviews(sessions: SessionView[] | undefined): ReviewsState {
  const [reviews, setReviews] = useState<Record<string, Review>>({});
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const firstLoad = useRef(true);

  // Re-fetch whenever any session's STATUS changes (not just the id set) — a
  // review is created at the completed transition, exactly when ids don't change.
  const key = useMemo(
    () =>
      (sessions ?? [])
        .map((s) => `${s.id}:${s.status}`)
        .sort()
        .join(","),
    [sessions],
  );

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    if (firstLoad.current) setLoading(true);

    get<{ reviews: (Review & { session_id: string })[] }>("/reviews")
      .then((d) => {
        if (cancelled) return;
        const next: Record<string, Review> = {};
        for (const r of d.reviews ?? []) next[r.session_id] = r;
        setReviews(next);
      })
      .catch(() => {
        /* offline / older daemon — leave the current map as-is */
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
        firstLoad.current = false;
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  return { reviews, loading, reload };
}
