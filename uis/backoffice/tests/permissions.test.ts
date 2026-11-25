import { describe, expect, it } from "vitest";
import {
  RANK,
  accessProfileOf,
  can,
  canReadDepartment,
  hasRank,
  isInternal,
  roleLabel,
  seesAllDepartments,
  type Action,
} from "../lib/permissions";

const employee = { rank: RANK.employee, departmentId: "corporate_training" };
const supervisor = { rank: RANK.supervisor, departmentId: "talent_selection" };
const admin = { rank: RANK.admin, departmentId: "technology" };
const external = { rank: RANK.external, departmentId: null };

const MATRIX: Array<[Action, boolean, boolean, boolean, boolean]> = [
  // acción, externo, employee, supervisor, admin
  ["create", false, true, true, true],
  ["update", false, false, true, true],
  ["approve", false, false, true, true],
  ["delete", false, false, false, true],
  ["administer", false, false, false, true],
];

describe("can()", () => {
  it.each(MATRIX)("%s → externo %s · employee %s · supervisor %s · admin %s", (action, ext, emp, sup, adm) => {
    expect(can(external, action)).toBe(ext);
    expect(can(employee, action)).toBe(emp);
    expect(can(supervisor, action)).toBe(sup);
    expect(can(admin, action)).toBe(adm);
  });

  it("sin perfil (no autenticado) no puede nada", () => {
    for (const [action] of MATRIX) expect(can(null, action)).toBe(false);
    expect(can(undefined, "create")).toBe(false);
  });
});

describe("departamento = alcance, no acciones", () => {
  it("dos supervisores de distinto departamento tienen las mismas acciones", () => {
    const sales = { rank: RANK.supervisor, departmentId: "sales" };
    for (const [action] of MATRIX) expect(can(sales, action)).toBe(can(supervisor, action));
  });

  it("solo el admin ve todos los departamentos", () => {
    expect(seesAllDepartments(admin)).toBe(true);
    expect(seesAllDepartments(supervisor)).toBe(false);
    expect(seesAllDepartments(employee)).toBe(false);
  });

  it("canReadDepartment: el suyo sí, otro no, admin todos, externo ninguno", () => {
    expect(canReadDepartment(supervisor, "talent_selection")).toBe(true);
    expect(canReadDepartment(supervisor, "sales")).toBe(false);
    expect(canReadDepartment(admin, "sales")).toBe(true);
    expect(canReadDepartment(external, "sales")).toBe(false);
    expect(canReadDepartment(null, "sales")).toBe(false);
  });
});

describe("utilidades", () => {
  it("isInternal / hasRank", () => {
    expect(isInternal(external)).toBe(false);
    expect(isInternal(employee)).toBe(true);
    expect(hasRank(supervisor, RANK.supervisor)).toBe(true);
    expect(hasRank(employee, RANK.supervisor)).toBe(false);
  });

  it("accessProfileOf adapta la respuesta de /auth/me", () => {
    expect(accessProfileOf({ rank: 2, department_id: "sales" })).toEqual({ rank: 2, departmentId: "sales" });
    expect(accessProfileOf(null)).toBeNull();
  });

  it("roleLabel traduce los códigos conocidos y deja pasar los demás", () => {
    expect(roleLabel("supervisor")).toBe("Supervisor");
    expect(roleLabel("auditor")).toBe("auditor");
  });
});
