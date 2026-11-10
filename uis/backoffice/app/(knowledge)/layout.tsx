import AppShell from "@/components/AppShell";

export default function KnowledgeLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Ventas"
      subtitle="Asistente de ventas sobre servicios, tarifas, plazos y objeciones"
    >
      {children}
    </AppShell>
  );
}
