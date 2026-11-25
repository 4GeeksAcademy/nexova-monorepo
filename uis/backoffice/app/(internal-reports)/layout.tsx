import AppShell from "@/components/AppShell";

export default function InternalReportsLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Informes internos"
      subtitle="Notas e informes de tu departamento — el administrador ve todos"
    >
      {children}
    </AppShell>
  );
}
