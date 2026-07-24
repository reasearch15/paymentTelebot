import { AdminShell } from "@/components/AdminShell";
import { SettlementsBrowser } from "@/components/SettlementsBrowser";

export default function SettlementsPage() {
  return (
    <AdminShell title="Settlements" description="Completed cash settlements against unsettled payment balances.">
      <SettlementsBrowser />
    </AdminShell>
  );
}
