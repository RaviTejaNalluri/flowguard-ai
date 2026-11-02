// Incident page. The view reads the incident id from the URL, which is only known
// when the page is requested, so it renders inside <Suspense> with a loading fallback.

import { Suspense } from "react";

import IncidentView from "./incident-view";

export default function IncidentPage() {
  return (
    <Suspense fallback={<p className="text-slate-400">Loading incident…</p>}>
      <IncidentView />
    </Suspense>
  );
}
