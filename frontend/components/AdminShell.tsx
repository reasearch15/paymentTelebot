import Link from "next/link";
import { ReactNode } from "react";
import { SignOutButton } from "@/components/SignOutButton";

const navItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/integrations", label: "Integrations" },
  { href: "/emails", label: "Emails" },
  { href: "/ledger", label: "Ledger" },
  { href: "/telegram-deliveries", label: "Telegram Deliveries" },
  { href: "/settlements", label: "Settlements" },
  { href: "/player-ledger", label: "Player Ledger" },
  { href: "/player-settlements", label: "Player Settlements" },
];

type AdminShellProps = {
  children: ReactNode;
  title: string;
  description?: string;
};

export function AdminShell({ children, title, description }: AdminShellProps) {
  return (
    <div className="admin-shell">
      <aside className="sidebar">
        <div>
          <Link href="/dashboard" className="brand">
            Payment Ledger
          </Link>
          <nav className="nav-list" aria-label="Main navigation">
            {navItems.map((item) => (
              <Link key={item.href} href={item.href} className="nav-link">
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="sidebar-footer">
          <SignOutButton />
        </div>
      </aside>
      <main className="content">
        <header className="page-header">
          <div>
            <h1>{title}</h1>
            {description ? <p>{description}</p> : null}
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
