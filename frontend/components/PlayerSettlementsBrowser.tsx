"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "@/lib/api";

const PAGE_SIZE = 30;

type PaymentAccount = {
  id: number;
  friendly_name: string;
  enabled: boolean;
};

type PlayerSettlement = {
  id: number;
  sender_name: string;
  direction: "PAID_TO_PLAYER" | "RECEIVED_FROM_PLAYER";
  amount_cents: number;
  account_name: string;
  reference: string | null;
  note: string | null;
  settled_at: string;
  created_by_user_id: string;
};

type PlayerSettlementListResponse = {
  items: PlayerSettlement[];
  next_cursor: string | null;
  has_more: boolean;
};

type PlayerSenderListResponse = {
  items: string[];
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

function mergeById(existing: PlayerSettlement[], incoming: PlayerSettlement[]) {
  if (incoming.length === 0) {
    return existing;
  }
  const seen = new Set(existing.map((row) => row.id));
  const appended = incoming.filter((row) => !seen.has(row.id));
  return appended.length === 0 ? existing : [...existing, ...appended];
}

function toDatetimeLocalValue(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function PlayerSettlementsBrowser() {
  const [settlements, setSettlements] = useState<PlayerSettlement[]>([]);
  const [senders, setSenders] = useState<string[]>([]);
  const [accounts, setAccounts] = useState<PaymentAccount[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [filterPlayer, setFilterPlayer] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  const [filterFrom, setFilterFrom] = useState("");
  const [filterTo, setFilterTo] = useState("");
  const [appliedFilters, setAppliedFilters] = useState({
    player: "",
    direction: "",
    from: "",
    to: "",
  });

  const [senderName, setSenderName] = useState("");
  const [direction, setDirection] = useState<"PAID_TO_PLAYER" | "RECEIVED_FROM_PLAYER">("PAID_TO_PLAYER");
  const [amountInput, setAmountInput] = useState("");
  const [accountId, setAccountId] = useState("");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [settledAt, setSettledAt] = useState(toDatetimeLocalValue(new Date()));

  const loadingMoreRef = useRef(false);

  const buildQuery = useCallback(
    (cursor?: string | null) => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (cursor) {
        params.set("cursor", cursor);
      }
      if (appliedFilters.player.trim()) {
        params.set("sender_name", appliedFilters.player.trim());
      }
      if (appliedFilters.direction) {
        params.set("direction", appliedFilters.direction);
      }
      if (appliedFilters.from) {
        params.set("settled_from", new Date(appliedFilters.from).toISOString());
      }
      if (appliedFilters.to) {
        params.set("settled_to", new Date(appliedFilters.to).toISOString());
      }
      return `/player-settlements?${params.toString()}`;
    },
    [appliedFilters]
  );

  const loadSettlements = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setLoadMoreError(null);
    try {
      const data = await apiRequest<PlayerSettlementListResponse>(buildQuery());
      setSettlements(data.items);
      setNextCursor(data.next_cursor);
      setHasMore(data.has_more);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load player settlements");
    } finally {
      setIsLoading(false);
    }
  }, [buildQuery]);

  const loadFormOptions = useCallback(async () => {
    try {
      const [senderData, accountData] = await Promise.all([
        apiRequest<PlayerSenderListResponse>("/player-ledger/senders"),
        apiRequest<PaymentAccount[]>("/payment-accounts"),
      ]);
      setSenders(senderData.items);
      setAccounts(accountData);
      if (!accountId && accountData.length === 1) {
        setAccountId(String(accountData[0].id));
      }
    } catch {
      // Form options are best-effort; create can still use typed player names.
    }
  }, [accountId]);

  useEffect(() => {
    void loadSettlements();
  }, [loadSettlements]);

  useEffect(() => {
    void loadFormOptions();
  }, [loadFormOptions]);

  async function loadMore() {
    if (!hasMore || !nextCursor || loadingMoreRef.current || isLoading) {
      return;
    }
    loadingMoreRef.current = true;
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const data = await apiRequest<PlayerSettlementListResponse>(buildQuery(nextCursor));
      setSettlements((current) => mergeById(current, data.items));
      setNextCursor(data.next_cursor);
      setHasMore(data.has_more);
    } catch (caught) {
      setLoadMoreError(caught instanceof Error ? caught.message : "Unable to load more settlements");
    } finally {
      loadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }

  async function submitSettlement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    setFormError(null);
    setSuccessMessage(null);

    if (!senderName.trim()) {
      setFormError("Select or enter a player/sender.");
      return;
    }
    if (!accountId) {
      setFormError("Select an account.");
      return;
    }
    if (!amountInput.trim()) {
      setFormError("Enter an amount.");
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await apiRequest<PlayerSettlement>("/player-settlements", {
        method: "POST",
        body: JSON.stringify({
          sender_name: senderName.trim(),
          direction,
          amount: amountInput.trim(),
          payment_account_id: Number(accountId),
          reference: reference.trim() || null,
          note: note.trim() || null,
          settled_at: settledAt ? new Date(settledAt).toISOString() : null,
        }),
      });
      setSuccessMessage(
        `Recorded ${created.direction.replaceAll("_", " ").toLowerCase()} ${formatMoney(created.amount_cents)} for ${created.sender_name}.`
      );
      setAmountInput("");
      setReference("");
      setNote("");
      setSettledAt(toDatetimeLocalValue(new Date()));
      await loadSettlements();
      await loadFormOptions();
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : "Unable to create player settlement");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="integrations-stack">
      {error ? <div className="alert-message">{error}</div> : null}
      {successMessage ? <div className="inline-result">{successMessage}</div> : null}

      <section className="panel-card">
        <div className="section-heading">
          <div>
            <h2>Create Player Settlement</h2>
            <p>Records sender-level settlements without changing original payment transactions or global account settlements.</p>
          </div>
        </div>
        <form className="form-stack" onSubmit={(event) => void submitSettlement(event)}>
          <label className="field">
            <span>Player / sender</span>
            <input
              list="player-sender-options"
              value={senderName}
              onChange={(event) => setSenderName(event.target.value)}
              placeholder="Exact sender name"
              required
            />
            <datalist id="player-sender-options">
              {senders.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>

          <label className="field">
            <span>Direction</span>
            <select
              value={direction}
              onChange={(event) =>
                setDirection(event.target.value as "PAID_TO_PLAYER" | "RECEIVED_FROM_PLAYER")
              }
            >
              <option value="PAID_TO_PLAYER">PAID_TO_PLAYER</option>
              <option value="RECEIVED_FROM_PLAYER">RECEIVED_FROM_PLAYER</option>
            </select>
          </label>

          <label className="field">
            <span>Amount (USD)</span>
            <input
              value={amountInput}
              onChange={(event) => setAmountInput(event.target.value)}
              placeholder="200.00"
              required
            />
          </label>

          <label className="field">
            <span>Account</span>
            <select value={accountId} onChange={(event) => setAccountId(event.target.value)} required>
              <option value="">Select account</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.friendly_name}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Reference</span>
            <input value={reference} onChange={(event) => setReference(event.target.value)} />
          </label>

          <label className="field">
            <span>Note</span>
            <input value={note} onChange={(event) => setNote(event.target.value)} />
          </label>

          <label className="field">
            <span>Settlement date/time</span>
            <input
              type="datetime-local"
              value={settledAt}
              onChange={(event) => setSettledAt(event.target.value)}
            />
          </label>

          {formError ? <div className="alert-message">{formError}</div> : null}
          <button className="primary-button" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Saving…" : "Record settlement"}
          </button>
        </form>
      </section>

      <div className="section-heading">
        <div>
          <h2>Settlement History</h2>
          <p>Filter by player, direction, and date range.</p>
        </div>
      </div>

      <form
        className="inline-filter-row"
        onSubmit={(event) => {
          event.preventDefault();
          setAppliedFilters({
            player: filterPlayer.trim(),
            direction: filterDirection,
            from: filterFrom,
            to: filterTo,
          });
        }}
      >
        <input
          aria-label="Filter player name"
          placeholder="Player name"
          value={filterPlayer}
          onChange={(event) => setFilterPlayer(event.target.value)}
        />
        <select
          aria-label="Filter direction"
          value={filterDirection}
          onChange={(event) => setFilterDirection(event.target.value)}
        >
          <option value="">All directions</option>
          <option value="PAID_TO_PLAYER">PAID_TO_PLAYER</option>
          <option value="RECEIVED_FROM_PLAYER">RECEIVED_FROM_PLAYER</option>
        </select>
        <input
          aria-label="Filter from date"
          type="datetime-local"
          value={filterFrom}
          onChange={(event) => setFilterFrom(event.target.value)}
        />
        <input
          aria-label="Filter to date"
          type="datetime-local"
          value={filterTo}
          onChange={(event) => setFilterTo(event.target.value)}
        />
        <button className="secondary-button" type="submit">
          Apply filters
        </button>
      </form>

      <section className="table-shell">
        <table className="placeholder-table">
          <thead>
            <tr>
              <th>Date/Time</th>
              <th>Player</th>
              <th>Direction</th>
              <th>Amount</th>
              <th>Account</th>
              <th>Reference</th>
              <th>Created by</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={7}>Loading player settlements...</td>
              </tr>
            ) : settlements.length === 0 ? (
              <tr>
                <td colSpan={7}>No player settlements recorded.</td>
              </tr>
            ) : (
              settlements.map((settlement) => (
                <tr key={settlement.id}>
                  <td>{formatDate(settlement.settled_at)}</td>
                  <td>{settlement.sender_name}</td>
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
        {!isLoading && hasMore ? (
          <div className="load-more-row">
            {loadMoreError ? <div className="alert-message">{loadMoreError}</div> : null}
            <button
              className="secondary-button"
              type="button"
              onClick={() => void loadMore()}
              disabled={isLoadingMore}
            >
              {isLoadingMore ? "Loading…" : "Load more"}
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
