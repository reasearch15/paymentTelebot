"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";

type DashboardSummary = {
  total_in_cents: number;
  total_out_cents: number;
  total_settled_cents: number;
  current_unsettled_balance_cents: number;
  total_transactions: number;
  connected_accounts: number;
  active_accounts: number;
  accounts_with_errors: number;
  worker_alive: boolean;
  worker_heartbeat: string | null;
  last_heartbeat_at: string | null;
  listener_health: string;
  latest_captured_email_at: string | null;
};

function formatMoney(cents: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(cents / 100);
}

function formatDate(value: string | null) {
  if (!value) {
    return "Never";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function titleCaseHealth(value: string) {
  if (!value) {
    return "Unknown";
  }
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function listenerHealthNote(summary: DashboardSummary) {
  if (summary.listener_health === "healthy") {
    return `Worker ${summary.worker_heartbeat ?? "alive"} · last heartbeat ${formatDate(summary.last_heartbeat_at)}`;
  }
  if (summary.listener_health === "degraded") {
    return `${summary.accounts_with_errors} account error(s) · last heartbeat ${formatDate(summary.last_heartbeat_at)}`;
  }
  if (summary.listener_health === "offline") {
    return `Worker offline · last heartbeat ${formatDate(summary.last_heartbeat_at)}`;
  }
  if (summary.listener_health === "idle") {
    return "No enabled accounts eligible for listening";
  }
  return `Last heartbeat ${formatDate(summary.last_heartbeat_at)}`;
}

export function DashboardOverview() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiRequest<DashboardSummary>("/dashboard/summary")
      .then((payload) => {
        if (!cancelled) {
          setSummary(payload);
          setError(null);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setSummary(null);
          setError(caught instanceof Error ? caught.message : "Unable to load dashboard summary");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <div className="alert-message">{error}</div>;
  }

  if (!summary) {
    return <div className="loading-row">Loading dashboard summary...</div>;
  }

  const primaryMetrics = [
    {
      label: "Total In",
      value: formatMoney(summary.total_in_cents),
      note: "Lifetime incoming from ledger",
    },
    {
      label: "Total Out",
      value: formatMoney(summary.total_out_cents),
      note: "Lifetime outgoing from ledger",
    },
    {
      label: "Current Balance",
      value: formatMoney(summary.current_unsettled_balance_cents),
      note: "Incoming − outgoing − settlements",
    },
    {
      label: "Active Gmail Accounts",
      value: summary.active_accounts.toString(),
      note: `${summary.connected_accounts} configured · enabled for listening`,
    },
    {
      label: "Listener Health",
      value: titleCaseHealth(summary.listener_health),
      note: listenerHealthNote(summary),
    },
  ];

  const secondaryMetrics = [
    {
      label: "Worker",
      value: summary.worker_heartbeat ?? "offline",
      note: formatDate(summary.last_heartbeat_at),
    },
    {
      label: "Connected Gmail Accounts",
      value: summary.connected_accounts.toString(),
      note: "Configured payment accounts",
    },
    {
      label: "Gmail Accounts With Errors",
      value: summary.accounts_with_errors.toString(),
      note: "Connection or capture errors",
    },
    {
      label: "Latest Captured Email",
      value: formatDate(summary.latest_captured_email_at),
      note: `${summary.total_transactions} ledger transactions`,
    },
  ];

  return (
    <>
      <section className="metric-grid" aria-label="Dashboard metrics">
        {primaryMetrics.map((metric) => (
          <article className="metric-card" key={metric.label}>
            <p className="metric-label">{metric.label}</p>
            <p className="metric-value">{metric.value}</p>
            <p className="metric-note">{metric.note}</p>
          </article>
        ))}
      </section>
      <section className="metric-grid dashboard-health" aria-label="Listener health">
        {secondaryMetrics.map((metric) => (
          <article className="metric-card" key={metric.label}>
            <p className="metric-label">{metric.label}</p>
            <p className="metric-value">{metric.value}</p>
            <p className="metric-note">{metric.note}</p>
          </article>
        ))}
      </section>
    </>
  );
}
