"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, get } from "./api";
import type { Review, SessionView } from "./types";

export interface ReviewsState {
  /** session_id -> Review for every session that currently has one. */
  reviews: Record<string, Review>;
  loading: boolean;
  reload: () => void;
}

/**
 * There is no "list reviews" endpoint, so we probe `/sessions/{id}/review`
 * for every plausibly-reviewable session (active or completed) and treat a
 * 200 as "has review". 404s are expected and ignored. Probing is keyed on the
 * candidate id set so it only re-runs when that set actually changes.
 */
export function useReviews(sessions: SessionView[] | undefined): ReviewsState {
  const [reviews, setReviews] = useState<Record<string, Review>>({});
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const firstLoad = useRef(true);

  const candidateIds = useMemo(() => {
    return (sessions ?? [])
      .filter((s) => {
        const st = s.status.toLowerCase();
        return st === "active" || st === "completed" || st === "running";
      })
      .map((s) => s.id);
  }, [sessions]);

  // Stable dependency key so identical id-sets don't re-trigger probing.
  const key = useMemo(() => [...candidateIds].sort().join(","), [candidateIds]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (candidateIds.length === 0) {
      setReviews({});
      setLoading(false);
      return;
    }
    let cancelled = false;
    if (firstLoad.current) setLoading(true);

    Promise.allSettled(
      candidateIds.map((id) =>
        get<Review>(`/sessions/${id}/review`).then((r) => ({ id, r })),
      ),
    ).then((settled) => {
      if (cancelled) return;
      const next: Record<string, Review> = {};
      for (const res of settled) {
        if (res.status === "fulfilled") {
          next[res.value.id] = { ...res.value.r, session_id: res.value.id };
        }
        // Rejected => 404 (no review) or offline; simply omit.
        else if (
          res.reason instanceof ApiError &&
          res.reason.status !== 404 &&
          res.reason.status !== 0
        ) {
          // unexpected — ignore but don't crash
        }
      }
      setReviews(next);
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
