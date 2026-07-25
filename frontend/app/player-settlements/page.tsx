import { AdminShell } from "@/components/AdminShell";
import { PlayerSettlementsBrowser } from "@/components/PlayerSettlementsBrowser";

export default function PlayerSettlementsPage() {
  return (
    <AdminShell
      title="Player Settlements"
      description="Create and review sender-level settlements without changing payment history or global account settlements."
    >
      <PlayerSettlementsBrowser />
    </AdminShell>
  );
}
