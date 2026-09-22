"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { me, updateProfile, type MeRead } from "@/lib/auth-api";
import { useSectionView } from "@/lib/useSectionView";

export default function AccountProfilePage() {
  useSectionView("account_profile");
  const [account, setAccount] = useState<MeRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [form, setForm] = useState({ name: "", phone: "", address: "" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    me()
      .then((data) => {
        setAccount(data);
        setForm({
          name: data.profile.name ?? "",
          phone: data.profile.phone ?? "",
          address: data.profile.address ?? "",
        });
      })
      .catch((error) => {
        setLoadError(error instanceof Error ? error.message : "No se pudo cargar el perfil");
      })
      .finally(() => setLoading(false));
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaveError("");
    setSaveSuccess(false);

    try {
      const updatedProfile = await updateProfile({
        name: form.name || undefined,
        phone: form.phone || undefined,
        address: form.address || undefined,
      });
      setAccount((current) => (current ? { ...current, profile: updatedProfile } : current));
      setSaveSuccess(true);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "No se pudo guardar el perfil");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100 sm:p-8">
      <div className="mx-auto max-w-2xl space-y-6">
        <header className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6">
          <Link href="/" className="text-sm font-medium text-cyan-400 hover:text-cyan-300">
            Volver al dashboard
          </Link>
          <h1 className="mt-3 text-2xl font-semibold text-slate-100">Mi cuenta</h1>
          <Link
            href="/account/change-password"
            className="mt-2 inline-block text-sm font-medium text-cyan-400 hover:text-cyan-300"
          >
            Cambiar contrasena
          </Link>
        </header>

        <section className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6">
          {loading && <p className="text-sm text-slate-400">Cargando perfil...</p>}
          {!loading && loadError && <p className="text-sm text-rose-400">Error: {loadError}</p>}

          {!loading && account && (
            <>
              <div className="grid gap-1 text-sm text-slate-300">
                <p>
                  <strong className="text-slate-100">Email:</strong> {account.email}
                </p>
                <p>
                  <strong className="text-slate-100">Rol:</strong> {account.role}
                </p>
              </div>

              <form onSubmit={handleSubmit} className="mt-6 grid gap-3">
                <label className="block space-y-1">
                  <span className="text-sm font-medium text-slate-300">Nombre</span>
                  <input
                    value={form.name}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, name: event.target.value }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500"
                  />
                </label>

                <label className="block space-y-1">
                  <span className="text-sm font-medium text-slate-300">Telefono</span>
                  <input
                    value={form.phone}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, phone: event.target.value }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500"
                  />
                </label>

                <label className="block space-y-1">
                  <span className="text-sm font-medium text-slate-300">Direccion</span>
                  <input
                    value={form.address}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, address: event.target.value }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500"
                  />
                </label>

                {saveError && <p className="text-sm text-rose-400">{saveError}</p>}
                {saveSuccess && <p className="text-sm text-emerald-400">Perfil actualizado.</p>}

                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-cyan-500 disabled:opacity-60"
                >
                  {saving ? "Guardando..." : "Guardar cambios"}
                </button>
              </form>
            </>
          )}
        </section>
      </div>
    </main>
  );
}
