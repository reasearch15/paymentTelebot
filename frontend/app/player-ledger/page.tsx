import { AdminShell } from "@/components/AdminShell";
import { PlayerLedgerBrowser } from "@/components/PlayerLedgerBrowser";

export default function PlayerLedgerPage() {
  return (
    <AdminShell
      title="Player Ledger"
      description="Per-player IN/OUT totals and unsettled balances. Transaction status never affects balances."
    >
      <PlayerLedgerBrowser />
    </AdminShell>
  );
}
