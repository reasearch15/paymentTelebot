import { AdminShell } from "@/components/AdminShell";
import { LedgerBrowser } from "@/components/LedgerBrowser";

export default function LedgerPage() {
  return (
    <AdminShell title="Ledger" description="Parsed payment transactions from captured emails.">
      <LedgerBrowser />
    </AdminShell>
  );
}
