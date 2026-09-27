"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { login, me } from "@/lib/auth-api";
import { useAuth } from "@/lib/AuthContext";
import { startTelemetrySession } from "@/lib/telemetry";

export default function LoginPage() {
  const router = useRouter();
  const { setUser } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      await login(email, password);
      const user = await me();
      startTelemetrySession();
      setUser(user);
      router.push("/");
    } catch (loginError) {
      const message =
        loginError instanceof Error ? loginError.message : "No se pudo iniciar sesion";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-100">
      <div className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900/80 p-8 shadow-[0_24px_40px_-36px_rgba(0,0,0,0.8)]">
        <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
          Nexova Solutions
        </p>
        <h1 className="mt-2 text-2xl font-semibold text-slate-100">Iniciar sesion</h1>
        <p className="mt-2 text-sm text-slate-400">Backoffice</p>

        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <label className="block space-y-1">
            <span className="text-sm font-medium text-slate-300">Email</span>
            <input
              required
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-sm font-medium text-slate-300">Contrasena</span>
            <input
              required
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500"
            />
          </label>

          <p className="text-right text-sm">
            <Link href="/forgot-password" className="font-medium text-cyan-400 hover:text-cyan-300">
              Olvidaste tu contrasena?
            </Link>
          </p>

          {error && <p className="text-sm text-rose-400">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-cyan-500 disabled:opacity-60"
          >
            {submitting ? "Entrando..." : "Entrar"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-400">
          No tienes cuenta?{" "}
          <Link href="/register" className="font-medium text-cyan-400 hover:text-cyan-300">
            Registrate
          </Link>
        </p>
      </div>
    </main>
  );
}
