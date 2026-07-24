import { AdminShell } from "@/components/AdminShell";
import { EmailsBrowser } from "@/components/EmailsBrowser";

export default function EmailsPage() {
  return (
    <AdminShell title="Captured Emails" description="Inspect raw Gmail messages captured by the listener.">
      <EmailsBrowser />
    </AdminShell>
  );
}
