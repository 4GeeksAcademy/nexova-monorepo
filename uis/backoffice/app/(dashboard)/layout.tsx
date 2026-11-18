import AppShell from "@/components/AppShell";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Dirección"
      subtitle="Control financiero consolidado y acceso a cada área"
      showBackLink={false}
    >
      {children}
    </AppShell>
  );
}
