import { AdminShell } from "@/components/AdminShell";
import { DashboardOverview } from "@/components/DashboardOverview";

export default function DashboardPage() {
  return (
    <AdminShell title="Dashboard" description="Payment activity and listener status from the live ledger.">
      <DashboardOverview />
    </AdminShell>
  );
}
