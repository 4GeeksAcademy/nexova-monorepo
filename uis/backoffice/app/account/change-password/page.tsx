"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { changePassword } from "@/lib/auth-api";
import { useSectionView } from "@/lib/useSectionView";

export default function ChangePasswordPage() {
  useSectionView("account_change_password");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSuccess(false);

    if (newPassword !== confirmPassword) {
      setError("Las contrasenas nuevas no coinciden.");
      return;
    }

    setSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
      setSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (changeError) {
      const message =
        changeError instanceof Error ? changeError.message : "No se pudo cambiar la contrasena";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100 sm:p-8">
      <div className="mx-auto max-w-2xl space-y-6">
        <header className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6">
          <Link href="/account/profile" className="text-sm font-medium text-cyan-400 hover:text-cyan-300">
            Volver a mi cuenta
          </Link>
          <h1 className="mt-3 text-2xl font-semibold text-slate-100">Cambiar contrasena</h1>
        </header>

        <section className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6">
          <form onSubmit={handleSubmit} className="grid gap-3">
            <label className="block space-y-1">
              <span className="text-sm font-medium text-slate-300">Contrasena actual</span>
              <input
                required
                type="password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500"
              />
            </label>

            <label className="block space-y-1">
              <span className="text-sm font-medium text-slate-300">Nueva contrasena</span>
              <input
                required
                type="password"
                minLength={8}
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500"
              />
            </label>

            <label className="block space-y-1">
              <span className="text-sm font-medium text-slate-300">Confirmar nueva contrasena</span>
              <input
                required
                type="password"
                minLength={8}
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500"
              />
            </label>

            {error && <p className="text-sm text-rose-400">{error}</p>}
            {success && <p className="text-sm text-emerald-400">Contrasena actualizada correctamente.</p>}

            <button
              type="submit"
              disabled={submitting}
              className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-cyan-500 disabled:opacity-60"
            >
              {submitting ? "Guardando..." : "Cambiar contrasena"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
