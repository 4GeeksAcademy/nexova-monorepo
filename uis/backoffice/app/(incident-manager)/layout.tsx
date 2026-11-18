import AppShell from "@/components/AppShell";

const TABS = [
  { href: "/incident-manager", label: "Listado" },
  { href: "/incident-manager/new", label: "Nueva incidencia" },
  { href: "/incident-manager/summary", label: "Resumen" },
];

export default function IncidentManagerLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Gestor de incidencias"
      subtitle="Registro, seguimiento y métricas de incidencias — Valencia / Miami"
      tabs={TABS}
    >
      {children}
    </AppShell>
  );
}
