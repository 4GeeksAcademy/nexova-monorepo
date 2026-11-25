import AppShell from "@/components/AppShell";

export default function AdminLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Administración"
      subtitle="Roles, departamentos y asignación de usuarios — solo administradores"
    >
      {children}
    </AppShell>
  );
}
