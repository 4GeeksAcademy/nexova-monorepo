import type { AnalysisSummary } from "./types";
import { getToken } from "./auth-storage";
import { handleSessionExpired } from "./session";

const API_BASE_PATH = "/api/incidents";

function authHeaders(): HeadersInit {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function handleUnauthorized(res: Response): void {
  if (res.status === 401) {
    handleSessionExpired();
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // el cuerpo no era JSON, seguimos con el mensaje genérico
  }
  return `Error inesperado (HTTP ${res.status}).`;
}

export async function analyzeIncidentsFile(file: File): Promise<AnalysisSummary> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_BASE_PATH}/analyze`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });

  if (!res.ok) {
    handleUnauthorized(res);
    throw new ApiError(res.status, await readErrorDetail(res));
  }

  return res.json();
}

/**
 * Descarga el CSV de resultados directamente desde el navegador,
 * disparando el diálogo nativo de "guardar como".
 */
export async function downloadResultsCsv(): Promise<void> {
  const res = await fetch(`${API_BASE_PATH}/results/export`, {
    headers: authHeaders(),
  });

  if (!res.ok) {
    handleUnauthorized(res);
    throw new ApiError(res.status, await readErrorDetail(res));
  }

  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "results.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
