"use client";

import { FormEvent, useState } from "react";
import {
  ACCOUNT_CARD,
  ERROR_NOTICE,
  FIELD_INPUT,
  FIELD_LABEL,
  SUBMIT_BUTTON,
  SUCCESS_NOTICE,
} from "@/components/account/form-styles";
import { Resource } from "@/components/dashboard/Resource";
import { me, updateProfile, type MeRead, type Role } from "@/lib/auth-api";
import { useAsyncResource } from "@/lib/useAsyncResource";
import { useSectionView } from "@/lib/useSectionView";

const ROLE_LABELS: Record<Role, string> = {
  admin: "Administrador",
  manager: "Manager",
  user: "Usuario",
};

// Cargador de módulo (identidad estable) para `useAsyncResource`.
const loadAccount = me;

interface ProfileFormProps {
  account: MeRead;
}

function ProfileForm({ account }: ProfileFormProps) {
  const [form, setForm] = useState({
    name: account.profile.name ?? "",
    phone: account.profile.phone ?? "",
    address: account.profile.address ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveSuccess, setSaveSuccess] = useState(false);

  function setField(field: keyof typeof form, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
    setSaveSuccess(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaveError("");
    setSaveSuccess(false);

    try {
      await updateProfile({
        name: form.name || undefined,
        phone: form.phone || undefined,
        address: form.address || undefined,
      });
      setSaveSuccess(true);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "No se pudo guardar el perfil.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <dl className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-stone-200 bg-stone-50 px-4 py-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-stone-500">Email</dt>
          <dd className="mt-1 break-all text-sm text-stone-900">{account.email}</dd>
        </div>
        <div className="rounded-xl border border-stone-200 bg-stone-50 px-4 py-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-stone-500">Rol</dt>
          <dd className="mt-1 text-sm text-stone-900">{ROLE_LABELS[account.role] ?? account.role}</dd>
        </div>
      </dl>

      <form onSubmit={handleSubmit} className="mt-6 space-y-5">
        <div>
          <label htmlFor="profile-name" className={FIELD_LABEL}>
            Nombre
          </label>
          <input
            id="profile-name"
            name="name"
            autoComplete="name"
            value={form.name}
            onChange={(event) => setField("name", event.target.value)}
            className={FIELD_INPUT}
          />
        </div>

        <div>
          <label htmlFor="profile-phone" className={FIELD_LABEL}>
            Teléfono
          </label>
          <input
            id="profile-phone"
            name="phone"
            type="tel"
            autoComplete="tel"
            value={form.phone}
            onChange={(event) => setField("phone", event.target.value)}
            className={FIELD_INPUT}
          />
        </div>

        <div>
          <label htmlFor="profile-address" className={FIELD_LABEL}>
            Dirección
          </label>
          <input
            id="profile-address"
            name="address"
            autoComplete="street-address"
            value={form.address}
            onChange={(event) => setField("address", event.target.value)}
            className={FIELD_INPUT}
          />
        </div>

        {saveError && (
          <p role="alert" className={ERROR_NOTICE}>
            {saveError}
          </p>
        )}
        {saveSuccess && (
          <p role="status" className={SUCCESS_NOTICE}>
            Perfil actualizado.
          </p>
        )}

        <button type="submit" disabled={saving} className={SUBMIT_BUTTON}>
          {saving ? "Guardando…" : "Guardar cambios"}
        </button>
      </form>
    </>
  );
}

export default function AccountProfilePage() {
  useSectionView("account_profile");
  const { state, reload } = useAsyncResource(loadAccount);

  return (
    <>
      <header className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Mi cuenta</p>
        <h1 className="mt-2 text-3xl font-medium tracking-tight text-stone-900 sm:text-4xl">Perfil</h1>
        <p className="mt-3 text-base leading-7 text-stone-600">
          Consulta tu acceso y mantén al día tus datos de contacto.
        </p>
      </header>

      <section className={ACCOUNT_CARD}>
        <Resource state={state} onRetry={reload}>
          {(account) => <ProfileForm account={account} />}
        </Resource>
      </section>
    </>
  );
}
