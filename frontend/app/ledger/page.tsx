import { AdminShell } from "@/components/AdminShell";
import { LedgerBrowser } from "@/components/LedgerBrowser";

export default function LedgerPage() {
  return (
    <AdminShell title="Ledger" description="Financial transactions, unsettled balances, and settlements.">
      <LedgerBrowser />
    </AdminShell>
  );
}
