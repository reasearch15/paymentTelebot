"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";

type Provider = {
  id: number;
  name: string;
};

type PaymentAccount = {
  id: number;
  provider_id: number;
  provider_name: string;
  friendly_name: string;
  receiver_tag: string | null;
};

type EmailSummary = {
  id: number;
  payment_account_id: number;
  provider_id: number;
  provider_name: string;
  friendly_name: string;
  receiver_tag: string | null;
  gmail_uid: number;
  gmail_message_id: string | null;
  sender_address: string | null;
  subject: string | null;
  received_at: string | null;
  processing_status: string;
  parser_key: string | null;
  parser_version: string | null;
  created_at: string;
};

type EmailDetail = EmailSummary & {
  mailbox: string;
  raw_text: string | null;
  raw_html: string | null;
  raw_headers_json: Record<string, string> | null;
  parsed_payload_json: ParserResult | null;
  parsed_at: string | null;
  processing_error: string | null;
  updated_at: string;
};

type ParserResult = {
  is_payment: boolean;
  direction: "IN" | "OUT" | null;
  amount_cents: number | null;
  sender_name: string | null;
  receiver_tag: string | null;
  payment_timestamp: string | null;
  provider_reference: string | null;
  confidence: number;
  missing_fields: string[];
  parser_key: string;
  parser_version: string;
  debug_evidence: Record<string, string>;
};

type ParserInspection = {
  normalized_subject: string;
  normalized_plain_text: string;
  visible_text_from_html: string;
  detected_monetary_amounts: number[];
  detected_date_time_candidates: string[];
  detected_sender_name_candidates: string[];
  gmail_message_id: string | null;
  provider_name: string;
  parser_key: string;
  parser_version: string;
  parsed_result: ParserResult | null;
};

type Filters = {
  payment_account_id: string;
  provider_id: string;
  processing_status: string;
  search: string;
};

const statuses = ["captured", "ignored", "pending_parse", "parsed", "failed"];

