"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";

type LedgerTransaction = {
  id: number;
  payment_account_id: number;
  friendly_name: string;
  direction: string;
  amount_cents: number;
  sender_name: string | null;
  receiver_tag: string | null;
  provider_reference: string | null;
  received_at: string;
  telegram_status: string;
};

type LedgerTotals = {
  total_incoming_cents: number;
  total_outgoing_cents: number;
  net_balance_cents: number;
  total_transactions: number;
};

type LedgerResponse = {
  transactions: LedgerTransaction[];
  totals: LedgerTotals;
  limit: number;
  offset: number;
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
  const [totals, setTotals] = useState<LedgerTotals | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadLedger() {
      setIsLoading(true);
      setError(null);
      try {
        const data = await apiRequest<LedgerResponse>("/transactions?limit=100");
        if (!cancelled) {
          setTransactions(data.transactions);
          setTotals(data.totals);
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

    void loadLedger();
    return () => {
      cancelled = true;
    };
  }, []);

  const summaryCards = totals
    ? [
        { label: "Total Incoming", value: formatMoney(totals.total_incoming_cents), note: "All IN transactions" },
        { label: "Total Outgoing", value: formatMoney(totals.total_outgoing_cents), note: "All OUT transactions" },
        { label: "Net Balance", value: formatMoney(totals.net_balance_cents), note: "Incoming minus outgoing" },
        {
          label: "Total Transactions",
          value: totals.total_transactions.toString(),
          note: "Each payment kept as its own row",
        },
      ]
    : [];

  return (
    <div className="integrations-stack">
      {error ? <div className="alert-message">{error}</div> : null}

      <section className="metric-grid ledger-totals" aria-label="Ledger totals">
        {isLoading && !totals ? (
          <article className="metric-card">
            <p className="metric-label">Totals</p>
            <p className="metric-value">…</p>
            <p className="metric-note">Loading ledger totals</p>
          </article>
        ) : (
          summaryCards.map((metric) => (
            <article className="metric-card" key={metric.label}>
              <p className="metric-label">{metric.label}</p>
              <p className="metric-value">{metric.value}</p>
              <p className="metric-note">{metric.note}</p>
            </article>
          ))
        )}
      </section>

      <section className="table-shell">
        <table className="placeholder-table">
          <thead>
            <tr>
              <th>Date/Time</th>
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
