import { AdminShell } from "@/components/AdminShell";
import { DashboardListenerHealth } from "@/components/DashboardListenerHealth";

const metrics = [
  { label: "Total In", value: "$0.00", note: "Ledger calculations pending" },
  { label: "Total Out", value: "$0.00", note: "Ledger calculations pending" },
  { label: "Current Balance", value: "$0.00", note: "Settlement logic pending" },
  { label: "Active Gmail Accounts", value: "0", note: "No accounts connected" },
  { label: "Listener Health", value: "Idle", note: "Listener not implemented" },
];

export default function DashboardPage() {
  return (
    <AdminShell title="Dashboard" description="A first-pass view of payment activity and listener status.">
      <section className="metric-grid" aria-label="Dashboard metrics">
        {metrics.map((metric) => (
          <article className="metric-card" key={metric.label}>
            <p className="metric-label">{metric.label}</p>
            <p className="metric-value">{metric.value}</p>
            <p className="metric-note">{metric.note}</p>
          </article>
        ))}
      </section>
      <DashboardListenerHealth />
    </AdminShell>
  );
}
