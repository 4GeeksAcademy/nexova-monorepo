/**
 * Catálogo de secciones del backoffice que enlaza el dashboard. Al aterrizar
 * tras el login, es la puerta de entrada a cada sección (las secciones vuelven
 * aquí con «Volver al dashboard»).
 */

export interface DashboardSection {
  href: string;
  title: string;
  description: string;
  /** Área de negocio, mismo vocabulario que el `eyebrow` de cada sección. */
  area: string;
}

export const DASHBOARD_SECTIONS: readonly DashboardSection[] = [
  {
    href: "/incidents",
    title: "Analizador de incidencias",
    description: "Sube el CSV del helpdesk y obtén un resumen operativo.",
    area: "Soporte",
  },
  {
    href: "/incident-manager",
    title: "Gestor de incidencias",
    description: "Registro, seguimiento y métricas de incidencias por sede.",
    area: "Soporte",
  },
  {
    href: "/inventory/products",
    title: "Inventario de activos",
    description: "Equipos y materiales, con entradas, salidas e historial.",
    area: "Operaciones",
  },
  {
    href: "/reporting",
    title: "Reporte semanal",
    description: "Inversión y entrega de formación por oficina y programa.",
    area: "Dirección",
  },
  {
    href: "/knowledge",
    title: "Chat de soporte",
    description: "Agente de primera línea sobre tickets, inventario y base de conocimiento.",
    area: "Soporte",
  },
  {
    href: "/rfp",
    title: "Recepción de RFPs",
    description: "Bandeja de solicitudes y aprobación por departamento.",
    area: "Ventas",
  },
  {
    href: "/telemetry",
    title: "Telemetría técnica",
    description: "Salud técnica del sistema a partir de eventos.",
    area: "Ingeniería",
  },
];

/** Secciones previstas que todavía no tienen página. */
export const UPCOMING_SECTIONS: readonly string[] = ["Flujo de Caja", "Facturación", "Proyecciones"];