function formatDate(value: string | null) {
  if (!value) {
    return "Unknown";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function sanitizeHtml(html: string) {
  const parser = new DOMParser();
  const document = parser.parseFromString(html, "text/html");
  document.querySelectorAll("script, iframe, object, embed, link, meta").forEach((node) => node.remove());
  document.querySelectorAll("*").forEach((element) => {
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim().toLowerCase();
      if (name.startsWith("on") || name === "src" || name === "srcset" || value.startsWith("javascript:")) {
        element.removeAttribute(attribute.name);
      }
    }
  });
  return document.body.innerHTML;
}

function formatMoney(cents: number | null) {
  if (cents === null) {
    return "Missing";
  }
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(cents / 100);
}

export function EmailsBrowser() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [accounts, setAccounts] = useState<PaymentAccount[]>([]);
  const [emails, setEmails] = useState<EmailSummary[]>([]);
  const [selectedEmail, setSelectedEmail] = useState<EmailDetail | null>(null);
  const [inspection, setInspection] = useState<ParserInspection | null>(null);
  const [detailMessage, setDetailMessage] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({
    payment_account_id: "",
    provider_id: "",
    processing_status: "",
    search: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const sanitizedHtml = useMemo(
    () => (selectedEmail?.raw_html ? sanitizeHtml(selectedEmail.raw_html) : ""),
    [selectedEmail?.raw_html]
  );

  async function loadReferenceData() {
    const [providerData, accountData] = await Promise.all([
      apiRequest<Provider[]>("/providers"),
      apiRequest<PaymentAccount[]>("/payment-accounts"),
    ]);
    setProviders(providerData);
    setAccounts(accountData);
  }

  async function loadEmails(nextFilters = filters) {
    const params = new URLSearchParams();
    params.set("limit", "100");
    for (const [key, value] of Object.entries(nextFilters)) {
      if (value.trim()) {
        params.set(key, value.trim());
      }
    }
    setError(null);
    const data = await apiRequest<EmailSummary[]>(`/payment-emails?${params.toString()}`);
    setEmails(data);
  }

  useEffect(() => {
    Promise.all([loadReferenceData(), loadEmails()])
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Unable to load emails"))
      .finally(() => setIsLoading(false));
  }, []);

  async function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await loadEmails(filters);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load emails");
    }
  }

  async function openEmail(email: EmailSummary) {
    try {
      const detail = await apiRequest<EmailDetail>(`/payment-emails/${email.id}`);
      setSelectedEmail(detail);
      setInspection(null);
      setDetailMessage(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load email");
    }
  }

  async function loadInspection(emailId: number) {
    const data = await apiRequest<ParserInspection>(`/payment-emails/${emailId}/parser-inspection`);
    setInspection(data);
  }

  async function runParser(action: "parse" | "reparse") {
    if (!selectedEmail) {
      return;
    }
    setDetailMessage(null);
    try {
      await apiRequest(`/payment-emails/${selectedEmail.id}/${action}`, { method: "POST" });
      const detail = await apiRequest<EmailDetail>(`/payment-emails/${selectedEmail.id}`);
      setSelectedEmail(detail);
      await loadInspection(selectedEmail.id);
      await loadEmails(filters);
      setDetailMessage(action === "parse" ? "Parse completed." : "Reparse completed.");
    } catch (caught) {
      setDetailMessage(caught instanceof Error ? caught.message : "Parser request failed");
    }
  }

  async function showInspection() {
    if (!selectedEmail) {
      return;
    }
    try {
      await loadInspection(selectedEmail.id);
    } catch (caught) {
      setDetailMessage(caught instanceof Error ? caught.message : "Unable to load parser inspection");
    }
  }

  return (
    <div className="integrations-stack">
      {error ? <div className="alert-message">{error}</div> : null}

      <form className="filters-bar" onSubmit={submitFilters}>
        <select
          value={filters.payment_account_id}
          onChange={(event) => setFilters((current) => ({ ...current, payment_account_id: event.target.value }))}
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
          value={filters.provider_id}
          onChange={(event) => setFilters((current) => ({ ...current, provider_id: event.target.value }))}
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
          value={filters.processing_status}
          onChange={(event) => setFilters((current) => ({ ...current, processing_status: event.target.value }))}
          aria-label="Status"
        >
          <option value="">All statuses</option>
          {statuses.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <input
          value={filters.search}
          onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
          placeholder="Search sender, subject, or body"
        />
        <button className="primary-button" type="submit">
          Apply
        </button>
      </form>

      <section className="table-shell">
        <table className="placeholder-table">
          <thead>
            <tr>
              <th>Received</th>
              <th>Receiver</th>
              <th>Gmail</th>
              <th>Provider</th>
              <th>Sender</th>
              <th>Subject</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={7}>Loading emails...</td>
              </tr>
            ) : emails.length === 0 ? (
              <tr>
                <td colSpan={7}>No captured emails.</td>
              </tr>
            ) : (
              emails.map((email) => (
                <tr key={email.id} className="clickable-row" onClick={() => openEmail(email)}>
                  <td>{formatDate(email.received_at)}</td>
                  <td>{email.receiver_tag ?? "Unknown"}</td>
                  <td>{email.friendly_name}</td>
                  <td>{email.provider_name}</td>
                  <td>{email.sender_address ?? "Unknown"}</td>
                  <td>{email.subject ?? "(No subject)"}</td>
                  <td>{email.processing_status}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>

      {selectedEmail ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel email-detail-modal" role="dialog" aria-modal="true" aria-labelledby="email-detail-title">
            <div className="email-detail-header">
              <div>
                <h2 id="email-detail-title">{selectedEmail.subject ?? "(No subject)"}</h2>
                <p>{selectedEmail.sender_address ?? "Unknown sender"}</p>
              </div>
              <button className="secondary-button" type="button" onClick={() => setSelectedEmail(null)}>
                Close
              </button>
            </div>

            <div className="row-actions detail-actions">
              <button className="primary-button" type="button" onClick={() => runParser("parse")}>
                Parse
              </button>
              <button className="secondary-button" type="button" onClick={() => runParser("reparse")}>
                Reparse
              </button>
              <button className="secondary-button" type="button" onClick={showInspection}>
                Parser Inspection
              </button>
            </div>
            {detailMessage ? <div className="inline-result">{detailMessage}</div> : null}

            <dl className="account-details email-meta">
              <div>
                <dt>Gmail UID</dt>
                <dd>{selectedEmail.gmail_uid}</dd>
              </div>
              <div>
                <dt>Message-ID</dt>
                <dd>{selectedEmail.gmail_message_id ?? "Unavailable"}</dd>
              </div>
              <div>
                <dt>Captured</dt>
                <dd>{formatDate(selectedEmail.created_at)}</dd>
              </div>
              <div>
                <dt>Received</dt>
                <dd>{formatDate(selectedEmail.received_at)}</dd>
              </div>
            </dl>

            <section className="email-detail-section">
              <h3>Parsed Result</h3>
              {selectedEmail.parsed_payload_json ? (
                <div className="parser-result-grid">
                  <div>
                    <dt>Receiver Tag</dt>
                    <dd>{selectedEmail.parsed_payload_json.receiver_tag ?? selectedEmail.receiver_tag ?? "Unknown"}</dd>
                  </div>
                  <div>
                    <dt>Sender Name</dt>
                    <dd>{selectedEmail.parsed_payload_json.sender_name ?? "Missing"}</dd>
                  </div>
                  <div>
                    <dt>Amount</dt>
                    <dd>{formatMoney(selectedEmail.parsed_payload_json.amount_cents)}</dd>
                  </div>
                  <div>
                    <dt>Confidence</dt>
                    <dd>{selectedEmail.parsed_payload_json.confidence}</dd>
                  </div>
                  <div>
                    <dt>Parser Version</dt>
                    <dd>
                      {selectedEmail.parsed_payload_json.parser_key} {selectedEmail.parsed_payload_json.parser_version}
                    </dd>
                  </div>
                </div>
              ) : (
                <div className="empty-block">This email has not been parsed yet.</div>
              )}
            </section>

            {inspection ? (
              <section className="email-detail-section">
                <h3>Parser Inspection</h3>
                <div className="parser-result-grid">
                  <div>
                    <dt>Provider / Parser</dt>
                    <dd>
                      {inspection.provider_name} / {inspection.parser_key}
                    </dd>
                  </div>
                  <div>
                    <dt>Parser Version</dt>
                    <dd>{inspection.parser_version}</dd>
                  </div>
                  <div>
                    <dt>Detected Amounts</dt>
                    <dd>{inspection.detected_monetary_amounts.map((amount) => formatMoney(amount)).join(", ") || "None"}</dd>
                  </div>
                  <div>
                    <dt>Detected Timestamps</dt>
                    <dd>{inspection.detected_date_time_candidates.join(", ") || "None"}</dd>
                  </div>
                  <div>
                    <dt>Sender Name Candidates</dt>
                    <dd>{inspection.detected_sender_name_candidates.join(", ") || "None"}</dd>
                  </div>
                  <div>
                    <dt>Missing Fields</dt>
                    <dd>{inspection.parsed_result?.missing_fields.join(", ") || "None"}</dd>
                  </div>
                  <div>
                    <dt>Confidence</dt>
                    <dd>{inspection.parsed_result?.confidence ?? "Not parsed"}</dd>
                  </div>
                </div>
                <details className="inspection-text">
                  <summary>Normalized text used for inspection</summary>
                  <pre>{inspection.normalized_plain_text || inspection.visible_text_from_html || "No body text."}</pre>
                </details>
              </section>
            ) : null}

            <section className="email-detail-section">
              <h3>Headers</h3>
              <pre>{JSON.stringify(selectedEmail.raw_headers_json ?? {}, null, 2)}</pre>
            </section>
            <section className="email-detail-section">
              <h3>Plain Text</h3>
              <pre>{selectedEmail.raw_text ?? "No plain-text body captured."}</pre>
            </section>
            <section className="email-detail-section">
              <h3>HTML Preview</h3>
              <div className="html-preview" dangerouslySetInnerHTML={{ __html: sanitizedHtml || "No HTML body captured." }} />
            </section>
          </section>
        </div>
      ) : null}
    </div>
  );
}
