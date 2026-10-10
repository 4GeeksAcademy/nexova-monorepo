import AppShell from "@/components/AppShell";

export default function TelemetryLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova Engineering"
      subtitle="Salud técnica del sistema a partir de telemetría"
    >
      {children}
    </AppShell>
  );
}
