import AppShell from "@/components/AppShell";

const TABS = [
  { href: "/account/profile", label: "Perfil" },
  { href: "/account/change-password", label: "Cambiar contraseña" },
];

export default function AccountLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <AppShell
      eyebrow="Nexova · Mi cuenta"
      subtitle="Tus datos de contacto y la seguridad de tu acceso"
      tabs={TABS}
    >
      <section className="px-6 py-12">
        <div className="mx-auto w-full max-w-2xl">{children}</div>
      </section>
    </AppShell>
  );
}
