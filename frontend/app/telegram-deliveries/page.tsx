import { AdminShell } from "@/components/AdminShell";
import { TelegramDeliveriesBrowser } from "@/components/TelegramDeliveriesBrowser";

export default function TelegramDeliveriesPage() {
  return (
    <AdminShell
      title="Telegram Deliveries"
      description="Delivery operations center for every Telegram send attempt."
    >
      <TelegramDeliveriesBrowser />
    </AdminShell>
  );
}
