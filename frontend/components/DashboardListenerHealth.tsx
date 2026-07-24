"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";

type ListenerStatus = {
  worker_heartbeat: string | null;
  last_heartbeat_at: string | null;
  enabled_account_count: number;
  connected_account_count: number;
  error_account_count: number;
  latest_captured_email_time: string | null;
};

function formatDate(value: string | null) {
  if (!value) {
    return "Never";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function DashboardListenerHealth() {
  const [status, setStatus] = useState<ListenerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiRequest<ListenerStatus>("/listener/status")
      .then(setStatus)
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Unable to load listener status"));
  }, []);

  if (error) {
    return <div className="alert-message dashboard-health">{error}</div>;
  }

  if (!status) {
    return <div className="loading-row dashboard-health">Loading listener health...</div>;
  }

  const metrics = [
    { label: "Worker", value: status.worker_heartbeat ?? "offline", note: formatDate(status.last_heartbeat_at) },
    { label: "Connected Gmail Accounts", value: status.connected_account_count.toString(), note: `${status.enabled_account_count} enabled` },
    { label: "Gmail Accounts With Errors", value: status.error_account_count.toString(), note: "Connection or capture errors" },
    { label: "Latest Captured Email", value: formatDate(status.latest_captured_email_time), note: "Raw capture only" },
  ];

  return (
    <section className="metric-grid dashboard-health" aria-label="Listener health">
      {metrics.map((metric) => (
        <article className="metric-card" key={metric.label}>
          <p className="metric-label">{metric.label}</p>
          <p className="metric-value">{metric.value}</p>
          <p className="metric-note">{metric.note}</p>
        </article>
      ))}
    </section>
  );
}
