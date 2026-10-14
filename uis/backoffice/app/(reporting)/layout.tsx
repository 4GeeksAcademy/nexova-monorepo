import AppShell from "@/components/AppShell";

export default function ReportingLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Dirección"
      subtitle="Inversión y entrega de material de formación por oficina y programa"
    >
      {children}
    </AppShell>
  );
}
