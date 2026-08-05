"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { apiRequest } from "@/lib/api";

type BotIntegration = {
  id: number;
  name: string;
  bot_username: string | null;
  group_id: string | null;
  enabled: boolean;
};

type DeliveryCounts = {
  sent: number;
  failed: number;
  pending: number;
  sending: number;
};

type BotSummary = {
  telegram_integration: BotIntegration;
  current_unsettled_cents: number;
  total_in_cents: number;
  total_settled_cents: number;
  payments_today: number;
  amount_today_cents: number;
  payments_week: number;
  amount_week_cents: number;
  payments_month: number;
  amount_month_cents: number;
  all_time_payments: number;
  all_time_amount_cents: number;
  assigned_gmail_accounts: number;
  delivery_counts: DeliveryCounts;
  last_payment_at: string | null;
  last_settlement_at: string | null;
};

type GmailBreakdownItem = {
  payment_account_id: number;
  gmail_account: string;
  friendly_name: string;
  provider_name: string;
  payment_count: number;
  total_amount_cents: number;
  last_payment_at: string | null;
};

type BotTransaction = {
  transaction_id: number;
  received_at: string;
  sender_name: string | null;
  payment_account_id: number;
  payment_account_name: string;
  payment_gmail: string;
  provider_id: number;
  provider_name: string;
  amount_cents: number;
  delivery_status: string;
  attempt_count: number;
  telegram_message_id: string | null;
  delivery_id: number;
  last_error: string | null;
};

type TransactionListResponse = {
  items: BotTransaction[];
  total: number;
  page: number;
  page_size: number;
};

type BotSettlement = {
  id: number;
  amount_cents: number;
  balance_before_cents: number;
  balance_after_cents: number;
  note: string | null;
  created_by_user_id: string;
  settled_at: string;
  running_settled_total_cents: number;
};

type SettlementListResponse = {
  items: BotSettlement[];
  total: number;
  page: number;
  page_size: number;
};

type Provider = { id: number; name: string };
type PaymentAccount = { id: number; friendly_name: string; provider_id: number };

type TxnFilters = {
  preset: string;
  date_from: string;
  date_to: string;
  payment_account_id: string;
  provider_id: string;
  sender: string;
  delivery_status: string;
  min_amount: string;
  max_amount: string;
};

const EMPTY_TXN_FILTERS: TxnFilters = {
  preset: "all",
  date_from: "",
  date_to: "",
  payment_account_id: "",
  provider_id: "",
  sender: "",
  delivery_status: "",
  min_amount: "",
  max_amount: "",
};

function formatMoney(cents: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(cents / 100);
}

