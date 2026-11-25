"use client";

import Link from "next/link";
import { useAuth } from "@/lib/AuthContext";
import { sectionsForRank } from "@/lib/dashboard-sections";

const CARD = "rounded-2xl border border-stone-300 bg-white/85 p-5 shadow-sm";

/** Accesos del dashboard filtrados por el rango del rol (p. ej. «Roles y departamentos» solo para admin). */
export default function SectionGrid() {
  const { user } = useAuth();
  const sections = sectionsForRank(user?.rank ?? 1);

  return (
    <ul className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {sections.map((section) => (
        <li key={section.href}>
          <Link
            href={section.href}
            prefetch={false}
            className={`${CARD} group block h-full transition hover:border-teal-600`}
          >
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-teal-700">{section.area}</p>
            <p className="mt-2 flex items-center justify-between gap-2 text-base font-medium text-stone-900">
              {section.title}
              <span
                aria-hidden="true"
                className="text-stone-400 transition group-hover:translate-x-0.5 group-hover:text-teal-700"
              >
                →
              </span>
            </p>
            <p className="mt-1 text-sm leading-6 text-stone-600">{section.description}</p>
          </Link>
        </li>
      ))}
    </ul>
  );
}
