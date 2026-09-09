import AppShell from "@/components/AppShell";

export default function IncidentsLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova Support Ops"
      subtitle="Consola específica para análisis de incidencias"
    >
      {children}
    </AppShell>
  );
}
