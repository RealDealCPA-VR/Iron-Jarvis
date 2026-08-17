"use client";

// ONE PLACE THAT KNOWS WHAT A FACE LOOKS LIKE (v1.180.0).
//
// THE DEFECT THIS FIXES, found by review: face overrides were persisted, served
// on GET /agents and GET /agents/roster, and rendered by exactly ONE component —
// the picker that sets them. `AgentFace` appears in seven files (SetupCard,
// RoundTable, RosterStrip, PanelPicker, schedules, TeamTree, SessionCard), so a
// user could carefully choose a shape and a colour and then see their choice
// NOWHERE except the control they chose it in. A customization nobody can see
// is not a feature.
//
// Threading a `face` prop through all seven was the obvious fix and the wrong
// one: four of those sites render an agent they know only by NAME (a kanban
// card, a session's team tree, a schedule row) and have no roster entry to read
// an override from. They would each have had to grow a fetch, and the app would
// answer "what does this agent look like" in seven places.
//
// So the map is fetched ONCE and read by the component itself. An explicit
// `face` prop still wins — the picker's live preview must show the draft being
// edited, not the saved value — and with no provider mounted, or an older
// daemon that 404s, every face derives from its name exactly as it did before.

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { FaceOverride } from "@/components/agents/AgentFace";

type FaceMap = Record<string, FaceOverride>;

const FacesContext = createContext<FaceMap | null>(null);

/**
 * The stored override for `name`, or null.
 *
 * Lookup is case-insensitive because the STORE is: face records key on the same
 * slug as portraits (`_avatar_slug`), which case-folds so "Analyst" and
 * "analyst" can never resolve to two files on a case-insensitive filesystem.
 * A map keyed by the agent's original casing would therefore miss its own
 * record whenever the two differ.
 */
export function useFaceStyle(name: string): FaceOverride | null {
  const faces = useContext(FacesContext);
  if (!faces || !name) return null;
  const direct = faces[name];
  if (direct) return direct;
  const wanted = name.toLowerCase();
  for (const key of Object.keys(faces)) {
    if (key.toLowerCase() === wanted) return faces[key];
  }
  return null;
}

export function FaceStylesProvider({ children }: { children: ReactNode }) {
  const [faces, setFaces] = useState<FaceMap>({});

  useEffect(() => {
    let live = true;
    // Best-effort and silent: this decorates faces, it never gates them. An
    // older daemon has no such route, and a failure here must leave every face
    // deriving from its name rather than surface an error about cosmetics.
    void (async () => {
      try {
        const res = await api<{ faces?: FaceMap }>("/agents/faces");
        if (live && res?.faces && typeof res.faces === "object") {
          setFaces(res.faces);
        }
      } catch {
        /* no overrides — derived faces, as before */
      }
    })();
    // Refetch when the picker saves, so a chosen face reaches the rail and the
    // transcript without a reload. The picker dispatches this after a
    // successful PUT/DELETE (same idiom as "ij:workflow-changed").
    const onChanged = () => {
      void (async () => {
        try {
          const res = await api<{ faces?: FaceMap }>("/agents/faces");
          if (live && res?.faces) setFaces(res.faces);
        } catch {
          /* keep what we have — a failed refresh must not blank real overrides */
        }
      })();
    };
    window.addEventListener("ij:agent-face-changed", onChanged);
    return () => {
      live = false;
      window.removeEventListener("ij:agent-face-changed", onChanged);
    };
  }, []);

  const value = useMemo(() => faces, [faces]);
  return <FacesContext.Provider value={value}>{children}</FacesContext.Provider>;
}
