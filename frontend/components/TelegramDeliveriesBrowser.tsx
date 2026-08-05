"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { API_BASE_URL, apiRequest } from "@/lib/api";

type Provider = { id: number; name: string };
type PaymentAccount = { id: number; friendly_name: string; provider_id: number };
type TelegramIntegration = { id: number; name: string; bot_username: string | null };

type DeliveryListItem = {
  id: number;
  status: string;
  telegram_integration_id: number;
  integration_name: string;
  bot_username: string | null;
  group_id: string | null;
  transaction_id: number;
  sender_name: string | null;
  amount_cents: number;
  provider_id: number;
  provider_name: string;
  payment_account_id: number;
  payment_account_name: string;
  payment_gmail: string;
  gmail_message_id: string;
  attempt_count: number;
  telegram_message_id: string | null;
  created_at: string;
  last_attempt_at: string | null;
  sent_at: string | null;
  last_error: string | null;
  can_retry: boolean;
};

type DeliveryAttempt = {
  id: number;
  attempt_number: number;
  status: string;
  telegram_message_id: string | null;
  error_message: string | null;
  attempted_at: string;
  completed_at: string | null;
};

type DeliveryDetail = DeliveryListItem & {
  attempts: DeliveryAttempt[];
  timeline: Array<{ event: string; at: string | null; detail: string | null }>;
  receiver_tag: string | null;
  provider_reference: string | null;
  transaction_received_at: string | null;
  direction: string | null;
};

type ListResponse = {
  items: DeliveryListItem[];
  total: number;
  page: number;
  page_size: number;
};

type Filters = {
  status: string;
  integration: string;
  payment_account: string;
  provider: string;
  sender: string;
  amount_min: string;
  amount_max: string;
  date_from: string;
  date_to: string;
  search: string;
};

const EMPTY_FILTERS: Filters = {
  status: "",
  integration: "",
  payment_account: "",
  provider: "",
  sender: "",
  amount_min: "",
  amount_max: "",
  date_from: "",
  date_to: "",
  search: "",
};

