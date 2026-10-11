/**
 * Shared API plumbing. The base URL is the only backend coordinate the browser knows; it is a
 * public value (NEXT_PUBLIC_*). No secret may ever be placed in a NEXT_PUBLIC_* variable.
 */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
export const AUTH_REQUEST_TIMEOUT_MS = 15_000;
export const LOGIN_REQUEST_TIMEOUT_MS = 30_000;

export function timeoutSignal(timeoutMs: number): AbortSignal {
  return AbortSignal.timeout(timeoutMs);
}

export class ApiError extends Error {
  status: number;
  code?: string;
  requestId?: string;

  constructor(status: number, message: string, code?: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

type ErrorBody = {
  error?: { code?: string; message?: string; request_id?: string; details?: Array<{ field?: string; message?: string }> };
  detail?: string | { code?: string; message?: string };
};

/** Turn an error response into a message an engineer can act on (never a bare "something went wrong"). */
export async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  let body: ErrorBody | null = null;
  try {
    body = (await response.clone().json()) as ErrorBody;
  } catch {
    body = null;
  }
  const message =
    body?.error?.message ??
    (typeof body?.detail === "string" ? body.detail : body?.detail?.message) ??
    null;
  const reference = body?.error?.request_id ? ` (reference ${body.error.request_id.slice(0, 8)})` : "";
  if (message) return `${message}${reference}`;
  switch (response.status) {
    case 401:
      return "Your session has expired. Sign in again.";
    case 403:
      return "Your role does not permit this action.";
    case 404:
      return `${fallback} The requested item was not found.`;
    case 413:
      return "The file or request is larger than this deployment accepts.";
    case 429:
      return "Too many requests. Wait a moment and retry.";
    case 502:
    case 503:
    case 504:
      return "The ProcessTwin API is temporarily unavailable. Retry shortly; if it persists, check the service status.";
    default:
      return `${fallback} (HTTP ${response.status})`;
  }
}

export function describeFailure(caught: unknown, timeoutMs: number): string {
  if (caught instanceof DOMException && (caught.name === "TimeoutError" || caught.name === "AbortError")) {
    return `The request timed out after ${Math.round(timeoutMs / 1000)} s. The server may be busy; retry.`;
  }
  if (caught instanceof Error && caught.name === "TimeoutError") {
    return `The request timed out after ${Math.round(timeoutMs / 1000)} s. The server may be busy; retry.`;
  }
  if (!(caught instanceof TypeError)) {
    return "The sign-in request could not be completed. Please retry.";
  }
  let host = API_URL;
  try {
    host = new URL(API_URL).host;
  } catch {
    // keep the raw value
  }
  return `Cannot reach the ProcessTwin API (${host}). Check your network connection; if it persists the service may be down or the deployment's API URL / CORS origin is misconfigured.`;
}

export function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof Error && caught.message ? caught.message : fallback;
}

/** Only same-origin relative paths are valid post-login destinations (blocks open redirects). */
export function safeRedirectTarget(candidate: string | null | undefined, fallback = "/dashboard"): string {
  if (!candidate) return fallback;
  if (!candidate.startsWith("/") || candidate.startsWith("//") || candidate.startsWith("/\\")) return fallback;
  if (/[\r\n\\]/.test(candidate)) return fallback;
  if (candidate === "/login" || candidate.startsWith("/login?") || candidate.startsWith("/login/")) return fallback;
  return candidate;
}
