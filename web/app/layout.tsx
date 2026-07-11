import type { Metadata } from "next";
import "./globals.css";
import "./design.css";
import Providers from "./providers";
import AppShell from "./component/AppShell";

export const metadata: Metadata = {
  title: "InsightX — Bank Intelligence",
  description: "AI-powered banking insights",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
