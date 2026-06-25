import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "Iron Jarvis — Control Center",
  description: "Dashboard for the Iron Jarvis daemon.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="relative flex h-screen overflow-hidden">
          {/* Ambient arc-reactor glow behind everything. */}
          <div className="app-aura pointer-events-none absolute inset-0 -z-10" />
          <Sidebar />
          <main className="flex-1 overflow-y-auto">
            <div className="mx-auto max-w-7xl px-6 py-8 lg:px-10">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
