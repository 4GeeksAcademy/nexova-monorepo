import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-xl px-6 py-16">
      <p className="font-mono text-xs uppercase tracking-widest text-teal-700">Nexova · Backoffice</p>
      <h1 className="mt-2 text-3xl font-medium text-stone-900">No encontramos esta página</h1>
      <p className="mt-3 text-stone-600">
        La dirección no existe o ya no está disponible. Comprueba que esté bien escrita o vuelve al inicio.
      </p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-lg bg-teal-700 px-5 py-2 text-sm font-medium text-white transition hover:bg-teal-800"
      >
        Volver al inicio
      </Link>
    </main>
  );
}
