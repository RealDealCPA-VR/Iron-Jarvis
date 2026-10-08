"use client";

import { createContext, useContext, type ReactNode } from "react";

/**
 * A whole page shown INSIDE another (calm UI redesign S10): Connections,
 * Notifications, Tools… are sections of Settings now. Inside this provider a
 * page's PageHeader renders its title as a section heading (h2), so the host
 * keeps ONE h1 and the page itself is unchanged.
 */
const EmbeddedPageContext = createContext(false);

export function EmbeddedPage({ children }: { children: ReactNode }) {
  return <EmbeddedPageContext.Provider value={true}>{children}</EmbeddedPageContext.Provider>;
}

export function useEmbeddedPage(): boolean {
  return useContext(EmbeddedPageContext);
}
