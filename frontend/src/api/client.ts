// Thin fetch wrapper for the Traceforge API: same-origin cookies, CSRF header, uniform error envelope.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken: string | null = null;
const unauthorizedListeners = new Set<() => void>();

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** Called whenever the API answers 401, so the app can drop its session state. */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
}

export async function api<T>(path: string, { method = "GET", body, query, signal }: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const url = `/api/v1${path}${params.size ? `?${params}` : ""}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "Cannot reach the Traceforge API. Is the backend running?");
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) unauthorizedListeners.forEach((listener) => listener());
    const error = data?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "http_error",
      error?.message ?? `Request failed with HTTP ${response.status}.`,
      error?.details ?? null,
    );
  }
  return data as T;
}
