"use client";

import { useEffect } from "react";
import { track } from "@/lib/telemetry";

/** Emite `backoffice_section_viewed` al montar la página de una sección. */
export function useSectionView(section: string): void {
  useEffect(() => {
    track("backoffice_section_viewed", { section });
  }, [section]);
}
