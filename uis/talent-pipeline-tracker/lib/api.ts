import type {
	CandidateFilters,
	CandidateNote,
	CandidatePatchPayload,
	CandidatePayload,
	CandidateRecord,
	NotesResponse,
	RecordsListResponse,
} from "@/types";
import { clearToken, getToken } from "./auth-storage";
import {
	CONNECTION_ERROR_MESSAGE,
	GENERIC_ERROR_MESSAGE,
	statusMessage,
	userMessage,
} from "./http-errors";

function getApiBaseUrl(): string {
	const baseUrl = process.env.NEXT_PUBLIC_API_URL;
	if (!baseUrl) {
		throw new Error("Falta configurar NEXT_PUBLIC_API_URL en .env.local");
	}

	return baseUrl;
}

async function apiRequest<T>(
	endpoint: string,
	options?: RequestInit,
): Promise<T> {
	const token = getToken();

	let response: Response;
	try {
		response = await fetch(`${getApiBaseUrl()}${endpoint}`, {
			headers: {
				"Content-Type": "application/json",
				...(token ? { Authorization: `Bearer ${token}` } : {}),
				...(options?.headers ?? {}),
			},
			...options,
		});
	} catch {
		throw new Error(CONNECTION_ERROR_MESSAGE);
	}

	if (response.status === 401) {
		clearToken();
		if (typeof window !== "undefined") {
			window.location.href = "/login";
		}
		throw new Error("Sesion expirada.");
	}

	const text = await response.text();
	let data: unknown = null;
	if (text) {
		try {
			data = JSON.parse(text);
		} catch {
			// El cuerpo no es JSON (p. ej. una página de error del servidor): nunca se muestra en crudo.
			throw new Error(response.ok ? GENERIC_ERROR_MESSAGE : statusMessage(response.status));
		}
	}

	if (!response.ok) {
		const body = data as { error?: unknown; message?: unknown } | null;
		throw new Error(userMessage(response.status, body?.error ?? body?.message));
	}

	return data as T;
}

function buildRecordsQuery(
	filters?: CandidateFilters,
	search?: string,
	page?: number,
	limit?: number,
): string {
	const params = new URLSearchParams();

	if (filters?.status) {
		params.set("status", filters.status);
	}

	if (filters?.stage) {
		params.set("stage", filters.stage);
	}

	if (search) {
		params.set("search", search);
	}

	if (page) {
		params.set("page", String(page));
	}

	if (limit) {
		params.set("limit", String(limit));
	}

	const queryString = params.toString();
	return queryString ? `?${queryString}` : "";
}

export async function getRecords(
	filters?: CandidateFilters,
	search?: string,
	page?: number,
	limit?: number,
): Promise<RecordsListResponse> {
	const query = buildRecordsQuery(filters, search, page, limit);
	return apiRequest<RecordsListResponse>(`/records${query}`);
}

export async function getAllRecords(
	filters?: CandidateFilters,
	search?: string,
): Promise<CandidateRecord[]> {
	const pageSize = 100;
	const firstPage = await getRecords(filters, search, 1, pageSize);
	const totalPages = Math.max(1, Math.ceil(firstPage.total / firstPage.limit));

	if (totalPages === 1) {
		return firstPage.data;
	}

	const restPages = await Promise.all(
		Array.from({ length: totalPages - 1 }, (_, index) =>
			getRecords(filters, search, index + 2, pageSize),
		),
	);

	return [firstPage.data, ...restPages.map((page) => page.data)].flat();
}

export async function getRecordById(id: string): Promise<CandidateRecord> {
	return apiRequest<CandidateRecord>(`/records/${id}`);
}

export async function createRecord(
	payload: CandidatePayload,
): Promise<CandidateRecord> {
	return apiRequest<CandidateRecord>("/records", {
		method: "POST",
		body: JSON.stringify(payload),
	});
}

export async function updateRecord(
	id: string,
	payload: CandidatePayload,
): Promise<CandidateRecord> {
	return apiRequest<CandidateRecord>(`/records/${id}`, {
		method: "PUT",
		body: JSON.stringify(payload),
	});
}

export async function patchRecord(
	id: string,
	payload: CandidatePatchPayload,
): Promise<CandidateRecord> {
	return apiRequest<CandidateRecord>(`/records/${id}`, {
		method: "PATCH",
		body: JSON.stringify(payload),
	});
}

export async function getRecordNotes(recordId: string): Promise<CandidateNote[]> {
	const response = await apiRequest<NotesResponse>(`/records/${recordId}/notes`);
	return response.data;
}

export async function createRecordNote(
	recordId: string,
	content: string,
): Promise<CandidateNote> {
	return apiRequest<CandidateNote>(`/records/${recordId}/notes`, {
		method: "POST",
		body: JSON.stringify({ content }),
	});
}

export async function deleteRecordNote(
	recordId: string,
	noteId: string,
): Promise<void> {
	await apiRequest(`/records/${recordId}/notes/${noteId}`, {
		method: "DELETE",
	});
}


