"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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

export function PlayerLedgerBrowser() {
  const [rows, setRows] = useState<PlayerLedgerRow[]>([]);
  const [searchInput, setSearchInput] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedSender, setSelectedSender] = useState<string | null>(null);
  const [detail, setDetail] = useState<PlayerLedgerDetailResponse | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [isDetailLoading, setIsDetailLoading] = useState(false);

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
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load player ledger");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRows(appliedSearch);
  }, [appliedSearch, loadRows]);

  const loadDetail = useCallback(async (senderName: string) => {
    setSelectedSender(senderName);
    setIsDetailLoading(true);
    setDetailError(null);
    try {
      const params = new URLSearchParams({ sender_name: senderName });
      const data = await apiRequest<PlayerLedgerDetailResponse>(`/player-ledger/detail?${params.toString()}`);
      setDetail(data);
    } catch (caught) {
      setDetail(null);
      setDetailError(caught instanceof Error ? caught.message : "Unable to load player detail");
    } finally {
      setIsDetailLoading(false);
    }
  }, []);

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

  return (
    <div className="integrations-stack">
      {error ? <div className="alert-message">{error}</div> : null}

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
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={10}>Loading player ledger...</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={10}>No players found.</td>
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
    </div>
  );
}
