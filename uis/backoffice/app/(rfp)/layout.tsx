import AppShell from "@/components/AppShell";

export default function RfpLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Ventas"
      subtitle="Recepción de RFPs: qué le toca a cada departamento y a quién pedírselo"
    >
      {/* Mismo margen que el resto de secciones (p. ej. /reporting): sin contenido pegado al borde. */}
      <section className="px-6 py-12">
        <div className="mx-auto w-full max-w-6xl">{children}</div>
      </section>
    </AppShell>
  );
}
