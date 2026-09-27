"use client";

import { useSectionView } from "@/lib/useSectionView";

/**
 * Puente cliente para emitir `backoffice_section_viewed` desde un Server
 * Component (que no puede llamar hooks directamente).
 */
export default function SectionViewTracker({ section }: { section: string }) {
  useSectionView(section);
  return null;
}
