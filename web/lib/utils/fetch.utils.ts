// web/lib/utils/fetch.utils.ts
//
// PURPOSE:
//   The one HTTP core for the whole web app. Every API call — and every SWR
//   fetcher — goes through request() (or its get/post/put/del/patch helpers).
//   It serializes JSON bodies, sets JSON headers, parses responses, and turns
//   any non-2xx status into a typed ApiError so SWR's `error` is always usable.
//
// AUTH:
//   Built on top of authFetch (web/lib/utils/auth-fetch.utils.ts), which injects the
//   Keycloak Bearer token and handles proactive refresh / 401 redirects.
//   Callers can still pass extra headers/options per request.
//
// NON-JSON RESPONSES:
//   Pass `responseFormat: "blob"` (or "text") for endpoints that don't return
//   JSON — e.g. the certificate PDF download. Otherwise the format is inferred
//   from the response Content-Type, defaulting to JSON.

import { authFetch } from "@/lib/utils/auth-fetch.utils";

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export type ResponseFormat = "json" | "blob" | "text";

/** Per-request options layered on top of the standard fetch init. */
export interface RequestOptions extends Omit<RequestInit, "method" | "body"> {
  /** How to read the response body. Inferred from Content-Type when omitted. */
  responseFormat?: ResponseFormat;
}

/**
 * Thrown on any non-2xx response. Carries the HTTP status and the parsed error
 * body (JSON object, plain text, or null) so callers/SWR can branch on either.
 */
export class ApiError<TBody = unknown> extends Error {
  readonly status: number;
  readonly body: TBody;

  constructor(status: number, body: TBody, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** Best-effort message extraction from a parsed error body (FastAPI: `detail`). */
function messageFromBody(body: unknown, status: number): string {
  if (typeof body === "string" && body.trim()) return body;
  if (body && typeof body === "object") {
    const detail = (body as { detail?: unknown; message?: unknown }).detail ??
      (body as { message?: unknown }).message;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return `Request failed (HTTP ${status})`;
}

/** Reads a response body in the requested (or inferred) format. */
async function readBody(res: Response, format?: ResponseFormat): Promise<unknown> {
  // 204 No Content / empty body — nothing to parse.
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined;
  }

  const resolved: ResponseFormat =
    format ??
    (res.headers.get("content-type")?.includes("application/json") ? "json" : "text");

  if (resolved === "blob") return res.blob();
  if (resolved === "text") return res.text();

  // JSON — tolerate an empty/invalid body rather than throwing a parse error.
  return res.json().catch(() => undefined);
}

/**
 * Generic HTTP core. Generic over the response type <TResponse> and, for
 * methods with a body, the request type <TBody>. No `any` in the signature.
 */
export async function request<TResponse, TBody = undefined>(
  method: HttpMethod,
  url: string,
  body?: TBody,
  options: RequestOptions = {},
): Promise<TResponse> {
  const { responseFormat, headers, ...rest } = options;

  // Raw bodies (multipart FormData, form-encoded URLSearchParams, strings,
  // Blobs) are sent as-is and let the browser/fetch pick the Content-Type;
  // everything else is serialized as JSON.
  const isRawBody =
    (typeof FormData !== "undefined" && body instanceof FormData) ||
    (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) ||
    (typeof Blob !== "undefined" && body instanceof Blob) ||
    typeof body === "string";

  const merged = new Headers(headers);
  // Set JSON Content-Type only for JSON bodies. For raw bodies, let fetch set
  // the correct header (e.g. multipart boundary / x-www-form-urlencoded).
  if (body !== undefined && !isRawBody && !merged.has("Content-Type")) {
    merged.set("Content-Type", "application/json");
  }
  if (!merged.has("Accept")) {
    merged.set("Accept", "application/json");
  }

  const res = await authFetch(url, {
    ...rest,
    method,
    headers: merged,
    body:
      body === undefined
        ? undefined
        : isRawBody
          ? (body as BodyInit)
          : JSON.stringify(body),
  });

  if (!res.ok) {
    const errorBody = await readBody(res).catch(() => undefined);
    throw new ApiError(res.status, errorBody, messageFromBody(errorBody, res.status));
  }

  return (await readBody(res, responseFormat)) as TResponse;
}

// ---------------------------------------------------------------------------
// Thin helpers — these are the fetchers SWR / useSWRMutation consume.
// ---------------------------------------------------------------------------

export const get = <TResponse>(url: string, options?: RequestOptions) =>
  request<TResponse>("GET", url, undefined, options);

export const post = <TResponse, TBody = undefined>(
  url: string,
  body?: TBody,
  options?: RequestOptions,
) => request<TResponse, TBody>("POST", url, body, options);

export const put = <TResponse, TBody = undefined>(
  url: string,
  body?: TBody,
  options?: RequestOptions,
) => request<TResponse, TBody>("PUT", url, body, options);

export const patch = <TResponse, TBody = undefined>(
  url: string,
  body?: TBody,
  options?: RequestOptions,
) => request<TResponse, TBody>("PATCH", url, body, options);

export const del = <TResponse = void>(url: string, options?: RequestOptions) =>
  request<TResponse>("DELETE", url, undefined, options);
