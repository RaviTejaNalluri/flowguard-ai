"use client";

// The full recovery story as a timeline (rendered by app/incidents/[id]/page.tsx).
// 1 Failure -> 2 Diagnosis -> 3 Repair attempts (sandbox tested) -> 4 Proposed change -> 5 Human decision

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, ErrorBanner, Spinner, StatusBadge, StepTrail, timeAgo } from "@/components/ui";
import { api, type Incident, type Workflow } from "@/lib/api";

export default function IncidentView() {
  const { id } = useParams<{ id: string }>();
  const incidentId = Number(id);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [original, setOriginal] = useState<Workflow | null>(null);
  const [busy, setBusy] = useState<"repair" | "approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const repairStarted = useRef(false);

  const load = useCallback(async () => {
    try {
      const inc = await api.incident(incidentId);
      setIncident(inc);
      if (inc.run) {
        const versions = await api.versions(inc.workflow_id);
        setOriginal(versions.find((v) => v.version === inc.run!.workflow_version)?.definition ?? null);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [incidentId]);

  const act = useCallback(async (kind: "repair" | "approve" | "reject") => {
    setBusy(kind);
    setError(null);
    try {
      const fn = { repair: api.repair, approve: api.approve, reject: api.reject }[kind];
      await fn(incidentId);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [incidentId, load]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load]);

  // A freshly diagnosed incident goes straight into repair, so the demo flows without extra clicks.
  useEffect(() => {
    if (incident?.status === "diagnosed" && !repairStarted.current) {
      repairStarted.current = true;
      act("repair");
    }
  }, [incident?.status, act]);

  if (!incident) {
    return (
      <div className="space-y-4">
        <ErrorBanner message={error} />
        {!error && <p className="flex items-center gap-2 text-slate-400"><Spinner /> Loading incident…</p>}
      </div>
    );
  }

  const run = incident.run!;
  const d = incident.diagnosis;
  const stepIds = original?.steps.map((s) => s.id) ?? run.steps.map((s) => s.id);
  const failedStep = run.steps.find((s) => s.id === run.failed_step);
  const patched = incident.repair?.patched_workflow ?? null;

  return (
    <div className="space-y-6">
      <Link href="/" className="text-sm text-slate-400 hover:text-white">← Back to dashboard</Link>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">Incident #{incident.id}</h1>
          <p className="text-sm text-slate-400">
            Run #{run.id} of <span className="font-mono">{incident.workflow_id}</span> v{run.workflow_version} ·
            payment {run.trigger.payment_id} · {timeAgo(incident.created_at)}
          </p>
        </div>
        <StatusBadge status={incident.status} />
      </div>

      <ErrorBanner message={error} />

      <ol className="relative space-y-6 border-l border-slate-800 pl-8">
        {/* 1. Failure */}
        <Stage n={1} title="Workflow failed" tone="bad">
          <StepTrail stepIds={stepIds} records={run.steps} />
          <p className="mt-3 font-mono text-sm text-rose-300">{run.error}</p>
          {failedStep && (
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <JsonBox label={`Sent by ${failedStep.id}`} value={failedStep.request.body} />
              <JsonBox label={`Response (HTTP ${failedStep.response.status})`} value={failedStep.response.body} />
            </div>
          )}
        </Stage>

        {/* 2. Diagnosis */}
        <Stage n={2} title="Diagnosis by Muse Spark" tone="ai">
          {d ? (
            <div className="space-y-3">
              <p className="text-lg text-white">{d.summary}</p>
              <p className="text-sm text-slate-300"><span className="text-slate-500">Root cause:</span> {d.root_cause}</p>
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <span className="rounded bg-slate-800 px-2 py-1 font-mono text-slate-300">{d.category}</span>
                <Confidence value={d.confidence} />
              </div>
              <div>
                <p className="mb-1 text-xs font-medium uppercase text-slate-500">Evidence</p>
                <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
                  {d.evidence.map((e, i) => <li key={i}>{e}</li>)}
                </ul>
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">No diagnosis.</p>
          )}
        </Stage>

        {/* 3. Repair attempts */}
        {incident.status === "needs_human" ? (
          <Stage n={3} title="Escalated to a human" tone="warn">
            <p className="text-sm text-amber-200">
              FlowGuard determined this cannot be fixed safely by editing the workflow, so it did <b>not</b> attempt a
              repair. A person needs to act (for example, reconnect the Slack integration), then re-run the payment.
            </p>
          </Stage>
        ) : (
          <Stage n={3} title="Repair, tested in a sandbox" tone="ai">
            {busy === "repair" && (
              <p className="mb-3 flex items-center gap-2 text-sm text-indigo-300">
                <Spinner /> Muse Spark is proposing a fix and replaying the failed run in a sandbox…
              </p>
            )}
            {incident.repair?.attempts.map((a) => (
              <div key={a.number} className="mb-3 rounded-lg border border-slate-800 bg-slate-950/60 p-4">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-200">Attempt {a.number}</span>
                  <StatusBadge status={a.error ? "failed" : "success"} />
                </div>
                <p className="text-sm text-slate-300">{a.patch.explanation}</p>
                <ul className="mt-2 space-y-1">
                  {a.changes.map((c) => <li key={c} className="font-mono text-xs text-indigo-300">+ {c}</li>)}
                </ul>
                {a.sandbox && (
                  <div className="mt-3">
                    <p className="mb-1 text-xs text-slate-500">Sandbox replay of {run.trigger.payment_id}:</p>
                    <StepTrail stepIds={stepIds} records={a.sandbox.steps} />
                  </div>
                )}
                {a.error && <p className="mt-2 font-mono text-xs text-rose-300">{a.error} → feeding this back to the model</p>}
              </div>
            ))}
            {incident.status === "repair_failed" && (
              <div className="flex items-center gap-3">
                <p className="text-sm text-rose-300">No patch passed the sandbox. Nothing was changed.</p>
                <Button variant="secondary" disabled={busy !== null} onClick={() => act("repair")}>Try again</Button>
              </div>
            )}
          </Stage>
        )}

        {/* 4. Proposed change */}
        {original && patched && (
          <Stage n={4} title="Proposed change" tone="ai">
            <WorkflowDiff before={original} after={patched} />
          </Stage>
        )}

        {/* 5. Human decision */}
        {(incident.status === "awaiting_approval" || incident.resolution_run || incident.status === "rejected") && (
          <Stage n={5} title="Human approval" tone={incident.status === "resolved" ? "good" : "ai"}>
            {incident.status === "awaiting_approval" && (
              <div className="space-y-3">
                <p className="text-sm text-slate-300">
                  The fix passed the sandbox test. Nothing changes in production until you approve it.
                </p>
                <div className="flex gap-3">
                  <Button variant="success" disabled={busy !== null} onClick={() => act("approve")}>
                    {busy === "approve" ? <Spinner /> : "✓"} Approve & apply fix
                  </Button>
                  <Button variant="secondary" disabled={busy !== null} onClick={() => act("reject")}>Reject</Button>
                </div>
              </div>
            )}
            {incident.status === "rejected" && <p className="text-sm text-slate-400">Rejected. The workflow was left unchanged.</p>}
            {incident.resolution_run && (
              <div className="space-y-2">
                <p className={`text-sm ${incident.resolution_run.status === "success" ? "text-emerald-300" : "text-rose-300"}`}>
                  {incident.resolution_run.status === "success"
                    ? `✅ Fix applied as workflow v${incident.resolution_run.workflow_version}. Payment ${run.trigger.payment_id} was replayed for real and the customer is recovered.`
                    : `Fix applied, but the replay still failed: ${incident.resolution_run.error}`}
                </p>
                <StepTrail stepIds={stepIds} records={incident.resolution_run.steps} />
              </div>
            )}
          </Stage>
        )}
      </ol>
    </div>
  );
}

function Stage({ n, title, tone, children }: { n: number; title: string; tone: "bad" | "ai" | "good" | "warn"; children: React.ReactNode }) {
  const dot = { bad: "bg-rose-500", ai: "bg-indigo-500", good: "bg-emerald-500", warn: "bg-amber-500" }[tone];
  return (
    <li className="relative">
      <span className={`absolute -left-[45px] flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold text-white ring-4 ring-slate-950 ${dot}`}>
        {n}
      </span>
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
        {children}
      </div>
    </li>
  );
}

function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className="flex items-center gap-2 text-slate-400">
      confidence
      <span className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-800">
        <span className="block h-full bg-indigo-400" style={{ width: `${pct}%` }} />
      </span>
      {pct}%
    </span>
  );
}

function JsonBox({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <p className="mb-1 text-xs text-slate-500">{label}</p>
      <pre className="overflow-x-auto rounded-lg bg-slate-950 p-3 font-mono text-xs text-slate-300">{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

/** Before/after view of every step the patch changed. Changed lines are highlighted. */
function WorkflowDiff({ before, after }: { before: Workflow; after: Workflow }) {
  const changed = after.steps.filter((s) => {
    const old = before.steps.find((b) => b.id === s.id);
    return JSON.stringify(old) !== JSON.stringify(s);
  });
  return (
    <div className="space-y-4">
      {changed.map((step) => {
        const oldLines = JSON.stringify(before.steps.find((b) => b.id === step.id), null, 2).split("\n");
        const newLines = JSON.stringify(step, null, 2).split("\n");
        return (
          <div key={step.id}>
            <p className="mb-1 font-mono text-xs text-slate-400">step: {step.id}</p>
            <div className="grid gap-3 md:grid-cols-2">
              <DiffColumn label="Before" lines={oldLines} other={newLines} tone="removed" />
              <DiffColumn label="After" lines={newLines} other={oldLines} tone="added" />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DiffColumn({ label, lines, other, tone }: { label: string; lines: string[]; other: string[]; tone: "added" | "removed" }) {
  const otherSet = new Set(other.map((l) => l.trim().replace(/,$/, "")));
  const highlight = tone === "added" ? "bg-emerald-500/15 text-emerald-200" : "bg-rose-500/15 text-rose-200";
  return (
    <div>
      <p className="mb-1 text-xs text-slate-500">{label}</p>
      <pre className="overflow-x-auto rounded-lg bg-slate-950 p-3 font-mono text-xs text-slate-400">
        {lines.map((line, i) => (
          <div key={i} className={otherSet.has(line.trim().replace(/,$/, "")) ? "" : highlight}>
            {tone === "added" && !otherSet.has(line.trim().replace(/,$/, "")) ? "+ " : tone === "removed" && !otherSet.has(line.trim().replace(/,$/, "")) ? "- " : "  "}
            {line}
          </div>
        ))}
      </pre>
    </div>
  );
}
