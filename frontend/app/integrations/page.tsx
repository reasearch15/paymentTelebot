import { AdminShell } from "@/components/AdminShell";
import { IntegrationsManager } from "@/components/IntegrationsManager";

export default function IntegrationsPage() {
  return (
    <AdminShell title="Integrations" description="Manage payment notification inboxes.">
      <IntegrationsManager />
    </AdminShell>
  );
}
