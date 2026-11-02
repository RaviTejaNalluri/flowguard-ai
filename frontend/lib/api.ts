// Talks to the FlowGuard Python server (backend/main.py).

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

// ---------- Types (mirror what the server returns) ----------

export type WorkflowStep = {
  id: string;
  service?: string;
  method: string;
  path: string;
  body?: Record<string, unknown>;
  defaults?: Record<string, string>;
  retry?: { max_attempts: number };
};

export type Workflow = {
  id: string;
  name: string;
  description?: string;
  version: number;
  steps: WorkflowStep[];
};

export type WorkflowVersion = { version: number; note: string; created_at: string; definition: Workflow };

export type StepRecord = {
  id: string;
  service?: string;
  request: { method: string; path: string; body: Record<string, unknown> | null };
  response: { status: number; body: unknown };
  attempts: number;
  status: "success" | "failed";
};

export type Run = {
  id: number;
  workflow_id: string;
  workflow_version: number;
  status: "success" | "failed";
  failed_step: string | null;
  error: string | null;
  trigger: { payment_id: string };
  steps: StepRecord[];
  started_at: string;
  incident_id: number | null; // the incident this failed run belongs to
  replay_of: number | null; // set on replays: the failed run being retried
  recovered_by: number | null; // set on failed runs: the replay that succeeded
};

export type Diagnosis = {
  summary: string;
  root_cause: string;
  category: string;
  evidence: string[];
  fixable_by_workflow_change: boolean;
  confidence: number;
};

export type RepairAttempt = {
  number: number;
  patch: { explanation: string; operations: Record<string, unknown>[] };
  changes: string[];
  sandbox: { status: string; failed_step: string | null; error: string | null; steps: StepRecord[] } | null;
  error: string | null;
};

export type IncidentStatus =
  | "diagnosing"
  | "repairing"
  | "error"
  | "diagnosed"
  | "needs_human"
  | "awaiting_approval"
  | "repair_failed"
  | "resolved"
  | "approved_but_failing"
  | "rejected";

export type Incident = {
  id: number;
  run_id: number;
  workflow_id: string;
  status: IncidentStatus;
  diagnosis: Diagnosis | null;
  repair: { status: string; attempts: RepairAttempt[]; patched_workflow: Workflow | null; changes: string[] } | null;
  resolution_run_id: number | null;
  recovery: {
    affected: number;
    recovered: number;
    replays: { run_id: number; replay_run_id: number; payment_id: string; status: string }[];
  } | null;
  error: string | null;
  created_at: string;
  run?: Run;
  resolution_run?: Run | null;
  affected_runs?: Run[];
};

/** Statuses where FlowGuard is still working, so the UI should keep polling. */
export const IN_PROGRESS: IncidentStatus[] = ["diagnosing", "repairing"];

export type ChaosSwitches = Record<string, { enabled: boolean; description: string }>;

export type ServicesData = {
  crm_contacts: { id: string; name: string; email: string }[];
  slack_messages: { ts: string; channel: string; text: string }[];
};

// ---------- Calls ----------

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${method} ${path} failed with HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  workflows: () => request<Workflow[]>("/workflows"),
  versions: (id: string) => request<WorkflowVersion[]>(`/workflows/${id}/versions`),
  runWorkflow: (id: string, paymentId: string) =>
    request<Run>(`/workflows/${id}/run`, "POST", { payment_id: paymentId }),

  runs: (limit = 20) => request<Run[]>(`/runs?limit=${limit}`),
  heal: (runId: number) => request<Incident>(`/runs/${runId}/heal`, "POST"),

  incidents: () => request<Incident[]>("/incidents"),
  incident: (id: number) => request<Incident>(`/incidents/${id}`),
  repair: (id: number) => request<Incident>(`/incidents/${id}/repair`, "POST"),
  approve: (id: number) => request<Incident>(`/incidents/${id}/approve`, "POST"),
  reject: (id: number) => request<Incident>(`/incidents/${id}/reject`, "POST"),
  replay: (id: number) => request<Incident>(`/incidents/${id}/replay`, "POST"),

  settings: () => request<{ auto_heal: boolean }>("/settings"),
  setAutoHeal: (on: boolean) => request<{ auto_heal: boolean }>("/settings", "POST", { auto_heal: on }),

  chaos: () => request<ChaosSwitches>("/chaos"),
  setChaos: (name: string, enabled: boolean) => request<ChaosSwitches>(`/chaos/${name}`, "POST", { enabled }),
  resetChaos: () => request<ChaosSwitches>("/chaos/reset", "POST"),

  servicesData: () => request<ServicesData>("/services/data"),
  resetDemo: () => request<{ status: string }>("/demo/reset", "POST"),
};
