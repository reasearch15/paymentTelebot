"use client";

import { FormEvent, MouseEvent, useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";

type PlayerLedgerRow = {
  sender_name: string;
  total_in_cents: number;
  total_out_cents: number;
  settlements_paid_cents: number;
  settlements_received_cents: number;
  unsettled_balance_cents: number;
  in_count: number;
  out_count: number;
  first_transaction_at: string | null;
  latest_transaction_at: string | null;
  latest_activity_at: string | null;
};

type PlayerLedgerListResponse = {
  items: PlayerLedgerRow[];
};

type PlayerLedgerTransaction = {
  id: number;
  account_name: string;
  direction: string;
  amount_cents: number;
  provider_reference: string | null;
  received_at: string;
  telegram_status: string;
};

type PlayerSettlement = {
  id: number;
  sender_name: string;
  direction: string;
  amount_cents: number;
  account_name: string;
  reference: string | null;
  note: string | null;
  settled_at: string;
  created_by_user_id: string;
};

type PlayerLedgerDetailResponse = {
  summary: PlayerLedgerRow;
  transactions: PlayerLedgerTransaction[];
  settlements: PlayerSettlement[];
};

type PaymentAccount = {
  id: number;
  friendly_name: string;
  enabled: boolean;
};

type SettlementDirection = "PAID_TO_PLAYER" | "RECEIVED_FROM_PLAYER";

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

function toDatetimeLocalValue(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function defaultDirectionForBalance(unsettledBalanceCents: number): SettlementDirection {
  return unsettledBalanceCents < 0 ? "RECEIVED_FROM_PLAYER" : "PAID_TO_PLAYER";
}

function parseAmountToCents(raw: string): number | null {
  const cleaned = raw.trim().replace(/\$/g, "").replace(/,/g, "");
  if (!cleaned) {
    return null;
  }
  const amount = Number(cleaned);
  if (!Number.isFinite(amount) || amount <= 0) {
    return null;
  }
  return Math.round(amount * 100);
}

export function PlayerLedgerBrowser() {
  const [rows, setRows] = useState<PlayerLedgerRow[]>([]);
  const [searchInput, setSearchInput] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedSender, setSelectedSender] = useState<string | null>(null);
  const [detail, setDetail] = useState<PlayerLedgerDetailResponse | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [isDetailLoading, setIsDetailLoading] = useState(false);

  const [accounts, setAccounts] = useState<PaymentAccount[]>([]);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [settleSenderName, setSettleSenderName] = useState("");
  const [settleUnsettledCents, setSettleUnsettledCents] = useState(0);
  const [settleDirection, setSettleDirection] = useState<SettlementDirection>("PAID_TO_PLAYER");
  const [settleAmount, setSettleAmount] = useState("");
  const [settleAccountId, setSettleAccountId] = useState("");
  const [settleReference, setSettleReference] = useState("");
  const [settleNote, setSettleNote] = useState("");
  const [settleAt, setSettleAt] = useState(toDatetimeLocalValue(new Date()));
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadRows = useCallback(async (search: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (search.trim()) {
        params.set("search", search.trim());
      }
      const query = params.toString();
      const data = await apiRequest<PlayerLedgerListResponse>(
        `/player-ledger${query ? `?${query}` : ""}`
      );
      setRows(data.items);
      return data.items;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load player ledger");
      return null;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      const data = await apiRequest<PaymentAccount[]>("/payment-accounts");
      setAccounts(data);
      setSettleAccountId((current) => {
        if (current) {
          return current;
        }
        return data.length === 1 ? String(data[0].id) : current;
      });
    } catch {
      // Account options are loaded when opening the modal.
    }
  }, []);

  useEffect(() => {
    void loadRows(appliedSearch);
  }, [appliedSearch, loadRows]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const loadDetail = useCallback(async (senderName: string) => {
    setSelectedSender(senderName);
    setIsDetailLoading(true);
    setDetailError(null);
    try {
      const params = new URLSearchParams({ sender_name: senderName });
      const data = await apiRequest<PlayerLedgerDetailResponse>(`/player-ledger/detail?${params.toString()}`);
      setDetail(data);
      return data;
    } catch (caught) {
      setDetail(null);
      setDetailError(caught instanceof Error ? caught.message : "Unable to load player detail");
      return null;
    } finally {
      setIsDetailLoading(false);
    }
  }, []);

  const openSettleModal = useCallback(
    async (row: PlayerLedgerRow, options?: { openDetail?: boolean }) => {
      setSuccessMessage(null);
      setFormError(null);
      setSettleSenderName(row.sender_name);
      setSettleUnsettledCents(row.unsettled_balance_cents);
      setSettleDirection(defaultDirectionForBalance(row.unsettled_balance_cents));
      setSettleAmount("");
      setSettleReference("");
      setSettleNote("");
      setSettleAt(toDatetimeLocalValue(new Date()));
      if (!settleAccountId && accounts.length === 1) {
        setSettleAccountId(String(accounts[0].id));
      }
      if (accounts.length === 0) {
        await loadAccounts();
      }
      if (options?.openDetail) {
        void loadDetail(row.sender_name);
      }
      setIsModalOpen(true);
    },
    [accounts, loadAccounts, loadDetail, settleAccountId]
  );

  function closeSettleModal() {
    if (isSubmitting) {
      return;
    }
    setIsModalOpen(false);
    setFormError(null);
  }

  async function submitPlayerSettlement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    setFormError(null);

    if (!settleSenderName.trim()) {
      setFormError("Player name is required.");
      return;
    }
    if (!settleAccountId) {
      setFormError("Select an account.");
      return;
    }
    const amountCents = parseAmountToCents(settleAmount);
    if (amountCents == null) {
      setFormError("Enter a valid settlement amount greater than zero.");
      return;
    }
    if (settleDirection === "PAID_TO_PLAYER") {
      if (settleUnsettledCents <= 0) {
        setFormError("Player has no positive unsettled balance to pay.");
        return;
      }
      if (amountCents > settleUnsettledCents) {
        setFormError(
          `Settlement amount exceeds unsettled balance of ${formatMoney(settleUnsettledCents)}.`
        );
        return;
      }
    }

    setIsSubmitting(true);
    try {
      const created = await apiRequest<PlayerSettlement>("/player-settlements", {
        method: "POST",
        body: JSON.stringify({
          sender_name: settleSenderName,
          direction: settleDirection,
          amount: settleAmount.trim(),
          payment_account_id: Number(settleAccountId),
          reference: settleReference.trim() || null,
          note: settleNote.trim() || null,
          settled_at: settleAt ? new Date(settleAt).toISOString() : null,
        }),
      });

      setIsModalOpen(false);
      setSuccessMessage(
        `Saved ${created.direction.replaceAll("_", " ")} ${formatMoney(created.amount_cents)} for ${created.sender_name}.`
      );

      const refreshedRows = await loadRows(appliedSearch);
      const refreshedRow = refreshedRows?.find((row) => row.sender_name === created.sender_name);
      if (refreshedRow) {
        setSettleUnsettledCents(refreshedRow.unsettled_balance_cents);
      }

      if (selectedSender === created.sender_name || settleSenderName === created.sender_name) {
        await loadDetail(created.sender_name);
      }
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : "Unable to save player settlement");
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleRowSettleClick(event: MouseEvent<HTMLButtonElement>, row: PlayerLedgerRow) {
    event.preventDefault();
    event.stopPropagation();
    void openSettleModal(row);
  }

  const summaryCards = useMemo(() => {
    if (!detail) {
      return [];
    }
    const summary = detail.summary;
    return [
      {
        label: "Current Unsettled Balance",
        value: formatMoney(summary.unsettled_balance_cents),
        note: "IN − OUT − paid + received",
      },
      { label: "Total IN", value: formatMoney(summary.total_in_cents), note: `${summary.in_count} IN tx` },
      { label: "Total OUT", value: formatMoney(summary.total_out_cents), note: `${summary.out_count} OUT tx` },
      {
        label: "Settlements Paid",
        value: formatMoney(summary.settlements_paid_cents),
        note: "PAID_TO_PLAYER",
      },
      {
        label: "Settlements Received",
        value: formatMoney(summary.settlements_received_cents),
        note: "RECEIVED_FROM_PLAYER",
      },
    ];
  }, [detail]);

  const detailUnsettled = detail?.summary.unsettled_balance_cents ?? settleUnsettledCents;

  return (
    <div className="integrations-stack">
      {error ? <div className="alert-message">{error}</div> : null}
      {successMessage ? <div className="inline-result">{successMessage}</div> : null}

      <div className="section-heading">
        <div>
          <h2>Players</h2>
          <p>One row per exact sender identity. Status values do not affect balances.</p>
        </div>
        <form
          className="inline-filter-row"
          onSubmit={(event) => {
            event.preventDefault();
            setAppliedSearch(searchInput.trim());
          }}
        >
          <input
            aria-label="Search player name"
            placeholder="Search sender/player name"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
          />
          <button className="secondary-button" type="submit">
            Search
          </button>
        </form>
      </div>

      <section className="table-shell">
        <table className="placeholder-table">
          <thead>
            <tr>
              <th>Player</th>
              <th>Total IN</th>
              <th>Total OUT</th>
              <th>Settlements Paid</th>
              <th>Settlements Received</th>
              <th>Unsettled Balance</th>
              <th>IN Count</th>
              <th>OUT Count</th>
              <th>First Tx</th>
              <th>Latest Tx</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={11}>Loading player ledger...</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={11}>No players found.</td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.sender_name}>
                  <td>
                    <button
                      className="link-button"
                      type="button"
                      onClick={() => void loadDetail(row.sender_name)}
                    >
                      {row.sender_name}
                    </button>
                  </td>
                  <td>{formatMoney(row.total_in_cents)}</td>
                  <td>{formatMoney(row.total_out_cents)}</td>
                  <td>{formatMoney(row.settlements_paid_cents)}</td>
                  <td>{formatMoney(row.settlements_received_cents)}</td>
                  <td>{formatMoney(row.unsettled_balance_cents)}</td>
                  <td>{row.in_count}</td>
                  <td>{row.out_count}</td>
                  <td>{formatDate(row.first_transaction_at)}</td>
                  <td>{formatDate(row.latest_transaction_at)}</td>
                  <td>
                    <button
                      className="secondary-button compact-button"
                      type="button"
                      onClick={(event) => handleRowSettleClick(event, row)}
                    >
                      Settle
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>

      {selectedSender ? (
        <section className="detail-panel" aria-label="Player detail">
          <div className="section-heading">
            <div>
              <h2>{selectedSender}</h2>
              <p>Player ledger detail. Positive unsettled means money is still owed to the player.</p>
            </div>
            <div className="inline-filter-row">
              <button
                className="primary-button"
                type="button"
                onClick={() => {
                  const row =
                    detail?.summary ??
                    rows.find((item) => item.sender_name === selectedSender) ??
                    null;
                  if (!row) {
                    setFormError("Unable to load player balance for settlement.");
                    return;
                  }
                  void openSettleModal(row);
                }}
                disabled={isDetailLoading || !detail}
              >
                Settle Player
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => {
                  setSelectedSender(null);
                  setDetail(null);
                  setDetailError(null);
                }}
              >
                Close
              </button>
            </div>
          </div>

          {detailError ? <div className="alert-message">{detailError}</div> : null}
          {isDetailLoading ? <p>Loading player detail...</p> : null}

          {!isDetailLoading && detail ? (
            <>
              <section className="metric-grid ledger-totals" aria-label="Player totals">
                {summaryCards.map((metric) => (
                  <article className="metric-card" key={metric.label}>
                    <p className="metric-label">{metric.label}</p>
                    <p className="metric-value">{metric.value}</p>
                    <p className="metric-note">{metric.note}</p>
                  </article>
                ))}
              </section>

              <div className="section-heading">
                <div>
                  <h2>IN / OUT History</h2>
                </div>
              </div>
              <section className="table-shell">
                <table className="placeholder-table">
                  <thead>
                    <tr>
                      <th>Date/Time</th>
                      <th>Account</th>
                      <th>Direction</th>
                      <th>Amount</th>
                      <th>Reference</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.transactions.length === 0 ? (
                      <tr>
                        <td colSpan={6}>No transactions for this player.</td>
                      </tr>
                    ) : (
                      detail.transactions.map((tx) => (
                        <tr key={tx.id}>
                          <td>{formatDate(tx.received_at)}</td>
                          <td>{tx.account_name}</td>
                          <td>{tx.direction}</td>
                          <td>{formatMoney(tx.amount_cents)}</td>
                          <td>{tx.provider_reference ?? "—"}</td>
                          <td>{tx.telegram_status}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </section>

              <div className="section-heading">
                <div>
                  <h2>Settlement History</h2>
                </div>
              </div>
              <section className="table-shell">
                <table className="placeholder-table">
                  <thead>
                    <tr>
                      <th>Date/Time</th>
                      <th>Direction</th>
                      <th>Amount</th>
                      <th>Account</th>
                      <th>Reference</th>
                      <th>Created by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.settlements.length === 0 ? (
                      <tr>
                        <td colSpan={6}>No player settlements recorded.</td>
                      </tr>
                    ) : (
                      detail.settlements.map((settlement) => (
                        <tr key={settlement.id}>
                          <td>{formatDate(settlement.settled_at)}</td>
                          <td>{settlement.direction}</td>
                          <td>{formatMoney(settlement.amount_cents)}</td>
                          <td>{settlement.account_name}</td>
                          <td>{settlement.reference ?? settlement.note ?? "—"}</td>
                          <td>{settlement.created_by_user_id}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </section>
            </>
          ) : null}
        </section>
      ) : null}

      {isModalOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="modal-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="player-settlement-title"
          >
            <div className="email-detail-header">
              <div>
                <h2 id="player-settlement-title">Settle Player</h2>
                <p>Record a sender-level settlement without changing original IN/OUT transactions.</p>
              </div>
              <button
                className="secondary-button"
                type="button"
                onClick={closeSettleModal}
                disabled={isSubmitting}
              >
                Cancel
              </button>
            </div>

            <form className="form-stack" onSubmit={(event) => void submitPlayerSettlement(event)}>
              <div className="field">
                <label htmlFor="player-settle-name">Player</label>
                <input id="player-settle-name" value={settleSenderName} readOnly />
              </div>

              <div className="field">
                <label htmlFor="player-settle-balance">Current unsettled balance</label>
                <input
                  id="player-settle-balance"
                  value={formatMoney(isModalOpen ? settleUnsettledCents : detailUnsettled)}
                  readOnly
                />
              </div>

              <div className="field">
                <label htmlFor="player-settle-direction">Direction</label>
                <select
                  id="player-settle-direction"
                  value={settleDirection}
                  onChange={(event) => setSettleDirection(event.target.value as SettlementDirection)}
                  disabled={isSubmitting}
                >
                  <option value="PAID_TO_PLAYER">PAID_TO_PLAYER</option>
                  <option value="RECEIVED_FROM_PLAYER">RECEIVED_FROM_PLAYER</option>
                </select>
              </div>

              <div className="field">
                <label htmlFor="player-settle-amount">Amount</label>
                <input
                  id="player-settle-amount"
                  value={settleAmount}
                  onChange={(event) => setSettleAmount(event.target.value)}
                  placeholder="10.00"
                  inputMode="decimal"
                  autoComplete="off"
                  disabled={isSubmitting}
                  required
                />
              </div>

              <div className="field">
                <label htmlFor="player-settle-account">Account</label>
                <select
                  id="player-settle-account"
                  value={settleAccountId}
                  onChange={(event) => setSettleAccountId(event.target.value)}
                  disabled={isSubmitting}
                  required
                >
                  <option value="">Select account</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.friendly_name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label htmlFor="player-settle-reference">Reference</label>
                <input
                  id="player-settle-reference"
                  value={settleReference}
                  onChange={(event) => setSettleReference(event.target.value)}
                  disabled={isSubmitting}
                />
              </div>

              <div className="field">
                <label htmlFor="player-settle-note">Note</label>
                <input
                  id="player-settle-note"
                  value={settleNote}
                  onChange={(event) => setSettleNote(event.target.value)}
                  disabled={isSubmitting}
                />
              </div>

              <div className="field">
                <label htmlFor="player-settle-at">Settlement date/time</label>
                <input
                  id="player-settle-at"
                  type="datetime-local"
                  value={settleAt}
                  onChange={(event) => setSettleAt(event.target.value)}
                  disabled={isSubmitting}
                />
              </div>

              {formError ? <div className="alert-message">{formError}</div> : null}

              <div className="modal-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={closeSettleModal}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={isSubmitting}>
                  {isSubmitting ? "Saving..." : "Save Settlement"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}
