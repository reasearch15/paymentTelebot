import { Suspense } from "react";
import { AdminShell } from "@/components/AdminShell";
import { BotLedgerBrowser } from "@/components/BotLedgerBrowser";

export default function BotLedgerPage() {
  return (
    <AdminShell
      title="Bot Ledger"
      description="Per-bot financial totals, payment history, and independent settlements."
    >
      <Suspense fallback={<div className="loading-row">Loading Bot Ledger...</div>}>
        <BotLedgerBrowser />
      </Suspense>
    </AdminShell>
  );
}
