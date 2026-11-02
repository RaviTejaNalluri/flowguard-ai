"use client";

// Main dashboard: the workflow, chaos switches, run history, and a "Heal" button on failed runs.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button, Card, ErrorBanner, Spinner, StatusBadge, StepTrail, timeAgo } from "@/components/ui";
import { api, type ChaosSwitches, type Incident, type Run, type ServicesData, type Workflow } from "@/lib/api";

const WORKFLOW_ID = "customer_onboarding";
const PAYMENTS = [
  { id: "pay_001", label: "pay_001 · Ada Lovelace · $49" },
  { id: "pay_002", label: "pay_002 · Alan Turing · $129" },
  { id: "pay_003", label: "pay_003 · Grace Hopper · €25" },
];
const CHAOS_LABELS: Record<string, string> = {
  schema_drift: "CRM API renamed a field",
  missing_data: "Stripe sends empty names",
  rate_limit: "Slack rate limiting",
  auth_expired: "Slack token revoked",
};

export default function Dashboard() {
  const router = useRouter();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [chaos, setChaos] = useState<ChaosSwitches>({});
  const [services, setServices] = useState<ServicesData>({ crm_contacts: [], slack_messages: [] });
  const [paymentId, setPaymentId] = useState(PAYMENTS[0].id);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [workflows, runs, incidents, chaos, services] = await Promise.all([
        api.workflows(), api.runs(), api.incidents(), api.chaos(), api.servicesData(),
      ]);
      setWorkflow(workflows.find((w) => w.id === WORKFLOW_ID) ?? null);
      setRuns(runs);
      setIncidents(incidents);
      setChaos(chaos);
      setServices(services);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // Load now, then poll every 5 seconds.
  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(refresh, 5000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh]);

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function heal(run: Run) {
    setBusy(`heal-${run.id}`);
    try {
      const incident = await api.diagnose(run.id);
      router.push(`/incidents/${incident.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  const incidentFor = (runId: number) => incidents.find((i) => i.run_id === runId);
  const stepIds = workflow?.steps.map((s) => s.id) ?? [];
  const failed = runs.filter((r) => r.status === "failed").length;

  return (
    <div className="space-y-6">
      <ErrorBanner message={error} />

      {/* Top stats */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Runs" value={runs.length} />
        <Stat label="Failed runs" value={failed} tone={failed ? "bad" : undefined} />
        <Stat label="Incidents resolved" value={incidents.filter((i) => i.status === "resolved").length} tone="good" />
        <Stat label="Awaiting your approval" value={incidents.filter((i) => i.status === "awaiting_approval").length} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left column */}
        <div className="space-y-6">
          <Card title="Workflow" right={workflow && <span className="text-xs text-slate-400">v{workflow.version}</span>}>
            {workflow ? (
              <>
                <p className="font-medium text-white">{workflow.name}</p>
                <p className="mt-1 text-sm text-slate-400">{workflow.description}</p>
                <ol className="mt-4 space-y-2">
                  {workflow.steps.map((s, i) => (
                    <li key={s.id} className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-slate-200">{i + 1}. {s.id}</span>
                        <span className="text-xs uppercase text-slate-500">{s.service}</span>
                      </div>
                      <div className="mt-1 font-mono text-xs text-slate-500">{s.method} {s.path}</div>
                      {(s.retry || s.defaults) && (
                        <div className="mt-2 flex flex-wrap gap-1 text-xs">
                          {s.retry && <Tag>retry ×{s.retry.max_attempts}</Tag>}
                          {s.defaults && Object.entries(s.defaults).map(([k, v]) => <Tag key={k}>{k} fallback: {v}</Tag>)}
                        </div>
                      )}
                    </li>
                  ))}
                </ol>
              </>
            ) : (
              <p className="text-sm text-slate-500">Loading…</p>
            )}
          </Card>

          <Card
            title="Chaos panel"
            right={
              <button onClick={() => act("reset", api.resetChaos)} className="text-xs text-slate-400 hover:text-white">
                Reset all
              </button>
            }
          >
            <p className="mb-3 text-xs text-slate-500">Break the integrations on purpose to simulate real-world failures.</p>
            <div className="space-y-2">
              {Object.entries(chaos).map(([name, { enabled, description }]) => (
                <label
                  key={name}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 transition ${
                    enabled ? "border-rose-500/50 bg-rose-500/10" : "border-slate-800 hover:border-slate-700"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={enabled}
                    disabled={busy !== null}
                    onChange={(e) => act(`chaos-${name}`, () => api.setChaos(name, e.target.checked))}
                    className="mt-1 accent-rose-500"
                  />
                  <span>
                    <span className="block text-sm font-medium text-slate-200">{CHAOS_LABELS[name] ?? name}</span>
                    <span className="block text-xs text-slate-500">{description}</span>
                  </span>
                </label>
              ))}
            </div>
          </Card>

          <Card title="What the apps received">
            <p className="mb-2 text-xs font-medium uppercase text-slate-500">CRM contacts</p>
            <ul className="mb-4 space-y-1 text-sm">
              {services.crm_contacts.length === 0 && <li className="text-slate-600">None yet</li>}
              {services.crm_contacts.map((c) => (
                <li key={c.id} className="font-mono text-xs text-slate-300">{c.id} · {c.name} · {c.email}</li>
              ))}
            </ul>
            <p className="mb-2 text-xs font-medium uppercase text-slate-500">Slack #sales</p>
            <ul className="space-y-1 text-sm">
              {services.slack_messages.length === 0 && <li className="text-slate-600">None yet</li>}
              {services.slack_messages.map((m) => (
                <li key={m.ts} className="text-xs text-slate-300">💬 {m.text}</li>
              ))}
            </ul>
          </Card>
        </div>

        {/* Right column */}
        <div className="space-y-6 lg:col-span-2">
          <Card title="Trigger a run">
            <div className="flex flex-wrap items-center gap-3">
              <select
                value={paymentId}
                onChange={(e) => setPaymentId(e.target.value)}
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
              >
                {PAYMENTS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
              <Button disabled={busy !== null} onClick={() => act("run", () => api.runWorkflow(WORKFLOW_ID, paymentId))}>
                {busy === "run" ? <Spinner /> : "▶"} Run workflow
              </Button>
              <span className="text-xs text-slate-500">Simulates a new Stripe payment arriving.</span>
              <span className="ml-auto">
                <Button
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() => {
                    if (confirm("Reset the demo? This deletes all runs and incidents and restores workflow v1.")) {
                      act("demo-reset", api.resetDemo);
                    }
                  }}
                >
                  ↺ Reset demo
                </Button>
              </span>
            </div>
          </Card>

          <Card title="Run history">
            {runs.length === 0 ? (
              <p className="text-sm text-slate-500">No runs yet. Trigger one above.</p>
            ) : (
              <ul className="divide-y divide-slate-800">
                {runs.map((run) => {
                  const incident = incidentFor(run.id);
                  return (
                    <li key={run.id} className="flex flex-col gap-3 py-4 md:flex-row md:items-center md:justify-between">
                      <div className="min-w-0 space-y-2">
                        <div className="flex items-center gap-2 text-sm">
                          <span className="font-mono text-slate-400">#{run.id}</span>
                          <StatusBadge status={run.status} />
                          <span className="text-slate-500">
                            {run.trigger.payment_id} · v{run.workflow_version} · {timeAgo(run.started_at)}
                          </span>
                        </div>
                        <StepTrail stepIds={stepIds} records={run.steps} />
                        {run.error && <p className="font-mono text-xs text-rose-300">{run.error}</p>}
                      </div>
                      <div className="shrink-0">
                        {incident ? (
                          <Link href={`/incidents/${incident.id}`} className="flex items-center gap-2 text-sm text-indigo-300 hover:text-indigo-200">
                            Incident #{incident.id} <StatusBadge status={incident.status} /> →
                          </Link>
                        ) : run.status === "failed" ? (
                          <Button disabled={busy !== null} onClick={() => heal(run)}>
                            {busy === `heal-${run.id}` ? <><Spinner /> Diagnosing…</> : "🛡️ Heal with FlowGuard"}
                          </Button>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "good" | "bad" }) {
  const color = tone === "good" ? "text-emerald-300" : tone === "bad" ? "text-rose-300" : "text-white";
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4">
      <div className={`text-2xl font-semibold ${color}`}>{value}</div>
      <div className="text-xs text-slate-400">{label}</div>
    </div>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-indigo-300 ring-1 ring-inset ring-indigo-500/30">{children}</span>;
}
