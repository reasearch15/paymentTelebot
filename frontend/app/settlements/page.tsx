import { AdminShell } from "@/components/AdminShell";

export default function SettlementsPage() {
  return (
    <AdminShell title="Settlements" description="Settlement history will be shown once settlement actions are implemented.">
      <section className="table-shell">
        <table className="placeholder-table">
          <thead>
            <tr>
              <th>Settled</th>
              <th>Amount</th>
              <th>Balance Before</th>
              <th>Balance After</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={5}>No settlements recorded.</td>
            </tr>
          </tbody>
        </table>
      </section>
    </AdminShell>
  );
}
