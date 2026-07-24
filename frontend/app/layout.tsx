import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Payment Ledger",
  description: "Admin payment ledger foundation",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
