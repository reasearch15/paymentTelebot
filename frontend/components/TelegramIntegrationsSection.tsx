"use client";

import { FormEvent, useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";

export type TelegramIntegration = {
  id: number;
  name: string;
  bot_token_masked: string | null;
  has_bot_token: boolean;
  group_id: string | null;
  bot_username: string | null;
  enabled: boolean;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  assigned_payment_account_count: number;
  is_legacy_default?: boolean;
  delivery_stats?: {
    messages_today: number;
    sent_today: number;
    failed_today: number;
    pending: number;
    last_delivery_at: string | null;
    last_failure_at: string | null;
    last_failure_error: string | null;
    success_rate: number | null;
    average_attempts: number | null;
  } | null;
};

type PaymentAccountSummary = {
  id: number;
  friendly_name: string;
  gmail_address: string;
};

type TelegramFormState = {
  name: string;
  bot_token: string;
  group_id: string;
  enabled: boolean;
};

type TelegramActionResponse = {
  success: boolean;
  message: string;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  bot_username: string | null;
};

type AssignmentResponse = {
  telegram_integration_id: number;
  payment_accounts: Array<{
    id: number;
    friendly_name: string;
    gmail_address: string;
    provider_name: string;
    enabled: boolean;
  }>;
};

const emptyTelegramForm: TelegramFormState = {
  name: "",
  bot_token: "",
  group_id: "",
  enabled: true,
};

function formatDate(value: string | null) {
  if (!value) {
    return "Never";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message;
  }
  return "Something went wrong.";
}

type TelegramIntegrationsSectionProps = {
  integrations: TelegramIntegration[];
  accounts: PaymentAccountSummary[];
  isLoading: boolean;
  onRefresh: () => Promise<void>;
};

export function TelegramIntegrationsSection({
  integrations,
  accounts,
  isLoading,
  onRefresh,
}: TelegramIntegrationsSectionProps) {
  const [sectionError, setSectionError] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editingIntegration, setEditingIntegration] = useState<TelegramIntegration | null>(null);
  const [assigningIntegration, setAssigningIntegration] = useState<TelegramIntegration | null>(null);
  const [deletingIntegration, setDeletingIntegration] = useState<TelegramIntegration | null>(null);
  const [telegramForm, setTelegramForm] = useState<TelegramFormState>(emptyTelegramForm);
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedAccountIds, setSelectedAccountIds] = useState<number[]>([]);
  const [assignError, setAssignError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [actionResults, setActionResults] = useState<Record<number, string>>({});
  const [pendingIntegrationId, setPendingIntegrationId] = useState<number | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const sortedAccounts = useMemo(
    () => [...accounts].sort((a, b) => a.friendly_name.localeCompare(b.friendly_name)),
    [accounts]
  );

  function openCreateModal() {
    setTelegramForm(emptyTelegramForm);
    setFormError(null);
    setCreateModalOpen(true);
  }

  function openEditModal(integration: TelegramIntegration) {
    setEditingIntegration(integration);
    setTelegramForm({
      name: integration.name,
      bot_token: "",
      group_id: integration.group_id ?? "",
      enabled: integration.enabled,
    });
    setFormError(null);
  }

  async function openAssignModal(integration: TelegramIntegration) {
    setAssigningIntegration(integration);
    setAssignError(null);
    setPendingIntegrationId(integration.id);
    try {
      const data = await apiRequest<AssignmentResponse>(
        `/telegram-integrations/${integration.id}/payment-accounts`
      );
      setSelectedAccountIds(data.payment_accounts.map((account) => account.id));
    } catch (error) {
      setAssignError(errorMessage(error));
      setSelectedAccountIds([]);
    } finally {
      setPendingIntegrationId(null);
    }
  }

  function closeAssignModal() {
    if (isSubmitting) {
      return;
    }
    setAssigningIntegration(null);
    setSelectedAccountIds([]);
    setAssignError(null);
  }

  function toggleAccountSelection(accountId: number) {
    setSelectedAccountIds((current) =>
      current.includes(accountId) ? current.filter((id) => id !== accountId) : [...current, accountId]
    );
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setIsSubmitting(true);
    try {
      await apiRequest<TelegramIntegration>("/telegram-integrations", {
        method: "POST",
        body: JSON.stringify(telegramForm),
      });
      setCreateModalOpen(false);
      await onRefresh();
    } catch (error) {
      setFormError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingIntegration) {
      return;
    }
    setFormError(null);
    setIsSubmitting(true);

    const payload: Record<string, string | boolean> = {
      name: telegramForm.name,
      group_id: telegramForm.group_id,
      enabled: telegramForm.enabled,
    };
    if (telegramForm.bot_token.trim()) {
      payload.bot_token = telegramForm.bot_token;
    }

    try {
      await apiRequest<TelegramIntegration>(`/telegram-integrations/${editingIntegration.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      setEditingIntegration(null);
      await onRefresh();
    } catch (error) {
      setFormError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitAssignments(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!assigningIntegration) {
      return;
    }
    setAssignError(null);
    setIsSubmitting(true);
    try {
      await apiRequest<AssignmentResponse>(
        `/telegram-integrations/${assigningIntegration.id}/payment-accounts`,
        {
          method: "PUT",
          body: JSON.stringify({ payment_account_ids: selectedAccountIds }),
        }
      );
      setAssigningIntegration(null);
      setSelectedAccountIds([]);
      await onRefresh();
    } catch (error) {
      setAssignError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function confirmDelete() {
    if (!deletingIntegration) {
      return;
    }
    setDeleteError(null);
    setIsSubmitting(true);
    try {
      await apiRequest<void>(`/telegram-integrations/${deletingIntegration.id}`, {
        method: "DELETE",
      });
      setDeletingIntegration(null);
      await onRefresh();
    } catch (error) {
      setDeleteError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function setIntegrationEnabled(integration: TelegramIntegration, enabled: boolean) {
    setSectionError(null);
    setPendingIntegrationId(integration.id);
    try {
      await apiRequest<TelegramIntegration>(`/telegram-integrations/${integration.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      await onRefresh();
    } catch (error) {
      setSectionError(errorMessage(error));
    } finally {
      setPendingIntegrationId(null);
    }
  }

  async function runIntegrationAction(
    integration: TelegramIntegration,
    action: "test-connection" | "send-test-message"
  ) {
    setActionResults((current) => ({ ...current, [integration.id]: "Running..." }));
    setPendingIntegrationId(integration.id);
    try {
      const result = await apiRequest<TelegramActionResponse>(
        `/telegram-integrations/${integration.id}/${action}`,
        { method: "POST" }
      );
      setActionResults((current) => ({ ...current, [integration.id]: result.message }));
      await onRefresh();
    } catch (error) {
      setActionResults((current) => ({ ...current, [integration.id]: errorMessage(error) }));
    } finally {
      setPendingIntegrationId(null);
    }
  }

  function isIntegrationPending(integrationId: number) {
    return isSubmitting || pendingIntegrationId === integrationId;
  }

  return (
    <>
      <section className="management-section">
        <div className="section-heading">
          <div>
            <h2>Telegram Integrations</h2>
            <p>Manage multiple Telegram bots and assign them to Gmail accounts.</p>
          </div>
          <button className="primary-button" type="button" onClick={openCreateModal}>
            Add Telegram Integration
          </button>
        </div>

        {sectionError ? <div className="alert-message">{sectionError}</div> : null}

        {isLoading ? (
          <div className="loading-row">Loading Telegram integrations...</div>
        ) : integrations.length === 0 ? (
          <div className="empty-block">No Telegram integrations configured.</div>
        ) : (
          <div className="account-grid">
            {integrations.map((integration) => (
              <article className="account-card" key={integration.id}>
                <div className="account-card-header">
                  <div>
                    <h3>{integration.name}</h3>
                    {integration.bot_username ? (
                      <p>@{integration.bot_username}</p>
                    ) : null}
                  </div>
                  <div className="row-actions">
                    <span className={integration.enabled ? "status-badge enabled" : "status-badge disabled"}>
                      {integration.enabled ? "Enabled" : "Disabled"}
                    </span>
                    {integration.is_legacy_default ? (
                      <span className="status-badge enabled">Legacy default</span>
                    ) : null}
                  </div>
                </div>
                <dl className="account-details">
                  <div>
                    <dt>Group ID</dt>
                    <dd>{integration.group_id ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Bot token</dt>
                    <dd>{integration.bot_token_masked ?? (integration.has_bot_token ? "****" : "Not set")}</dd>
                  </div>
                  <div>
                    <dt>Assigned Gmail accounts</dt>
                    <dd>{integration.assigned_payment_account_count}</dd>
                  </div>
                  <div>
                    <dt>Messages today</dt>
                    <dd>{integration.delivery_stats?.messages_today ?? 0}</dd>
                  </div>
                  <div>
                    <dt>Sent today</dt>
                    <dd>{integration.delivery_stats?.sent_today ?? 0}</dd>
                  </div>
                  <div>
                    <dt>Failed today</dt>
                    <dd>{integration.delivery_stats?.failed_today ?? 0}</dd>
                  </div>
                  <div>
                    <dt>Pending</dt>
                    <dd>{integration.delivery_stats?.pending ?? 0}</dd>
                  </div>
                  <div>
                    <dt>Success rate</dt>
                    <dd>
                      {integration.delivery_stats?.success_rate != null
                        ? `${integration.delivery_stats.success_rate}%`
                        : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt>Average attempts</dt>
                    <dd>{integration.delivery_stats?.average_attempts ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Last delivery</dt>
                    <dd>{formatDate(integration.delivery_stats?.last_delivery_at ?? null)}</dd>
                  </div>
                  <div>
                    <dt>Last failure</dt>
                    <dd>
                      {formatDate(integration.delivery_stats?.last_failure_at ?? null)}
                      {integration.delivery_stats?.last_failure_error
                        ? ` · ${integration.delivery_stats.last_failure_error}`
                        : ""}
                    </dd>
                  </div>
                  <div>
                    <dt>Last checked</dt>
                    <dd>{formatDate(integration.last_checked_at)}</dd>
                  </div>
                  <div>
                    <dt>Last success</dt>
                    <dd>{formatDate(integration.last_success_at)}</dd>
                  </div>
                  <div>
                    <dt>Last error</dt>
                    <dd>{integration.last_error ?? "None"}</dd>
                  </div>
                </dl>
                {actionResults[integration.id] ? (
                  <div className="inline-result">{actionResults[integration.id]}</div>
                ) : null}
                <div className="row-actions">
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => openEditModal(integration)}
                    disabled={isIntegrationPending(integration.id)}
                  >
                    Edit
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => void openAssignModal(integration)}
                    disabled={isIntegrationPending(integration.id)}
                  >
                    Assign Gmail
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => void runIntegrationAction(integration, "test-connection")}
                    disabled={isIntegrationPending(integration.id)}
                  >
                    Test Connection
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => void runIntegrationAction(integration, "send-test-message")}
                    disabled={isIntegrationPending(integration.id)}
                  >
                    Send Test Message
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => void setIntegrationEnabled(integration, !integration.enabled)}
                    disabled={isIntegrationPending(integration.id)}
                  >
                    {integration.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => {
                      setDeleteError(null);
                      setDeletingIntegration(integration);
                    }}
                    disabled={isIntegrationPending(integration.id)}
                  >
                    Delete
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {createModalOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel wide" role="dialog" aria-modal="true" aria-labelledby="telegram-create-title">
            <h2 id="telegram-create-title">Add Telegram Integration</h2>
            <form className="form-stack" onSubmit={submitCreate}>
              <div className="field">
                <label htmlFor="telegram-create-name">Name</label>
                <input
                  id="telegram-create-name"
                  value={telegramForm.name}
                  onChange={(event) => setTelegramForm((current) => ({ ...current, name: event.target.value }))}
                  required
                  disabled={isSubmitting}
                />
              </div>
              <div className="field">
                <label htmlFor="telegram-create-token">Bot Token</label>
                <input
                  id="telegram-create-token"
                  type="password"
                  value={telegramForm.bot_token}
                  onChange={(event) => setTelegramForm((current) => ({ ...current, bot_token: event.target.value }))}
                  required
                  autoComplete="new-password"
                  disabled={isSubmitting}
                />
              </div>
              <div className="field">
                <label htmlFor="telegram-create-group-id">Group ID</label>
                <input
                  id="telegram-create-group-id"
                  value={telegramForm.group_id}
                  onChange={(event) => setTelegramForm((current) => ({ ...current, group_id: event.target.value }))}
                  required
                  disabled={isSubmitting}
                />
              </div>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={telegramForm.enabled}
                  onChange={(event) => setTelegramForm((current) => ({ ...current, enabled: event.target.checked }))}
                  disabled={isSubmitting}
                />
                Enabled
              </label>
              {formError ? <p className="error-message">{formError}</p> : null}
              <div className="modal-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setCreateModalOpen(false)}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={isSubmitting}>
                  Create Integration
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      {editingIntegration ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel wide" role="dialog" aria-modal="true" aria-labelledby="telegram-edit-title">
            <h2 id="telegram-edit-title">Edit Telegram Integration</h2>
            <form className="form-stack" onSubmit={submitEdit}>
              <div className="field">
                <label htmlFor="telegram-edit-name">Name</label>
                <input
                  id="telegram-edit-name"
                  value={telegramForm.name}
                  onChange={(event) => setTelegramForm((current) => ({ ...current, name: event.target.value }))}
                  required
                  disabled={isSubmitting}
                />
              </div>
              <div className="field">
                <label htmlFor="telegram-edit-token">Bot Token</label>
                <input
                  id="telegram-edit-token"
                  type="password"
                  value={telegramForm.bot_token}
                  onChange={(event) => setTelegramForm((current) => ({ ...current, bot_token: event.target.value }))}
                  placeholder={editingIntegration.bot_token_masked ?? ""}
                  autoComplete="new-password"
                  disabled={isSubmitting}
                />
                <p className="helper-text">Leave blank to keep the saved token.</p>
              </div>
              <div className="field">
                <label htmlFor="telegram-edit-group-id">Group ID</label>
                <input
                  id="telegram-edit-group-id"
                  value={telegramForm.group_id}
                  onChange={(event) => setTelegramForm((current) => ({ ...current, group_id: event.target.value }))}
                  required
                  disabled={isSubmitting}
                />
              </div>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={telegramForm.enabled}
                  onChange={(event) => setTelegramForm((current) => ({ ...current, enabled: event.target.checked }))}
                  disabled={isSubmitting}
                />
                Enabled
              </label>
              {formError ? <p className="error-message">{formError}</p> : null}
              <div className="modal-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setEditingIntegration(null)}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={isSubmitting}>
                  Save Changes
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      {assigningIntegration ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel wide" role="dialog" aria-modal="true" aria-labelledby="telegram-assign-title">
            <h2 id="telegram-assign-title">Assign Gmail Accounts — {assigningIntegration.name}</h2>
            <form className="form-stack" onSubmit={submitAssignments}>
              {sortedAccounts.length === 0 ? (
                <p className="helper-text">No Gmail accounts available.</p>
              ) : (
                sortedAccounts.map((account) => (
                  <label className="toggle-row" key={account.id}>
                    <input
                      type="checkbox"
                      checked={selectedAccountIds.includes(account.id)}
                      onChange={() => toggleAccountSelection(account.id)}
                      disabled={isSubmitting || pendingIntegrationId === assigningIntegration.id}
                    />
                    <span>
                      {account.friendly_name} ({account.gmail_address})
                    </span>
                  </label>
                ))
              )}
              {assignError ? <p className="error-message">{assignError}</p> : null}
              <div className="modal-actions">
                <button className="secondary-button" type="button" onClick={closeAssignModal} disabled={isSubmitting}>
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={isSubmitting}>
                  Save Assignments
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      {deletingIntegration ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="telegram-delete-title">
            <h2 id="telegram-delete-title">Delete Telegram Integration</h2>
            <p>
              Delete <strong>{deletingIntegration.name}</strong>? This cannot be undone. Integrations with assigned
              accounts or delivery history cannot be deleted.
            </p>
            {deleteError ? <p className="error-message">{deleteError}</p> : null}
            <div className="modal-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => setDeletingIntegration(null)}
                disabled={isSubmitting}
              >
                Cancel
              </button>
              <button className="primary-button" type="button" onClick={() => void confirmDelete()} disabled={isSubmitting}>
                Delete
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
