import AppShell from "@/components/AppShell";

const TABS = [
  { href: "/inventory/products", label: "Productos" },
  { href: "/inventory/orders/inbound", label: "Registrar entrada" },
  { href: "/inventory/orders/outbound", label: "Registrar salida" },
  { href: "/inventory/orders", label: "Historial" },
];

export default function InventoryLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Inventario de Activos"
      subtitle="Equipos y materiales — Valencia / Miami"
      tabs={TABS}
    >
      {children}
    </AppShell>
  );
}
