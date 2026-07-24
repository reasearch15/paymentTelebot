"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
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
  total_settled_cents: number;
  unsettled_balance_cents: number;
  total_transactions: number;
};

type AccountBalance = {
  payment_account_id: number;
  friendly_name: string;
  unsettled_balance_cents: number;
};

type LedgerResponse = {
  transactions: LedgerTransaction[];
  totals: LedgerTotals;
  account_balances: AccountBalance[];
  limit: number;
  offset: number;
};

type SettlementResponse = {
  id: number;
  amount_cents: number;
  balance_after_cents: number;
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
  const [accountBalances, setAccountBalances] = useState<AccountBalance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [amountInput, setAmountInput] = useState("");
  const [noteInput, setNoteInput] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function loadLedger() {
    setIsLoading(true);
    setError(null);
    try {
      const data = await apiRequest<LedgerResponse>("/transactions?limit=100");
      setTransactions(data.transactions);
      setTotals(data.totals);
      setAccountBalances(data.account_balances);
      if (!selectedAccountId && data.account_balances.length === 1) {
        setSelectedAccountId(String(data.account_balances[0].payment_account_id));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load ledger");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadLedger();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedBalance = useMemo(
    () => accountBalances.find((account) => String(account.payment_account_id) === selectedAccountId) ?? null,
    [accountBalances, selectedAccountId]
  );

  const summaryCards = totals
    ? [
        {
          label: "Current Unsettled Balance",
          value: formatMoney(totals.unsettled_balance_cents),
          note: "Incoming − outgoing − settlements",
        },
        {
          label: "Lifetime Incoming",
          value: formatMoney(totals.total_incoming_cents),
          note: "All IN transactions",
        },
        {
          label: "Lifetime Outgoing",
          value: formatMoney(totals.total_outgoing_cents),
          note: "All OUT transactions",
        },
        {
          label: "Total Transactions",
          value: totals.total_transactions.toString(),
          note: "Payment history is unchanged by settlements",
        },
      ]
    : [];

  function openSettlementModal() {
    setFormError(null);
    setSuccessMessage(null);
    setAmountInput("");
    setNoteInput("");
    if (!selectedAccountId && accountBalances.length === 1) {
      setSelectedAccountId(String(accountBalances[0].payment_account_id));
    }
    setIsModalOpen(true);
  }

  function closeSettlementModal() {
    if (isSubmitting) {
      return;
    }
    setIsModalOpen(false);
    setFormError(null);
  }

  async function submitSettlement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    setFormError(null);
    setSuccessMessage(null);

    if (!selectedAccountId) {
      setFormError("Select a payment account.");
      return;
    }
    if (!amountInput.trim()) {
      setFormError("Enter a settlement amount.");
      return;
    }

    setIsSubmitting(true);
    try {
      const settlement = await apiRequest<SettlementResponse>("/settlements", {
        method: "POST",
        body: JSON.stringify({
          payment_account_id: Number(selectedAccountId),
          amount: amountInput.trim(),
          note: noteInput.trim() || null,
        }),
      });
      setIsModalOpen(false);
      setAmountInput("");
      setNoteInput("");
      setSuccessMessage(
        `Settlement of ${formatMoney(settlement.amount_cents)} recorded. Unsettled balance is now ${formatMoney(settlement.balance_after_cents)} for this account.`
      );
      await loadLedger();
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : "Unable to create settlement");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="integrations-stack">
      {error ? <div className="alert-message">{error}</div> : null}
      {successMessage ? <div className="inline-result">{successMessage}</div> : null}

      <div className="section-heading">
        <div>
          <h2>Balances</h2>
          <p>Settle cash manually without changing payment history.</p>
        </div>
        <button className="primary-button" type="button" onClick={openSettlementModal}>
          Create Settlement
        </button>
      </div>

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

      {isModalOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="settlement-title">
            <div className="email-detail-header">
              <div>
                <h2 id="settlement-title">Create Settlement</h2>
                <p>Enter the amount you are settling now. Payment transactions stay unchanged.</p>
              </div>
              <button className="secondary-button" type="button" onClick={closeSettlementModal} disabled={isSubmitting}>
                Cancel
              </button>
            </div>

            <form className="form-stack" onSubmit={submitSettlement}>
              <div className="field">
                <label htmlFor="settlement-account">Account</label>
                <select
                  id="settlement-account"
                  value={selectedAccountId}
                  onChange={(event) => setSelectedAccountId(event.target.value)}
                  required
                  disabled={isSubmitting}
                >
                  <option value="">Select account</option>
                  {accountBalances.map((account) => (
                    <option key={account.payment_account_id} value={account.payment_account_id}>
                      {account.friendly_name} ({formatMoney(account.unsettled_balance_cents)} unsettled)
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label htmlFor="settlement-balance">Current unsettled balance</label>
                <input
                  id="settlement-balance"
                  value={selectedBalance ? formatMoney(selectedBalance.unsettled_balance_cents) : "—"}
                  readOnly
                />
              </div>

              <div className="field">
                <label htmlFor="settlement-amount">Settlement amount</label>
                <input
                  id="settlement-amount"
                  value={amountInput}
                  onChange={(event) => setAmountInput(event.target.value)}
                  placeholder="15.00"
                  inputMode="decimal"
                  autoComplete="off"
                  disabled={isSubmitting}
                  required
                />
              </div>

              <div className="field">
                <label htmlFor="settlement-note">Note (optional)</label>
                <input
                  id="settlement-note"
                  value={noteInput}
                  onChange={(event) => setNoteInput(event.target.value)}
                  placeholder="Cash pickup"
                  maxLength={500}
                  disabled={isSubmitting}
                />
              </div>

              {formError ? <div className="alert-message">{formError}</div> : null}

              <div className="modal-actions">
                <button className="secondary-button" type="button" onClick={closeSettlementModal} disabled={isSubmitting}>
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={isSubmitting}>
                  {isSubmitting ? "Saving..." : "Confirm Settlement"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}
