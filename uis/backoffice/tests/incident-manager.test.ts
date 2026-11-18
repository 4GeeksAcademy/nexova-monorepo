import { describe, expect, it } from "vitest";
import {
  BRANCHES,
  BRANCH_LABELS,
  CATEGORIES,
  EMPTY_FORM,
  ORIGINS,
  STATUSES,
  TRANSITIONS,
  isFinalStatus,
  selectableStatuses,
  shouldHighlightBranch,
  toCreatePayload,
  validateIncidentForm,
  type IncidentFormValues,
} from "../lib/incident-manager-domain";
import {
  parseErrorBody,
  parseIncident,
  parseIncidentList,
  parseSummary,
  withStatus,
} from "../lib/incident-manager";

const valid: IncidentFormValues = {
  title: "  Fallo en el ATS ",
  description: "No cargan las candidaturas.",
  category: "technical_failure",
  origin: "branch",
  branch: "valencia_operations",
};

const incident = {
  id: 7,
  title: "t",
  description: "d",
  category: "other",
  status: "open",
  origin: "internal",
  branch: "remote",
  created_at: "2026-11-18T10:00:00Z",
  updated_at: "2026-11-18T10:00:00Z",
};

describe("valores del BRIEFING", () => {
  it("expone exactamente las 4 sedes, 8 categorías, 4 estados y 3 orígenes", () => {
    expect(BRANCHES).toEqual(["central", "valencia_operations", "miami_office", "remote"]);
    expect(CATEGORIES).toHaveLength(8);
    expect(CATEGORIES).toContain("sla_breach");
    expect(STATUSES).toEqual(["open", "in_progress", "resolved", "discarded"]);
    expect(ORIGINS).toEqual(["customer", "branch", "internal"]);
  });

  it("muestra las etiquetas del BRIEFING, incluido remoto", () => {
    expect(BRANCH_LABELS.central).toBe("Central — Sede Valencia");
    expect(BRANCH_LABELS.remote).toBe("Remoto (empleado sin sede fija)");
    expect(new Set(Object.values(BRANCH_LABELS)).size).toBe(4);
  });
});

describe("ciclo de vida", () => {
  it("solo permite open→in_progress|discarded y in_progress→resolved|discarded", () => {
    expect(TRANSITIONS.open).toEqual(["in_progress", "discarded"]);
    expect(TRANSITIONS.in_progress).toEqual(["resolved", "discarded"]);
    expect(isFinalStatus("resolved")).toBe(true);
    expect(isFinalStatus("discarded")).toBe(true);
    expect(isFinalStatus("open")).toBe(false);
  });

  it("el selector ofrece el estado actual más los permitidos", () => {
    expect(selectableStatuses("open")).toEqual(["open", "in_progress", "discarded"]);
    expect(selectableStatuses("resolved")).toEqual(["resolved"]);
  });
});

describe("validación del formulario", () => {
  it("un formulario vacío marca los cinco campos", () => {
    expect(Object.keys(validateIncidentForm(EMPTY_FORM)).sort()).toEqual(
      ["branch", "category", "description", "origin", "title"],
    );
  });

  it("rechaza texto en blanco y títulos demasiado largos", () => {
    expect(validateIncidentForm({ ...valid, title: "   " }).title).toBeDefined();
    expect(validateIncidentForm({ ...valid, title: "x".repeat(121) }).title).toMatch(/120/);
    expect(validateIncidentForm({ ...valid, description: "  " }).description).toBeDefined();
  });

  it("no acepta sedes que no estén en el BRIEFING", () => {
    const bad = { ...valid, branch: "headquarters" } as unknown as IncidentFormValues;
    expect(validateIncidentForm(bad).branch).toBeDefined();
  });

  it("un formulario válido no tiene errores y genera el payload recortado", () => {
    expect(validateIncidentForm(valid)).toEqual({});
    expect(toCreatePayload(valid)).toEqual({
      title: "Fallo en el ATS",
      description: "No cargan las candidaturas.",
      category: "technical_failure",
      origin: "branch",
      branch: "valencia_operations",
    });
    expect(toCreatePayload(EMPTY_FORM)).toBeNull();
  });

  it("resalta la sede solo cuando el origen es «sede»", () => {
    expect(shouldHighlightBranch("branch")).toBe(true);
    expect(shouldHighlightBranch("customer")).toBe(false);
    expect(shouldHighlightBranch("")).toBe(false);
  });
});

describe("validación de respuestas de la API", () => {
  it("acepta una incidencia correcta y una lista vacía", () => {
    expect(parseIncident(incident).branch).toBe("remote");
    expect(parseIncidentList([])).toEqual([]);
  });

  it("rechaza valores fuera de contrato en vez de castearlos", () => {
    expect(() => parseIncident({ ...incident, status: "OPEN" })).toThrow();
    expect(() => parseIncident({ ...incident, branch: "headquarters" })).toThrow();
    expect(() => parseIncident({ ...incident, id: "7" })).toThrow();
    expect(() => parseIncidentList({})).toThrow();
  });

  it("valida el resumen, también con todo a cero", () => {
    const zeros = (keys: readonly string[]) => Object.fromEntries(keys.map((k) => [k, 0]));
    const summary = parseSummary({
      total: 0,
      by_status: zeros(STATUSES),
      by_category: zeros(CATEGORIES),
      by_origin: zeros(ORIGINS),
      by_branch: zeros(BRANCHES),
    });
    expect(summary.total).toBe(0);
    expect(() => parseSummary({ total: 1, by_status: {} })).toThrow();
    expect(() => parseSummary(null)).toThrow();
  });
});

describe("errores de la API", () => {
  it("asocia los errores 400 a su campo", () => {
    const parsed = parseErrorBody({
      errors: [
        { field: "title", message: "Este campo es obligatorio." },
        { field: "branch", message: "Valor no permitido." },
      ],
    });
    expect(parsed.fieldErrors).toEqual({
      title: "Este campo es obligatorio.",
      branch: "Valor no permitido.",
    });
    expect(parsed.message).toBe("Revisa los campos marcados.");
  });

  it("deja los errores sin campo conocido en el mensaje general (p. ej. transición inválida)", () => {
    const parsed = parseErrorBody({
      errors: [{ field: "status", message: "No se puede pasar de «open» a «resolved»." }],
    });
    expect(parsed.fieldErrors).toEqual({});
    expect(parsed.message).toContain("No se puede pasar");
  });

  it("no revienta con cuerpos inesperados", () => {
    expect(parseErrorBody(null)).toEqual({ message: "", fieldErrors: {} });
    expect(parseErrorBody({ detail: "boom" })).toEqual({ message: "", fieldErrors: {} });
  });
});

describe("actualización optimista", () => {
  it("cambia solo la incidencia indicada y permite el rollback", () => {
    const rows = [parseIncident(incident), parseIncident({ ...incident, id: 8 })];
    const changed = withStatus(rows, 7, "in_progress");
    expect(changed.map((r) => r.status)).toEqual(["in_progress", "open"]);
    expect(rows[0].status).toBe("open"); // no muta el original
    expect(withStatus(changed, 7, "open").map((r) => r.status)).toEqual(["open", "open"]);
  });
});
