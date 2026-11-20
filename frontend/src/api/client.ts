export const SESSION_RECHECK_EVENT = "evorag:session-recheck";
const SESSION_TOKEN_KEY = "evorag:session-token";
let tokenInMemory: string | null = null;

function sessionToken(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_TOKEN_KEY);
  } catch {
    return tokenInMemory;
  }
}

export function setSessionToken(token: string | null): void {
  tokenInMemory = token;
  try {
    if (token) window.sessionStorage.setItem(SESSION_TOKEN_KEY, token);
    else window.sessionStorage.removeItem(SESSION_TOKEN_KEY);
  } catch {
    // Keep the current tab usable if the browser also blocks session storage.
  }
}

export function apiUrl(path: string): URL {
  const base: string | undefined = import.meta.env.VITE_API_BASE_URL;
  return new URL(path, base || window.location.origin);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiFetch(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  const url = apiUrl(path);
  const headers = new Headers(options?.headers);
  const token =
    url.origin === apiUrl("/").origin && url.pathname.startsWith("/api/v1/")
      ? sessionToken()
      : null;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url, {
    credentials: "include",
    ...options,
    headers,
  });
  if (response.ok) return response;
  if (
    response.status === 401 &&
    url.pathname === "/api/v1/auth/me" &&
    token &&
    sessionToken() === token
  ) {
    setSessionToken(null);
  }
  if (response.status === 401 && !url.pathname.startsWith("/api/v1/auth/")) {
    window.dispatchEvent(new Event(SESSION_RECHECK_EVENT));
  }
  const payload = (await response.json().catch(() => null)) as {
    detail?: unknown;
  } | null;
  const message = payload?.detail;
  throw new ApiError(
    response.status,
    typeof message === "string"
      ? message
      : `The API returned HTTP ${response.status}.`,
  );
}

export async function apiJson<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  return (await (await apiFetch(path, options)).json()) as T;
}

export function postJson(data: unknown, signal?: AbortSignal): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    signal,
  };
}
