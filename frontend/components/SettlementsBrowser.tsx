"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "@/lib/api";

const PAGE_SIZE = 30;

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

type SettlementListResponse = {
  items: Settlement[];
  limit: number;
  next_cursor: string | null;
  has_more: boolean;
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

function mergeById(existing: Settlement[], incoming: Settlement[]): Settlement[] {
  if (incoming.length === 0) {
    return existing;
  }
  const seen = new Set(existing.map((row) => row.id));
  const appended = incoming.filter((row) => !seen.has(row.id));
  return appended.length === 0 ? existing : [...existing, ...appended];
}

function buildSettlementsQuery(cursor?: string | null) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return `/settlements?${params.toString()}`;
}

export function SettlementsBrowser() {
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const loadingMoreRef = useRef(false);
  const cancelledRef = useRef(false);

  const loadSettlements = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setLoadMoreError(null);
    try {
      const data = await apiRequest<SettlementListResponse>(buildSettlementsQuery());
      if (!cancelledRef.current) {
        setSettlements(data.items);
        setNextCursor(data.next_cursor);
        setHasMore(data.has_more);
      }
    } catch (caught) {
      if (!cancelledRef.current) {
        setError(caught instanceof Error ? caught.message : "Unable to load settlements");
      }
    } finally {
      if (!cancelledRef.current) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void loadSettlements();
    return () => {
      cancelledRef.current = true;
    };
  }, [loadSettlements]);

  async function loadMoreSettlements() {
    if (!hasMore || !nextCursor || loadingMoreRef.current || isLoading) {
      return;
    }
    loadingMoreRef.current = true;
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const data = await apiRequest<SettlementListResponse>(buildSettlementsQuery(nextCursor));
      if (!cancelledRef.current) {
        setSettlements((current) => mergeById(current, data.items));
        setNextCursor(data.next_cursor);
        setHasMore(data.has_more);
      }
    } catch (caught) {
      if (!cancelledRef.current) {
        setLoadMoreError(caught instanceof Error ? caught.message : "Unable to load more settlements");
      }
    } finally {
      loadingMoreRef.current = false;
      if (!cancelledRef.current) {
        setIsLoadingMore(false);
      }
    }
  }

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
        {!isLoading && hasMore ? (
          <div className="load-more-row">
            {loadMoreError ? <div className="alert-message">{loadMoreError}</div> : null}
            <button
              className="secondary-button"
              type="button"
              onClick={() => void loadMoreSettlements()}
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
