import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-xl px-6 py-20">
      <h1 className="text-2xl font-semibold text-slate-900">No encontramos esta página</h1>
      <p className="mt-3 text-slate-600">
        La dirección no existe o ya no está disponible. Comprueba que esté bien escrita o vuelve al inicio.
      </p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-full bg-slate-900 px-5 py-2 text-sm font-medium text-white transition hover:bg-slate-700"
      >
        Volver al inicio
      </Link>
    </main>
  );
}
