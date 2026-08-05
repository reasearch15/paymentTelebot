"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";
import { TelegramIntegration, TelegramIntegrationsSection } from "@/components/TelegramIntegrationsSection";

type Provider = {
  id: number;
  name: string;
  parser_key: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

type PaymentAccountTelegramSummary = {
  id: number;
  name: string;
  enabled: boolean;
  bot_username: string | null;
  group_id: string | null;
};

type PaymentAccount = {
  id: number;
  provider_id: number;
  provider_name: string;
  friendly_name: string;
  receiver_tag: string | null;
  gmail_address: string;
  enabled: boolean;
  listener_status: string;
  last_checked_at: string | null;
  last_email_at: string | null;
  last_captured_email_at: string | null;
  has_app_password: boolean;
  created_at: string;
  updated_at: string;
  telegram_integrations: PaymentAccountTelegramSummary[];
  telegram_integration_count: number;
  telegram_integration_ids: number[];
};

type ProviderFormState = {
  name: string;
  parser_key: string;
  enabled: boolean;
};

type AccountFormState = {
  provider_id: string;
  friendly_name: string;
  gmail_address: string;
  app_password: string;
  telegram_integration_ids: number[];
};

const emptyProviderForm: ProviderFormState = {
  name: "",
  parser_key: "",
  enabled: true,
};

const emptyAccountForm: AccountFormState = {
  provider_id: "",
  friendly_name: "",
  gmail_address: "",
  app_password: "",
  telegram_integration_ids: [],
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

export function IntegrationsManager() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [accounts, setAccounts] = useState<PaymentAccount[]>([]);
  const [telegramIntegrations, setTelegramIntegrations] = useState<TelegramIntegration[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [providerModalOpen, setProviderModalOpen] = useState(false);
  const [accountModalOpen, setAccountModalOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<PaymentAccount | null>(null);
  const [providerForm, setProviderForm] = useState<ProviderFormState>(emptyProviderForm);
  const [accountForm, setAccountForm] = useState<AccountFormState>(emptyAccountForm);
  const [formError, setFormError] = useState<string | null>(null);
  const [connectionResults, setConnectionResults] = useState<Record<number, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  const enabledProviders = useMemo(() => providers.filter((provider) => provider.enabled), [providers]);

  const sortedTelegramIntegrations = useMemo(
    () =>
      [...telegramIntegrations].sort((a, b) => {
        if (a.enabled !== b.enabled) {
          return a.enabled ? -1 : 1;
        }
        return a.name.localeCompare(b.name);
      }),
    [telegramIntegrations]
  );

  async function loadData() {
    setPageError(null);
    const [providerData, accountData, integrationData] = await Promise.all([
      apiRequest<Provider[]>("/providers"),
      apiRequest<PaymentAccount[]>("/payment-accounts"),
      apiRequest<TelegramIntegration[]>("/telegram-integrations"),
    ]);
    setProviders(providerData);
    setAccounts(accountData);
    setTelegramIntegrations(integrationData);
  }

  useEffect(() => {
    loadData()
      .catch((error) => setPageError(errorMessage(error)))
      .finally(() => setIsLoading(false));
  }, []);

  function openCreateProvider() {
    setProviderForm(emptyProviderForm);
    setFormError(null);
    setProviderModalOpen(true);
  }

  function openCreateAccount() {
    setEditingAccount(null);
    setAccountForm({
      ...emptyAccountForm,
      provider_id: enabledProviders[0]?.id.toString() ?? providers[0]?.id.toString() ?? "",
    });
    setFormError(null);
    setAccountModalOpen(true);
  }

  function openEditAccount(account: PaymentAccount) {
    setEditingAccount(account);
    setAccountForm({
      provider_id: account.provider_id.toString(),
      friendly_name: account.friendly_name,
      gmail_address: account.gmail_address,
      app_password: "",
      telegram_integration_ids: [...account.telegram_integration_ids],
    });
    setFormError(null);
    setAccountModalOpen(true);
  }

  function toggleAccountTelegramIntegration(integrationId: number) {
    setAccountForm((current) => ({
      ...current,
      telegram_integration_ids: current.telegram_integration_ids.includes(integrationId)
        ? current.telegram_integration_ids.filter((id) => id !== integrationId)
        : [...current.telegram_integration_ids, integrationId],
    }));
  }

  async function submitProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setIsSubmitting(true);
    try {
      await apiRequest<Provider>("/providers", {
        method: "POST",
        body: JSON.stringify(providerForm),
      });
      setProviderModalOpen(false);
      await loadData();
    } catch (error) {
      setFormError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setIsSubmitting(true);

    const payload: Record<string, string | number | number[]> = {
      provider_id: Number(accountForm.provider_id),
      friendly_name: accountForm.friendly_name,
      gmail_address: accountForm.gmail_address,
      telegram_integration_ids: accountForm.telegram_integration_ids,
    };

    if (!editingAccount || accountForm.app_password.trim()) {
      payload.app_password = accountForm.app_password;
    }

    try {
      await apiRequest<PaymentAccount>(editingAccount ? `/payment-accounts/${editingAccount.id}` : "/payment-accounts", {
        method: editingAccount ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      setAccountModalOpen(false);
      await loadData();
    } catch (error) {
      setFormError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function setProviderEnabled(provider: Provider, enabled: boolean) {
    try {
      const updated = await apiRequest<Provider>(`/providers/${provider.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      setProviders((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (error) {
      setPageError(errorMessage(error));
    }
  }

  async function setAccountEnabled(account: PaymentAccount, enabled: boolean) {
    try {
      const updated = await apiRequest<PaymentAccount>(`/payment-accounts/${account.id}/${enabled ? "enable" : "disable"}`, {
        method: "POST",
      });
      setAccounts((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (error) {
      setPageError(errorMessage(error));
    }
  }

  async function testConnection(account: PaymentAccount) {
    setConnectionResults((current) => ({ ...current, [account.id]: "Testing connection..." }));
    try {
      const result = await apiRequest<{ success: boolean; message: string; checked_at: string }>(
        `/payment-accounts/${account.id}/test-connection`,
        { method: "POST" }
      );
      setConnectionResults((current) => ({ ...current, [account.id]: result.message }));
      await loadData();
    } catch (error) {
      setConnectionResults((current) => ({ ...current, [account.id]: errorMessage(error) }));
    }
  }

  return (
    <div className="integrations-stack">
      {pageError ? <div className="alert-message">{pageError}</div> : null}

      <section className="management-section">
        <div className="section-heading">
          <div>
            <h2>Providers</h2>
            <p>Payment source definitions used by Gmail account records.</p>
          </div>
          <button className="primary-button" type="button" onClick={openCreateProvider}>
            Add Provider
          </button>
        </div>

        {isLoading ? (
          <div className="loading-row">Loading providers...</div>
        ) : providers.length === 0 ? (
          <div className="empty-block">No providers configured.</div>
        ) : (
          <div className="provider-list">
            {providers.map((provider) => (
              <article className="provider-row" key={provider.id}>
                <div>
                  <h3>{provider.name}</h3>
                  <p>{provider.parser_key}</p>
                </div>
                <div className="row-actions">
                  <span className={provider.enabled ? "status-badge enabled" : "status-badge disabled"}>
                    {provider.enabled ? "Enabled" : "Disabled"}
                  </span>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => setProviderEnabled(provider, !provider.enabled)}
                  >
                    {provider.enabled ? "Disable" : "Enable"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="management-section">
        <div className="section-heading">
          <div>
            <h2>Gmail Accounts</h2>
            <p>Inbox credentials stored for payment email monitoring.</p>
          </div>
          <button className="primary-button" type="button" onClick={openCreateAccount} disabled={providers.length === 0}>
            Add Gmail Account
          </button>
        </div>

        {isLoading ? (
          <div className="loading-row">Loading Gmail accounts...</div>
        ) : accounts.length === 0 ? (
          <div className="empty-block">No Gmail accounts configured.</div>
        ) : (
          <div className="account-grid">
            {accounts.map((account) => (
              <article className="account-card" key={account.id}>
                <div className="account-card-header">
                  <div>
                    <h3>{account.friendly_name}</h3>
                  </div>
                  <span className={account.enabled ? "status-badge enabled" : "status-badge disabled"}>
                    {account.enabled ? "Enabled" : "Disabled"}
                  </span>
                </div>
                <dl className="account-details">
                  <div>
                    <dt>Gmail</dt>
                    <dd>{account.gmail_address}</dd>
                  </div>
                  <div>
                    <dt>Provider</dt>
                    <dd>{account.provider_name}</dd>
                  </div>
                  <div>
                    <dt>Telegram destinations</dt>
                    <dd>{account.telegram_integration_count}</dd>
                  </div>
                  <div>
                    <dt>Listener status</dt>
                    <dd>
                      <span className={`status-badge ${account.listener_status === "error" ? "disabled" : "enabled"}`}>
                        {account.listener_status}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>Last checked</dt>
                    <dd>{formatDate(account.last_checked_at)}</dd>
                  </div>
                  <div>
                    <dt>Last payment email</dt>
                    <dd>{formatDate(account.last_email_at)}</dd>
                  </div>
                  <div>
                    <dt>Last captured email</dt>
                    <dd>{formatDate(account.last_captured_email_at)}</dd>
                  </div>
                </dl>
                {connectionResults[account.id] ? (
                  <div className="inline-result">{connectionResults[account.id]}</div>
                ) : null}
                <div className="row-actions">
                  <button className="secondary-button" type="button" onClick={() => openEditAccount(account)}>
                    Edit
                  </button>
                  <button className="secondary-button" type="button" onClick={() => testConnection(account)}>
                    Test Connection
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => setAccountEnabled(account, !account.enabled)}
                  >
                    {account.enabled ? "Disable" : "Enable"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <TelegramIntegrationsSection
        integrations={telegramIntegrations}
        accounts={accounts}
        isLoading={isLoading}
        onRefresh={loadData}
      />

      {providerModalOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title">
            <h2 id="provider-modal-title">Add Provider</h2>
            <form className="form-stack" onSubmit={submitProvider}>
              <div className="field">
                <label htmlFor="provider-name">Name</label>
                <input
                  id="provider-name"
                  value={providerForm.name}
                  onChange={(event) => setProviderForm((current) => ({ ...current, name: event.target.value }))}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="parser-key">Parser Key</label>
                <input
                  id="parser-key"
                  value={providerForm.parser_key}
                  onChange={(event) => setProviderForm((current) => ({ ...current, parser_key: event.target.value }))}
                  required
                  pattern="[a-z][a-z0-9]*(_[a-z0-9]+)*"
                />
              </div>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={providerForm.enabled}
                  onChange={(event) => setProviderForm((current) => ({ ...current, enabled: event.target.checked }))}
                />
                Enabled
              </label>
              {formError ? <p className="error-message">{formError}</p> : null}
              <div className="modal-actions">
                <button className="secondary-button" type="button" onClick={() => setProviderModalOpen(false)}>
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={isSubmitting}>
                  Create Provider
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      {accountModalOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel wide" role="dialog" aria-modal="true" aria-labelledby="account-modal-title">
            <h2 id="account-modal-title">{editingAccount ? "Edit Gmail Account" : "Add Gmail Account"}</h2>
            <form className="form-stack" onSubmit={submitAccount}>
              <div className="field">
                <label htmlFor="account-provider">Provider</label>
                <select
                  id="account-provider"
                  value={accountForm.provider_id}
                  onChange={(event) => setAccountForm((current) => ({ ...current, provider_id: event.target.value }))}
                  required
                >
                  <option value="" disabled>
                    Select provider
                  </option>
                  {providers.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="friendly-name">Friendly Name</label>
                <input
                  id="friendly-name"
                  value={accountForm.friendly_name}
                  onChange={(event) => setAccountForm((current) => ({ ...current, friendly_name: event.target.value }))}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="gmail-address">Gmail Address</label>
                <input
                  id="gmail-address"
                  type="email"
                  value={accountForm.gmail_address}
                  onChange={(event) => setAccountForm((current) => ({ ...current, gmail_address: event.target.value }))}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="app-password">Gmail App Password</label>
                <input
                  id="app-password"
                  type="password"
                  value={accountForm.app_password}
                  onChange={(event) => setAccountForm((current) => ({ ...current, app_password: event.target.value }))}
                  required={!editingAccount}
                  autoComplete="new-password"
                />
                <p className="helper-text">Use the Gmail 16-character App Password, not the normal Gmail password.</p>
              </div>
              <div className="field">
                <span>Telegram Integrations</span>
                {sortedTelegramIntegrations.length === 0 ? (
                  <p className="helper-text">No Telegram integrations configured yet.</p>
                ) : (
                  sortedTelegramIntegrations.map((integration) => (
                    <label className="toggle-row" key={integration.id}>
                      <input
                        type="checkbox"
                        checked={accountForm.telegram_integration_ids.includes(integration.id)}
                        onChange={() => toggleAccountTelegramIntegration(integration.id)}
                        disabled={isSubmitting}
                      />
                      <span>
                        {integration.name}
                        {!integration.enabled ? " (disabled)" : ""}
                        {integration.bot_username ? ` — @${integration.bot_username}` : ""}
                      </span>
                    </label>
                  ))
                )}
              </div>
              {formError ? <p className="error-message">{formError}</p> : null}
              <div className="modal-actions">
                <button className="secondary-button" type="button" onClick={() => setAccountModalOpen(false)}>
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={isSubmitting}>
                  {editingAccount ? "Save Changes" : "Create Account"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}