function formatDate(value: string | null) {
  if (!value) {
    return "—";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatMoney(cents: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(cents / 100);
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

function buildQuery(filters: Filters, page: number, pageSize: number) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  Object.entries(filters).forEach(([key, value]) => {
    if (value.trim()) {
      params.set(key, value.trim());
    }
  });
  return params.toString();
}

export function TelegramDeliveriesBrowser() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [items, setItems] = useState<DeliveryListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [accounts, setAccounts] = useState<PaymentAccount[]>([]);
  const [integrations, setIntegrations] = useState<TelegramIntegration[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DeliveryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [pendingRetryId, setPendingRetryId] = useState<number | null>(null);
  const [bulkPending, setBulkPending] = useState(false);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [total, pageSize]);

  async function loadLookups() {
    const [providerRows, accountRows, integrationRows] = await Promise.all([
      apiRequest<Provider[]>("/providers"),
      apiRequest<PaymentAccount[]>("/payment-accounts"),
      apiRequest<TelegramIntegration[]>("/telegram-integrations"),
    ]);
    setProviders(providerRows);
    setAccounts(accountRows);
    setIntegrations(integrationRows);
  }

  async function loadDeliveries(nextFilters = appliedFilters, nextPage = page) {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiRequest<ListResponse>(
        `/telegram-deliveries?${buildQuery(nextFilters, nextPage, pageSize)}`,
      );
      setItems(response.items);
      setTotal(response.total);
      setPage(response.page);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load deliveries.");
    } finally {
      setIsLoading(false);
    }
  }

  async function openDetail(deliveryId: number) {
    setSelectedId(deliveryId);
    setDetailLoading(true);
    setDetail(null);
    try {
      const response = await apiRequest<DeliveryDetail>(`/telegram-deliveries/${deliveryId}`);
      setDetail(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load delivery detail.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function retryOne(deliveryId: number) {
    setPendingRetryId(deliveryId);
    setActionMessage(null);
    try {
      const result = await apiRequest<{ ok: boolean; reason: string | null; status: string | null }>(
        `/telegram-deliveries/${deliveryId}/retry`,
        { method: "POST" },
      );
      setActionMessage(
        result.ok
          ? `Delivery #${deliveryId} sent.`
          : `Retry finished with status ${result.status ?? "unknown"}${result.reason ? ` (${result.reason})` : ""}.`,
      );
      await loadDeliveries();
      if (selectedId === deliveryId) {
        await openDetail(deliveryId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Retry failed.");
    } finally {
      setPendingRetryId(null);
    }
  }

  async function retryFiltered() {
    setBulkPending(true);
    setActionMessage(null);
    setError(null);
    try {
      const body = {
        status: appliedFilters.status || "failed",
        integration_id: appliedFilters.integration ? Number(appliedFilters.integration) : null,
        payment_account_id: appliedFilters.payment_account ? Number(appliedFilters.payment_account) : null,
        provider_id: appliedFilters.provider ? Number(appliedFilters.provider) : null,
        sender: appliedFilters.sender || null,
        amount_min: appliedFilters.amount_min || null,
        amount_max: appliedFilters.amount_max || null,
        date_from: appliedFilters.date_from || null,
        date_to: appliedFilters.date_to || null,
        search: appliedFilters.search || null,
        limit: 100,
      };
      const result = await apiRequest<{ attempted: number; succeeded: number; failed: number; skipped: number }>(
        "/telegram-deliveries/retry-filter",
        { method: "POST", body: JSON.stringify(body) },
      );
      setActionMessage(
        `Bulk retry: ${result.succeeded} sent, ${result.failed} failed, ${result.skipped} skipped (${result.attempted} attempted).`,
      );
      await loadDeliveries();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bulk retry failed.");
    } finally {
      setBulkPending(false);
    }
  }

  async function exportCsv() {
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/telegram-deliveries/export?${buildQuery(appliedFilters, 1, 200)}`, {
        credentials: "include",
      });
      if (!response.ok) {
        throw new Error("CSV export failed.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "telegram-deliveries.csv";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "CSV export failed.");
    }
  }

  useEffect(() => {
    void loadLookups().catch((err) => {
      setError(err instanceof Error ? err.message : "Failed to load filters.");
    });
  }, []);

  useEffect(() => {
    void loadDeliveries(appliedFilters, page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appliedFilters, page]);

  function submitFilters(event: FormEvent) {
    event.preventDefault();
    setPage(1);
    setAppliedFilters(filters);
  }

  return (
    <div className="integrations-stack">
      <form className="filters-bar" onSubmit={submitFilters}>
        <select
          value={filters.status}
          onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value }))}
          aria-label="Status"
        >
          <option value="">All statuses</option>
          <option value="sent">Sent</option>
          <option value="failed">Failed</option>
          <option value="pending">Pending</option>
          <option value="sending">Sending</option>
        </select>
        <select
          value={filters.integration}
          onChange={(event) => setFilters((current) => ({ ...current, integration: event.target.value }))}
          aria-label="Integration"
        >
          <option value="">All integrations</option>
          {integrations.map((integration) => (
            <option key={integration.id} value={integration.id}>
              {integration.name}
            </option>
          ))}
        </select>
        <select
          value={filters.payment_account}
          onChange={(event) => setFilters((current) => ({ ...current, payment_account: event.target.value }))}
          aria-label="Payment account"
        >
          <option value="">All payment accounts</option>
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.friendly_name}
            </option>
          ))}
        </select>
        <select
          value={filters.provider}
          onChange={(event) => setFilters((current) => ({ ...current, provider: event.target.value }))}
          aria-label="Provider"
        >
          <option value="">All providers</option>
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.name}
            </option>
          ))}
        </select>
        <input
          value={filters.sender}
          onChange={(event) => setFilters((current) => ({ ...current, sender: event.target.value }))}
          placeholder="Sender name"
          aria-label="Sender name"
        />
        <input
          value={filters.amount_min}
          onChange={(event) => setFilters((current) => ({ ...current, amount_min: event.target.value }))}
          placeholder="Min amount"
          aria-label="Minimum amount"
        />
        <input
          value={filters.amount_max}
          onChange={(event) => setFilters((current) => ({ ...current, amount_max: event.target.value }))}
          placeholder="Max amount"
          aria-label="Maximum amount"
        />
        <input
          type="date"
          value={filters.date_from}
          onChange={(event) => setFilters((current) => ({ ...current, date_from: event.target.value }))}
          aria-label="From date"
        />
        <input
          type="date"
          value={filters.date_to}
          onChange={(event) => setFilters((current) => ({ ...current, date_to: event.target.value }))}
          aria-label="To date"
        />
        <input
          value={filters.search}
          onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
          placeholder="Search sender, txn, message IDs"
          aria-label="Search"
        />
        <button className="primary-button" type="submit">
          Apply filters
        </button>
        <button className="secondary-button" type="button" onClick={() => void exportCsv()}>
          Export CSV
        </button>
        <button className="secondary-button" type="button" onClick={() => void retryFiltered()} disabled={bulkPending}>
          {bulkPending ? "Retrying…" : "Retry filtered failed"}
        </button>
      </form>

      {error ? <div className="alert-message">{error}</div> : null}
      {actionMessage ? <div className="inline-result">{actionMessage}</div> : null}

      <section className="table-shell">
        <table className="placeholder-table">
          <thead>
            <tr>
              <th></th>
              <th>Integration</th>
              <th>Bot</th>
              <th>Group</th>
              <th>Txn</th>
              <th>Sender</th>
              <th>Amount</th>
              <th>Provider</th>
              <th>Payment Gmail</th>
              <th>Attempts</th>
              <th>Message ID</th>
              <th>Created</th>
              <th>Last Attempt</th>
              <th>Sent</th>
              <th>Last Error</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={16}>Loading deliveries...</td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={16}>No Telegram deliveries found.</td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.id} className="clickable-row" onClick={() => void openDetail(item.id)}>
                  <td title={item.status}>{statusIcon(item.status)}</td>
                  <td>{item.integration_name}</td>
                  <td>{item.bot_username ? `@${item.bot_username}` : "—"}</td>
                  <td>{item.group_id ?? "—"}</td>
                  <td>{item.transaction_id}</td>
                  <td>{item.sender_name ?? "—"}</td>
                  <td>{formatMoney(item.amount_cents)}</td>
                  <td>{item.provider_name}</td>
                  <td>{item.payment_gmail}</td>
                  <td>{item.attempt_count}</td>
                  <td>{item.telegram_message_id ?? "—"}</td>
                  <td>{formatDate(item.created_at)}</td>
                  <td>{formatDate(item.last_attempt_at)}</td>
                  <td>{formatDate(item.sent_at)}</td>
                  <td>{item.last_error ?? (item.status === "sent" ? "Sent" : "—")}</td>
                  <td>
                    {item.can_retry ? (
                      <button
                        className="secondary-button"
                        type="button"
                        disabled={pendingRetryId === item.id}
                        onClick={(event) => {
                          event.stopPropagation();
                          void retryOne(item.id);
                        }}
                      >
                        {pendingRetryId === item.id ? "Retrying…" : "Retry"}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <div className="load-more-row">
          <span>
            Page {page} of {totalPages} · {total} total
          </span>
          <button
            className="secondary-button"
            type="button"
            disabled={page <= 1 || isLoading}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            Previous
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={page >= totalPages || isLoading}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
          </button>
        </div>
      </section>

      {selectedId !== null ? (
        <div className="drawer-backdrop" role="presentation" onClick={() => setSelectedId(null)}>
          <aside
            className="delivery-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Delivery detail"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="email-detail-header">
              <div>
                <h2>Delivery #{selectedId}</h2>
                <p>Telegram outbox detail and attempt history.</p>
              </div>
              <button className="secondary-button" type="button" onClick={() => setSelectedId(null)}>
                Close
              </button>
            </div>
            {detailLoading || !detail ? (
              <div className="loading-row">Loading detail...</div>
            ) : (
              <div className="form-stack">
                <dl className="account-details">
                  <div>
                    <dt>Status</dt>
                    <dd>
                      {statusIcon(detail.status)} {detail.status}
                    </dd>
                  </div>
                  <div>
                    <dt>Transaction</dt>
                    <dd>
                      #{detail.transaction_id} · {detail.sender_name ?? "Unknown"} · {formatMoney(detail.amount_cents)}
                    </dd>
                  </div>
                  <div>
                    <dt>Integration</dt>
                    <dd>
                      {detail.integration_name}
                      {detail.bot_username ? ` (@${detail.bot_username})` : ""}
                    </dd>
                  </div>
                  <div>
                    <dt>Payment account</dt>
                    <dd>
                      {detail.payment_account_name} · {detail.payment_gmail}
                    </dd>
                  </div>
                  <div>
                    <dt>Provider</dt>
                    <dd>{detail.provider_name}</dd>
                  </div>
                  <div>
                    <dt>Telegram message ID</dt>
                    <dd>{detail.telegram_message_id ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Last error</dt>
                    <dd>{detail.last_error ?? "None"}</dd>
                  </div>
                </dl>

                {detail.can_retry ? (
                  <button
                    className="primary-button"
                    type="button"
                    disabled={pendingRetryId === detail.id}
                    onClick={() => void retryOne(detail.id)}
                  >
                    {pendingRetryId === detail.id ? "Retrying…" : "Retry delivery"}
                  </button>
                ) : null}

                <section>
                  <h3>Attempt history</h3>
                  {detail.attempts.length === 0 ? (
                    <p>No attempt rows recorded yet.</p>
                  ) : (
                    <ul className="timeline-list">
                      {detail.attempts.map((attempt) => (
                        <li key={attempt.id}>
                          <strong>
                            Attempt {attempt.attempt_number}: {attempt.status}
                          </strong>
                          <div>{formatDate(attempt.attempted_at)}</div>
                          {attempt.telegram_message_id ? <div>Message ID {attempt.telegram_message_id}</div> : null}
                          {attempt.error_message ? <div>{attempt.error_message}</div> : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section>
                  <h3>Timeline</h3>
                  <ul className="timeline-list">
                    {detail.timeline.map((event, index) => (
                      <li key={`${event.event}-${index}`}>
                        <strong>{event.event}</strong>
                        <div>{formatDate(event.at)}</div>
                        {event.detail ? <div>{event.detail}</div> : null}
                      </li>
                    ))}
                  </ul>
                </section>
              </div>
            )}
          </aside>
        </div>
      ) : null}
    </div>
  );
}
