"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";

type LedgerTransaction = {
  id: number;
  payment_account_id: number;
  provider_name: string;
  friendly_name: string;
  direction: string;
  amount_cents: number;
  sender_name: string | null;
  receiver_tag: string | null;
  provider_reference: string | null;
  received_at: string;
  telegram_status: string;
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

export function LedgerBrowser() {
  const [transactions, setTransactions] = useState<LedgerTransaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadTransactions() {
      setIsLoading(true);
      setError(null);
      try {
        const data = await apiRequest<LedgerTransaction[]>("/transactions?limit=100");
        if (!cancelled) {
          setTransactions(data);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Unable to load ledger");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadTransactions();
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
              <th>Received</th>
              <th>Account</th>
              <th>Direction</th>
              <th>Amount</th>
              <th>Sender</th>
              <th>Reference</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={7}>Loading transactions...</td>
              </tr>
            ) : transactions.length === 0 ? (
              <tr>
                <td colSpan={7}>No transactions recorded.</td>
              </tr>
            ) : (
              transactions.map((transaction) => (
                <tr key={transaction.id}>
                  <td>{formatDate(transaction.received_at)}</td>
                  <td>{transaction.friendly_name}</td>
                  <td>{transaction.direction}</td>
                  <td>{formatMoney(transaction.amount_cents)}</td>
                  <td>{transaction.sender_name ?? "Unknown"}</td>
                  <td>{transaction.provider_reference ?? transaction.receiver_tag ?? "—"}</td>
                  <td>{transaction.telegram_status}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
