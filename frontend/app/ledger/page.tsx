import { AdminShell } from "@/components/AdminShell";

export default function LedgerPage() {
  return (
    <AdminShell title="Ledger" description="Transactions will appear here after parsers and listeners are added.">
      <section className="table-shell">
        <table className="placeholder-table">
          <thead>
            <tr>
              <th>Received</th>
              <th>Direction</th>
              <th>Amount</th>
              <th>Reference</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={5}>No transactions recorded.</td>
            </tr>
          </tbody>
        </table>
      </section>
    </AdminShell>
  );
}
