// Small shared building blocks used across pages.

import type { IncidentStatus, StepRecord } from "@/lib/api";

export function Card({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
      <div className="mb-4 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

const STATUS_STYLES: Record<string, string> = {
  success: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  resolved: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  failed: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  repair_failed: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  approved_but_failing: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  needs_human: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  awaiting_approval: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  diagnosed: "bg-violet-500/15 text-violet-300 ring-violet-500/30",
  rejected: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
};

const STATUS_LABELS: Record<string, string> = {
  awaiting_approval: "awaiting approval",
  needs_human: "needs a human",
  repair_failed: "repair failed",
  approved_but_failing: "approved, still failing",
};

export function StatusBadge({ status }: { status: IncidentStatus | "success" | "failed" | string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
        STATUS_STYLES[status] ?? STATUS_STYLES.rejected
      }`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  variant = "primary",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "danger" | "success";
}) {
  const styles = {
    primary: "bg-indigo-500 hover:bg-indigo-400 text-white",
    secondary: "bg-slate-800 hover:bg-slate-700 text-slate-200 ring-1 ring-inset ring-slate-700",
    danger: "bg-rose-600 hover:bg-rose-500 text-white",
    success: "bg-emerald-600 hover:bg-emerald-500 text-white",
  }[variant];
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  );
}

export function Spinner() {
  return <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent" />;
}

/** A row of step pills: fetch_payment ✓ → create_contact ✗ → notify_slack (skipped). */
export function StepTrail({ stepIds, records }: { stepIds: string[]; records: StepRecord[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {stepIds.map((id, i) => {
        const r = records.find((s) => s.id === id);
        const style = !r
          ? "border-slate-700 text-slate-500"
          : r.status === "success"
            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
            : "border-rose-500/40 bg-rose-500/10 text-rose-300";
        return (
          <span key={id} className="flex items-center gap-2">
            <span className={`rounded-md border px-2 py-1 font-mono ${style}`}>
              {r ? (r.status === "success" ? "✓ " : "✗ ") : ""}
              {id}
              {r && r.attempts > 1 ? ` (${r.attempts} tries)` : ""}
            </span>
            {i < stepIds.length - 1 && <span className="text-slate-600">→</span>}
          </span>
        );
      })}
    </div>
  );
}

export function timeAgo(iso: string) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
      {message}
      {message.includes("fetch") && " — is the backend running? (cd backend && .venv/bin/uvicorn main:app --reload)"}
    </div>
  );
}
