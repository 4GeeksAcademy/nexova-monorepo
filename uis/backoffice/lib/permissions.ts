/**
 * Permisos de la interfaz, derivados del RANGO del rol que devuelve `GET /auth/me`.
 *
 * Esto solo decide qué mostrar u ocultar: la autorización real vive en el backend (cada endpoint
 * responde 403 si el rol no alcanza). Por eso las reglas son puras y están en un único sitio.
 *
 * Jerarquía (BRIEFING): employee (1) < supervisor (2) < admin (3). Una cuenta externa tiene rango 0.
 * El departamento NO da acciones: solo acota qué datos se listan.
 */

export type Action = "create" | "update" | "approve" | "delete" | "administer";

/** Rango mínimo para cada acción (crear/leer: employee · actualizar/aprobar: supervisor · resto: admin). */
export const REQUIRED_RANK: Readonly<Record<Action, number>> = {
  create: 1,
  update: 2,
  approve: 2,
  delete: 3,
  administer: 3,
};

export const RANK = { external: 0, employee: 1, supervisor: 2, admin: 3 } as const;

export interface AccessProfile {
  rank: number;
  departmentId: string | null;
}

export function isInternal(profile: Pick<AccessProfile, "rank"> | null | undefined): boolean {
  return (profile?.rank ?? 0) >= RANK.employee;
}

export function can(profile: Pick<AccessProfile, "rank"> | null | undefined, action: Action): boolean {
  return (profile?.rank ?? 0) >= REQUIRED_RANK[action];
}

export function hasRank(profile: Pick<AccessProfile, "rank"> | null | undefined, minimum: number): boolean {
  return (profile?.rank ?? 0) >= minimum;
}

/** El Admin ve todos los departamentos (privilegio del rol); el resto, solo el suyo. */
export function seesAllDepartments(profile: Pick<AccessProfile, "rank"> | null | undefined): boolean {
  return can(profile, "administer");
}

/** ¿Puede este perfil leer un dato de `departmentId`? (la API lo vuelve a comprobar y responde 403). */
export function canReadDepartment(profile: AccessProfile | null | undefined, departmentId: string): boolean {
  if (!profile || !isInternal(profile)) return false;
  return seesAllDepartments(profile) || profile.departmentId === departmentId;
}

const ROLE_LABELS: Readonly<Record<string, string>> = {
  employee: "Empleado",
  supervisor: "Supervisor",
  admin: "Administrador",
  manager: "Supervisor (heredado)",
  user: "Usuario externo",
};

export function roleLabel(code: string): string {
  return ROLE_LABELS[code] ?? code;
}

/** Extrae el perfil de acceso de una respuesta de `/auth/me` ya validada. */
export function accessProfileOf(me: { rank: number; department_id: string | null } | null | undefined): AccessProfile | null {
  return me ? { rank: me.rank, departmentId: me.department_id } : null;
}
