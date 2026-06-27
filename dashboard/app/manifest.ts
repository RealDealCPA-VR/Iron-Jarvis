import type { MetadataRoute } from "next";

// PWA manifest (App Router metadata route). Served at /manifest.webmanifest.
// Colors mirror the dark crimson/cyan arc-reactor theme (ink-950 + accent).
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Iron Jarvis",
    short_name: "Jarvis",
    description: "Control center for the Iron Jarvis daemon.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#070809",
    theme_color: "#070809",
    icons: [
      {
        src: "/icon.svg",
        type: "image/svg+xml",
        sizes: "any",
        purpose: "any",
      },
    ],
  };
}