function formatDate(value: string | null) {
  if (!value) {
    return "—";
  }
  return new Intl.DateTimeFormat(undefined, {
    timeZone: "Asia/Kathmandu",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function statusIcon(status: string) {
  if (status === "sent") {
    return "✓";
  }
  if (status === "failed") {
    return "✗";
  }
  if (status === "sending") {
    return "…";
  }
  return "○";
}

function integrationLabel(item: BotIntegration) {
  const username = item.bot_username ? ` — @${item.bot_username}` : "";
  const disabled = item.enabled ? "" : " (Disabled)";
  return `${item.name}${username}${disabled}`;
}

function defaultIntegrationId(integrations: BotIntegration[]): number | null {
  const enabled = integrations.find((item) => item.enabled);
  if (enabled) {
    return enabled.id;
  }
  return integrations[0]?.id ?? null;
}

export function BotLedgerBrowser() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [integrations, setIntegrations] = useState<BotIntegration[]>([]);
  const [integrationId, setIntegrationId] = useState<number | null>(null);
  const [summary, setSummary] = useState<BotSummary | null>(null);
  const [breakdown, setBreakdown] = useState<GmailBreakdownItem[]>([]);
  const [transactions, setTransactions] = useState<BotTransaction[]>([]);
  const [txnTotal, setTxnTotal] = useState(0);
  const [txnPage, setTxnPage] = useState(1);
  const [txnFilters, setTxnFilters] = useState<TxnFilters>(EMPTY_TXN_FILTERS);
  const [appliedTxnFilters, setAppliedTxnFilters] = useState<TxnFilters>(EMPTY_TXN_FILTERS);
  const [settlements, setSettlements] = useState<BotSettlement[]>([]);
  const [settlementTotal, setSettlementTotal] = useState(0);
  const [settlementPage, setSettlementPage] = useState(1);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [accounts, setAccounts] = useState<PaymentAccount[]>([]);

  const [settlementAmount, setSettlementAmount] = useState("");
  const [settlementNote, setSettlementNote] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const [isLoading, setIsLoading] = useState(true);
  const [isTxnLoading, setIsTxnLoading] = useState(false);
  const [isSettlementLoading, setIsSettlementLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const pageSize = 50;
  const txnPages = useMemo(() => Math.max(1, Math.ceil(txnTotal / pageSize)), [txnTotal]);
  const settlementPages = useMemo(
    () => Math.max(1, Math.ceil(settlementTotal / pageSize)),
    [settlementTotal],
  );

  const selectedIntegration = useMemo(
    () => integrations.find((item) => item.id === integrationId) ?? null,
    [integrations, integrationId],
  );

  const setIntegrationInUrl = useCallback(
    (id: number) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("integration", String(id));
      router.replace(`${pathname}?${params.toString()}`);
    },
    [pathname, router, searchParams],
  );

  const loadIntegrations = useCallback(async () => {
    const [botRows, providerRows, accountRows] = await Promise.all([
      apiRequest<BotIntegration[]>("/bot-ledger/integrations"),
      apiRequest<Provider[]>("/providers"),
      apiRequest<PaymentAccount[]>("/payment-accounts"),
    ]);
    setIntegrations(botRows);
    setProviders(providerRows);
    setAccounts(accountRows);

    const fromUrl = Number(searchParams.get("integration") || "");
    const urlMatch = Number.isFinite(fromUrl) && botRows.some((item) => item.id === fromUrl) ? fromUrl : null;
    const chosen = urlMatch ?? defaultIntegrationId(botRows);
    setIntegrationId((current) => current ?? chosen);
    if (chosen != null && urlMatch == null) {
      const params = new URLSearchParams(searchParams.toString());
      params.set("integration", String(chosen));
      router.replace(`${pathname}?${params.toString()}`);
    }
  }, [pathname, router, searchParams]);

  const loadSummaryAndBreakdown = useCallback(async (id: number) => {
    const [summaryData, breakdownData] = await Promise.all([
      apiRequest<BotSummary>(`/bot-ledger/${id}/summary`),
      apiRequest<GmailBreakdownItem[]>(`/bot-ledger/${id}/gmail-breakdown`),
    ]);
    setSummary(summaryData);
    setBreakdown(breakdownData);
  }, []);

  const loadTransactions = useCallback(
    async (id: number, filters: TxnFilters, page: number) => {
      setIsTxnLoading(true);
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
        preset: filters.preset || "all",
      });
      if (filters.date_from) params.set("date_from", filters.date_from);
      if (filters.date_to) params.set("date_to", filters.date_to);
      if (filters.payment_account_id) params.set("payment_account_id", filters.payment_account_id);
      if (filters.provider_id) params.set("provider_id", filters.provider_id);
      if (filters.sender.trim()) params.set("sender", filters.sender.trim());
      if (filters.delivery_status) params.set("delivery_status", filters.delivery_status);
      if (filters.min_amount.trim()) params.set("min_amount", filters.min_amount.trim());
      if (filters.max_amount.trim()) params.set("max_amount", filters.max_amount.trim());

      const data = await apiRequest<TransactionListResponse>(
        `/bot-ledger/${id}/transactions?${params.toString()}`,
      );
      setTransactions(data.items);
      setTxnTotal(data.total);
      setTxnPage(data.page);
      setIsTxnLoading(false);
    },
    [],
  );

  const loadSettlements = useCallback(async (id: number, page: number) => {
    setIsSettlementLoading(true);
    const data = await apiRequest<SettlementListResponse>(
      `/bot-ledger/${id}/settlements?page=${page}&page_size=${pageSize}`,
    );
    setSettlements(data.items);
    setSettlementTotal(data.total);
    setSettlementPage(data.page);
    setIsSettlementLoading(false);
  }, []);

  const reloadAll = useCallback(
    async (id: number) => {
      setIsLoading(true);
      setError(null);
      try {
        await Promise.all([
          loadSummaryAndBreakdown(id),
          loadTransactions(id, appliedTxnFilters, txnPage),
          loadSettlements(id, settlementPage),
        ]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load Bot Ledger.");
      } finally {
        setIsLoading(false);
      }
    },
    [appliedTxnFilters, loadSettlements, loadSummaryAndBreakdown, loadTransactions, settlementPage, txnPage],
  );

  useEffect(() => {
    void loadIntegrations().catch((err) => {
      setError(err instanceof Error ? err.message : "Failed to load integrations.");
      setIsLoading(false);
    });
  }, [loadIntegrations]);

  useEffect(() => {
    if (integrationId == null) {
      setIsLoading(false);
      return;
    }
    void reloadAll(integrationId);
  }, [integrationId, reloadAll]);

  function onSelectIntegration(nextId: number) {
    setTxnPage(1);
    setSettlementPage(1);
    setIntegrationId(nextId);
    setIntegrationInUrl(nextId);
    setActionMessage(null);
  }

  function submitTxnFilters(event: FormEvent) {
    event.preventDefault();
    setTxnPage(1);
    setAppliedTxnFilters(txnFilters);
  }

  async function submitSettlement() {
    if (integrationId == null || !selectedIntegration) {
      return;
    }
    setIsSubmitting(true);
    setError(null);
    setActionMessage(null);
    try {
      await apiRequest(`/bot-ledger/${integrationId}/settlements`, {
        method: "POST",
        body: JSON.stringify({
          amount: settlementAmount,
          note: settlementNote.trim() || null,
        }),
      });
      setSettlementAmount("");
      setSettlementNote("");
      setConfirmOpen(false);
      setActionMessage(`Settlement recorded for ${selectedIntegration.name}.`);
      setSettlementPage(1);
      await Promise.all([
        loadSummaryAndBreakdown(integrationId),
        loadSettlements(integrationId, 1),
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to record settlement.");
      setConfirmOpen(false);
    } finally {
      setIsSubmitting(false);
    }
  }

  if (integrations.length === 0 && !isLoading) {
    return <div className="empty-block">No Telegram integrations available.</div>;
  }

  return (
    <div className="integrations-stack">
      <div className="section-heading">
        <div>
          <h2>Bot Ledger</h2>
          <p>Select a Telegram integration to view its independent financial ledger.</p>
        </div>
        <select
          aria-label="Telegram integration"
          value={integrationId ?? ""}
          onChange={(event) => onSelectIntegration(Number(event.target.value))}
        >
          {integrations.map((item) => (
            <option key={item.id} value={item.id}>
              {integrationLabel(item)}
            </option>
          ))}
        </select>
      </div>

      {error ? <div className="alert-message">{error}</div> : null}
      {actionMessage ? <div className="inline-result">{actionMessage}</div> : null}

      {isLoading || !summary ? (
        <div className="loading-row">Loading Bot Ledger...</div>
      ) : (
        <>
          <section className="metric-grid ledger-totals" aria-label="Primary balances">
            <article className="metric-card">
              <p className="metric-label">Current Unsettled</p>
              <p className="metric-value">{formatMoney(summary.current_unsettled_cents)}</p>
              <p className="metric-note">{summary.telegram_integration.name}</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">Total In</p>
              <p className="metric-value">{formatMoney(summary.total_in_cents)}</p>
              <p className="metric-note">{summary.all_time_payments} payments</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">Total Settled</p>
              <p className="metric-value">{formatMoney(summary.total_settled_cents)}</p>
              <p className="metric-note">Last: {formatDate(summary.last_settlement_at)}</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">Assigned Gmail</p>
              <p className="metric-value">{summary.assigned_gmail_accounts}</p>
              <p className="metric-note">Current routes only</p>
            </article>
          </section>

          <section className="metric-grid" aria-label="Period and delivery health">
            <article className="metric-card">
              <p className="metric-label">Today</p>
              <p className="metric-value">{formatMoney(summary.amount_today_cents)}</p>
              <p className="metric-note">{summary.payments_today} payments</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">This Week</p>
              <p className="metric-value">{formatMoney(summary.amount_week_cents)}</p>
              <p className="metric-note">{summary.payments_week} payments</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">This Month</p>
              <p className="metric-value">{formatMoney(summary.amount_month_cents)}</p>
              <p className="metric-note">{summary.payments_month} payments</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">All Time</p>
              <p className="metric-value">{formatMoney(summary.all_time_amount_cents)}</p>
              <p className="metric-note">{summary.all_time_payments} payments</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">Delivery Health</p>
              <p className="metric-value">
                {summary.delivery_counts.sent}/
                {summary.delivery_counts.sent + summary.delivery_counts.failed}
              </p>
              <p className="metric-note">
                Sent {summary.delivery_counts.sent} · Failed {summary.delivery_counts.failed} · Pending{" "}
                {summary.delivery_counts.pending} · Sending {summary.delivery_counts.sending}
              </p>
            </article>
          </section>

          <div className="bot-ledger-layout">
            <section className="management-section">
              <div className="section-heading">
                <div>
                  <h2>Payment History</h2>
                  <p>Unique payments belonging to this bot via telegram_deliveries.</p>
                </div>
              </div>

              <form className="filters-bar" onSubmit={submitTxnFilters}>
                <select
                  value={txnFilters.preset}
                  onChange={(event) =>
                    setTxnFilters((current) => ({ ...current, preset: event.target.value }))
                  }
                  aria-label="Date preset"
                >
                  <option value="all">All Time</option>
                  <option value="today">Today</option>
                  <option value="week">This Week</option>
                  <option value="month">This Month</option>
                  <option value="custom">Custom</option>
                </select>
                {txnFilters.preset === "custom" ? (
                  <>
                    <input
                      type="date"
                      value={txnFilters.date_from}
                      onChange={(event) =>
                        setTxnFilters((current) => ({ ...current, date_from: event.target.value }))
                      }
                      aria-label="From date"
                    />
                    <input
                      type="date"
                      value={txnFilters.date_to}
                      onChange={(event) =>
                        setTxnFilters((current) => ({ ...current, date_to: event.target.value }))
                      }
                      aria-label="To date"
                    />
                  </>
                ) : null}
                <select
                  value={txnFilters.payment_account_id}
                  onChange={(event) =>
                    setTxnFilters((current) => ({ ...current, payment_account_id: event.target.value }))
                  }
                  aria-label="Gmail account"
                >
                  <option value="">All Gmail accounts</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.friendly_name}
                    </option>
                  ))}
                </select>
                <select
                  value={txnFilters.provider_id}
                  onChange={(event) =>
                    setTxnFilters((current) => ({ ...current, provider_id: event.target.value }))
                  }
                  aria-label="Provider"
                >
                  <option value="">All providers</option>
                  {providers.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name}
                    </option>
                  ))}
                </select>
                <select
                  value={txnFilters.delivery_status}
                  onChange={(event) =>
                    setTxnFilters((current) => ({ ...current, delivery_status: event.target.value }))
                  }
                  aria-label="Delivery status"
                >
                  <option value="">All delivery statuses</option>
                  <option value="sent">Sent</option>
                  <option value="failed">Failed</option>
                  <option value="pending">Pending</option>
                  <option value="sending">Sending</option>
                </select>
                <input
                  value={txnFilters.sender}
                  onChange={(event) => setTxnFilters((current) => ({ ...current, sender: event.target.value }))}
                  placeholder="Sender search"
                  aria-label="Sender search"
                />
                <input
                  value={txnFilters.min_amount}
                  onChange={(event) =>
                    setTxnFilters((current) => ({ ...current, min_amount: event.target.value }))
                  }
                  placeholder="Min amount"
                  aria-label="Minimum amount"
                />
                <input
                  value={txnFilters.max_amount}
                  onChange={(event) =>
                    setTxnFilters((current) => ({ ...current, max_amount: event.target.value }))
                  }
                  placeholder="Max amount"
                  aria-label="Maximum amount"
                />
                <button className="primary-button" type="submit">
                  Apply filters
                </button>
              </form>

              <section className="table-shell">
                <table className="placeholder-table">
                  <thead>
                    <tr>
                      <th>Date / Time</th>
                      <th>Sender</th>
                      <th>Gmail Account</th>
                      <th>Provider</th>
                      <th>Amount</th>
                      <th>Delivery</th>
                      <th>Attempts</th>
                      <th>Message ID</th>
                      <th>Txn ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {isTxnLoading ? (
                      <tr>
                        <td colSpan={9}>Loading payments...</td>
                      </tr>
                    ) : transactions.length === 0 ? (
                      <tr>
                        <td colSpan={9}>No payments for this bot.</td>
                      </tr>
                    ) : (
                      transactions.map((row) => (
                        <tr key={row.delivery_id}>
                          <td>{formatDate(row.received_at)}</td>
                          <td>{row.sender_name ?? "—"}</td>
                          <td>{row.payment_account_name}</td>
                          <td>{row.provider_name}</td>
                          <td>{formatMoney(row.amount_cents)}</td>
                          <td>
                            {statusIcon(row.delivery_status)} {row.delivery_status}
                          </td>
                          <td>{row.attempt_count}</td>
                          <td>{row.telegram_message_id ?? "—"}</td>
                          <td>{row.transaction_id}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
                <div className="load-more-row">
                  <span>
                    Page {txnPage} of {txnPages} · {txnTotal} total
                  </span>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={txnPage <= 1 || isTxnLoading || integrationId == null}
                    onClick={() => {
                      const next = Math.max(1, txnPage - 1);
                      setTxnPage(next);
                      if (integrationId != null) {
                        void loadTransactions(integrationId, appliedTxnFilters, next);
                      }
                    }}
                  >
                    Previous
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={txnPage >= txnPages || isTxnLoading || integrationId == null}
                    onClick={() => {
                      const next = txnPage + 1;
                      setTxnPage(next);
                      if (integrationId != null) {
                        void loadTransactions(integrationId, appliedTxnFilters, next);
                      }
                    }}
                  >
                    Next
                  </button>
                </div>
              </section>
            </section>

            <aside className="bot-ledger-side">
              <section className="management-section panel-card">
                <div className="section-heading">
                  <div>
                    <h2>Settlement Panel</h2>
                    <p>Independent from Gmail settlements.</p>
                  </div>
                </div>
                <dl className="account-details">
                  <div>
                    <dt>Current Unsettled</dt>
                    <dd>{formatMoney(summary.current_unsettled_cents)}</dd>
                  </div>
                </dl>
                <form
                  className="form-stack"
                  onSubmit={(event) => {
                    event.preventDefault();
                    setConfirmOpen(true);
                  }}
                >
                  <div className="field">
                    <label htmlFor="bot-settlement-amount">Settlement amount</label>
                    <input
                      id="bot-settlement-amount"
                      value={settlementAmount}
                      onChange={(event) => setSettlementAmount(event.target.value)}
                      placeholder="500.00"
                      required
                      disabled={isSubmitting}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="bot-settlement-note">Note (optional)</label>
                    <input
                      id="bot-settlement-note"
                      value={settlementNote}
                      onChange={(event) => setSettlementNote(event.target.value)}
                      maxLength={500}
                      disabled={isSubmitting}
                    />
                  </div>
                  <button className="primary-button" type="submit" disabled={isSubmitting || !settlementAmount.trim()}>
                    Record Settlement
                  </button>
                </form>
              </section>

              <section className="management-section panel-card">
                <div className="section-heading">
                  <div>
                    <h2>Settlement History</h2>
                    <p>Newest first. Append-only.</p>
                  </div>
                </div>
                <section className="table-shell">
                  <table className="placeholder-table">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Amount</th>
                        <th>Note</th>
                        <th>By</th>
                        <th>Running</th>
                      </tr>
                    </thead>
                    <tbody>
                      {isSettlementLoading ? (
                        <tr>
                          <td colSpan={5}>Loading...</td>
                        </tr>
                      ) : settlements.length === 0 ? (
                        <tr>
                          <td colSpan={5}>No settlements yet.</td>
                        </tr>
                      ) : (
                        settlements.map((row) => (
                          <tr key={row.id}>
                            <td>{formatDate(row.settled_at)}</td>
                            <td>{formatMoney(row.amount_cents)}</td>
                            <td>{row.note ?? "—"}</td>
                            <td>{row.created_by_user_id}</td>
                            <td>{formatMoney(row.running_settled_total_cents)}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                  <div className="load-more-row">
                    <span>
                      Page {settlementPage} of {settlementPages}
                    </span>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={settlementPage <= 1 || isSettlementLoading || integrationId == null}
                      onClick={() => {
                        const next = Math.max(1, settlementPage - 1);
                        setSettlementPage(next);
                        if (integrationId != null) {
                          void loadSettlements(integrationId, next);
                        }
                      }}
                    >
                      Previous
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={
                        settlementPage >= settlementPages || isSettlementLoading || integrationId == null
                      }
                      onClick={() => {
                        const next = settlementPage + 1;
                        setSettlementPage(next);
                        if (integrationId != null) {
                          void loadSettlements(integrationId, next);
                        }
                      }}
                    >
                      Next
                    </button>
                  </div>
                </section>
              </section>

              <section className="management-section panel-card">
                <div className="section-heading">
                  <div>
                    <h2>Gmail Breakdown</h2>
                    <p>Historical sources from deliveries, not current routes.</p>
                  </div>
                </div>
                {breakdown.length === 0 ? (
                  <div className="empty-block">No payment sources yet.</div>
                ) : (
                  <div className="account-grid">
                    {breakdown.map((item) => (
                      <article className="account-card" key={item.payment_account_id}>
                        <div className="account-card-header">
                          <div>
                            <h3>{item.friendly_name}</h3>
                            <p>{item.gmail_account}</p>
                          </div>
                        </div>
                        <dl className="account-details">
                          <div>
                            <dt>Provider</dt>
                            <dd>{item.provider_name}</dd>
                          </div>
                          <div>
                            <dt>Payments</dt>
                            <dd>{item.payment_count}</dd>
                          </div>
                          <div>
                            <dt>Total</dt>
                            <dd>{formatMoney(item.total_amount_cents)}</dd>
                          </div>
                          <div>
                            <dt>Last payment</dt>
                            <dd>{formatDate(item.last_payment_at)}</dd>
                          </div>
                        </dl>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </aside>
          </div>
        </>
      )}

      {confirmOpen && selectedIntegration ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="bot-settle-title">
            <div className="email-detail-header">
              <div>
                <h2 id="bot-settle-title">Confirm settlement</h2>
                <p>
                  Record a ${settlementAmount.trim()} settlement for {selectedIntegration.name}?
                </p>
              </div>
              <button
                className="secondary-button"
                type="button"
                onClick={() => setConfirmOpen(false)}
                disabled={isSubmitting}
              >
                Cancel
              </button>
            </div>
            <div className="modal-actions">
              <button className="primary-button" type="button" onClick={() => void submitSettlement()} disabled={isSubmitting}>
                {isSubmitting ? "Recording…" : "Confirm"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
