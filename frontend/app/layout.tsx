import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "FlowGuard AI",
  description: "Self-healing business automations, powered by Meta Muse Spark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <header className="border-b border-slate-800 bg-slate-950/80">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
            <Link href="/" className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500 text-lg">🛡️</span>
              <span>
                <span className="block text-lg font-semibold text-white">FlowGuard AI</span>
                <span className="block text-xs text-slate-400">Self-healing business automations</span>
              </span>
            </Link>
            <span className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300">
              Powered by Meta <span className="font-semibold text-white">Muse Spark</span>
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
