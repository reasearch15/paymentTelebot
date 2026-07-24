"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";

type Settlement = {
  id: number;
  payment_account_id: number;
  friendly_name: string;
  amount_cents: number;
  balance_before_cents: number;
  balance_after_cents: number;
  note: string | null;
  status: string;
  settled_at: string;
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatMoney(cents: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(cents / 100);
}

export function SettlementsBrowser() {
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadSettlements() {
      setIsLoading(true);
      setError(null);
      try {
        const data = await apiRequest<Settlement[]>("/settlements?limit=100");
        if (!cancelled) {
          setSettlements(data);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Unable to load settlements");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadSettlements();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="integrations-stack">
      {error ? <div className="alert-message">{error}</div> : null}
      <section className="table-shell">
        <table className="placeholder-table">
          <thead>
            <tr>
              <th>Date/Time</th>
              <th>Account</th>
              <th>Amount</th>
              <th>Note</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={5}>Loading settlements...</td>
              </tr>
            ) : settlements.length === 0 ? (
              <tr>
                <td colSpan={5}>No settlements recorded.</td>
              </tr>
            ) : (
              settlements.map((settlement) => (
                <tr key={settlement.id}>
                  <td>{formatDate(settlement.settled_at)}</td>
                  <td>{settlement.friendly_name}</td>
                  <td>{formatMoney(settlement.amount_cents)}</td>
                  <td>{settlement.note ?? "—"}</td>
                  <td>{settlement.status}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
