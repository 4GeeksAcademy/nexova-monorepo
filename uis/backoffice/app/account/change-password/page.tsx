"use client";

import { FormEvent, useState } from "react";
import {
  ACCOUNT_CARD,
  ERROR_NOTICE,
  FIELD_HINT,
  FIELD_INPUT,
  FIELD_LABEL,
  SUBMIT_BUTTON,
  SUCCESS_NOTICE,
} from "@/components/account/form-styles";
import { changePassword } from "@/lib/auth-api";
import { useSectionView } from "@/lib/useSectionView";

const MIN_PASSWORD_LENGTH = 8;

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
      setError("Las contraseñas nuevas no coinciden.");
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
        changeError instanceof Error ? changeError.message : "No se pudo cambiar la contraseña.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <header className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Mi cuenta</p>
        <h1 className="mt-2 text-3xl font-medium tracking-tight text-stone-900 sm:text-4xl">
          Cambiar contraseña
        </h1>
        <p className="mt-3 text-base leading-7 text-stone-600">
          Introduce tu contraseña actual y elige una nueva.
        </p>
      </header>

      <section className={ACCOUNT_CARD}>
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label htmlFor="current-password" className={FIELD_LABEL}>
              Contraseña actual
            </label>
            <input
              id="current-password"
              name="current-password"
              required
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className={FIELD_INPUT}
            />
          </div>

          <div>
            <label htmlFor="new-password" className={FIELD_LABEL}>
              Nueva contraseña
            </label>
            <input
              id="new-password"
              name="new-password"
              required
              type="password"
              autoComplete="new-password"
              minLength={MIN_PASSWORD_LENGTH}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className={FIELD_INPUT}
              aria-describedby="new-password-hint"
            />
            <p id="new-password-hint" className={FIELD_HINT}>
              Mínimo {MIN_PASSWORD_LENGTH} caracteres.
            </p>
          </div>

          <div>
            <label htmlFor="confirm-password" className={FIELD_LABEL}>
              Confirmar nueva contraseña
            </label>
            <input
              id="confirm-password"
              name="confirm-password"
              required
              type="password"
              autoComplete="new-password"
              minLength={MIN_PASSWORD_LENGTH}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className={FIELD_INPUT}
            />
          </div>

          {error && (
            <p role="alert" className={ERROR_NOTICE}>
              {error}
            </p>
          )}
          {success && (
            <p role="status" className={SUCCESS_NOTICE}>
              Contraseña actualizada correctamente.
            </p>
          )}

          <button type="submit" disabled={submitting} className={SUBMIT_BUTTON}>
            {submitting ? "Guardando…" : "Cambiar contraseña"}
          </button>
        </form>
      </section>
    </>
  );
}
