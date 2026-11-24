// Tiny typed fetch wrapper. Non-2xx responses throw an ApiError carrying the
// server's `detail` — a plain string (e.g. the friendly 503 when Azure is
// unreachable) or a structured object like the duplicate-iCIMS 409's
// {message, field, req_id, title} — so pages can surface it directly.

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Dev user switching (until SSO): when the sidebar switcher saved an identity,
// every request carries it as X-Dev-User / X-Dev-Role. The server honors these
// only when no Easy-Auth principal is present, so hosted behavior is unchanged.
const DEV_IDENTITY_KEY = "dev-identity";

export interface DevIdentity {
  user?: string;
  roles?: string[];
}

export function readDevIdentity(): DevIdentity | null {
  try {
    const raw = localStorage.getItem(DEV_IDENTITY_KEY);
    return raw ? (JSON.parse(raw) as DevIdentity) : null;
  } catch {
    return null;
  }
}

export function writeDevIdentity(identity: DevIdentity | null): void {
  try {
    if (identity === null) localStorage.removeItem(DEV_IDENTITY_KEY);
    else localStorage.setItem(DEV_IDENTITY_KEY, JSON.stringify(identity));
  } catch {
    /* storage unavailable — switching just won't persist */
  }
}

function devHeaders(): Record<string, string> {
  const identity = readDevIdentity();
  if (!identity) return {};
  const headers: Record<string, string> = {};
  if (identity.user) headers["X-Dev-User"] = identity.user;
  if (identity.roles?.length) headers["X-Dev-Role"] = identity.roles.join(",");
  return headers;
}

// Sign-in redirect for the ONE case the server cannot handle itself: on the Vite dev
// server (:5173) Vite serves index.html for "/" without consulting FastAPI, so the
// document request is never redirected and the SPA boots unauthenticated. Its first
// /api call comes back 401 with X-Auth-Login, and that header — never a bare 401 — is
// what we act on, so every other 401 in the app keeps its meaning. Guarded against a
// redirect loop when several requests fail at once.
let redirectingToLogin = false;

function maybeRedirectToLogin(res: Response): boolean {
  if (res.status !== 401) return false;
  const login = res.headers.get("X-Auth-Login");
  if (!login || redirectingToLogin || typeof window === "undefined") return false;
  redirectingToLogin = true;
  const back = window.location.pathname + window.location.search;
  window.location.assign(`${login}?post_login_redirect_uri=${encodeURIComponent(back)}`);
  return true;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    if (maybeRedirectToLogin(res)) {
      // Navigation is underway; never resolve, so callers don't flash an error first.
      return new Promise<T>(() => {});
    }
    let message = `${res.status} ${res.statusText}`;
    let detail: unknown = null;
    try {
      const body = await res.json();
      detail = body?.detail ?? null;
      if (typeof detail === "string") message = detail;
      else if (detail && typeof (detail as { message?: unknown }).message === "string") {
        message = (detail as { message: string }).message;
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, res.status, detail);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export function get<T>(path: string): Promise<T> {
  return fetch(path, { headers: devHeaders() }).then((r) => handle<T>(r));
}

// path -> (validator, the value it stood for). Only paths the server gives an ETag
// to ever land here, which is the handful a page polls while it sits open.
const _validators = new Map<string, { tag: string; value: unknown }>();

/** GET a path the caller polls, asking the server to skip the body if the answer has
 *  not moved.
 *
 *  The server has already done the work by the time it answers, and after the
 *  2026-09-10 pass that work is a few milliseconds — so what this saves is the
 *  payload, and the client parsing and re-rendering something identical to what it
 *  already holds. Falls back to an ordinary GET on anything unexpected, so a server
 *  that does not send validators behaves exactly as before. */
export async function getPolled<T>(path: string): Promise<T> {
  const known = _validators.get(path);
  const res = await fetch(path, {
    headers: { ...devHeaders(), ...(known ? { "If-None-Match": known.tag } : {}) },
  });
  if (res.status === 304 && known) return known.value as T;
  const value = await handle<T>(res);
  const tag = res.headers.get("ETag");
  if (tag) _validators.set(path, { tag, value });
  else _validators.delete(path);
  return value;
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...devHeaders() },
    body: JSON.stringify(body ?? {}),
  }).then((r) => handle<T>(r));
}

export function put<T>(path: string, body?: unknown): Promise<T> {
  return fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...devHeaders() },
    body: JSON.stringify(body ?? {}),
  }).then((r) => handle<T>(r));
}

export function del<T>(path: string, body?: unknown): Promise<T> {
  return fetch(path, {
    method: "DELETE",
    ...(body === undefined
      ? { headers: devHeaders() }
      : {
          headers: { "Content-Type": "application/json", ...devHeaders() },
          body: JSON.stringify(body),
        }),
  }).then((r) => handle<T>(r));
}

// Fetch a file (CSV, ZIP, …) as a blob and hand it to the browser's download
// flow. Filename comes from the server's Content-Disposition when present, else
// `fallbackName`. Errors surface as ApiError, like the JSON helpers.
export async function downloadFile(
  path: string,
  opts?: { method?: string; body?: unknown; fallbackName?: string },
): Promise<void> {
  const method = opts?.method ?? "GET";
  const init: RequestInit = { method, headers: { ...devHeaders() } };
  if (opts?.body !== undefined) {
    init.headers = { "Content-Type": "application/json", ...devHeaders() };
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let detail: unknown = null;
    try {
      const body = await res.json();
      detail = body?.detail ?? null;
      if (typeof detail === "string") message = detail;
      else if (detail && typeof (detail as { message?: unknown }).message === "string") {
        message = (detail as { message: string }).message;
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, res.status, detail);
  }
  const cd = res.headers.get("Content-Disposition") || "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  const filename = (match ? decodeURIComponent(match[1]) : "") || opts?.fallbackName || "download";
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function uploadFiles<T>(path: string, files: FileList | File[]): Promise<T> {
  const form = new FormData();
  Array.from(files).forEach((f) => form.append("files", f));
  return fetch(path, { method: "POST", headers: devHeaders(), body: form }).then((r) =>
    handle<T>(r),
  );
}

/** Upload budget per request. An iCIMS Bulk Print packet is ~12 MB / 100 pages and
 *  costs ~15s to split on the hosted single-core plan, so a batch posted as ONE
 *  request blows the ~230s gateway timeout and returns 504 with the work half done —
 *  which is exactly what a 70 MB / 6-packet upload did. Bytes are the proxy we have
 *  for pages, which is what actually costs; the file cap stops a few hundred tiny
 *  résumés from arriving as one giant multipart body. */
const UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;
const UPLOAD_CHUNK_FILES = 20;

/** Split a file list into upload-sized groups. A single file larger than the budget
 *  still goes on its own — we can't subdivide one PDF, and the server splits it
 *  incrementally anyway. */
export function chunkForUpload(files: FileList | File[]): File[][] {
  const chunks: File[][] = [];
  let current: File[] = [];
  let bytes = 0;
  for (const f of Array.from(files)) {
    if (current.length && (bytes + f.size > UPLOAD_CHUNK_BYTES || current.length >= UPLOAD_CHUNK_FILES)) {
      chunks.push(current);
      current = [];
      bytes = 0;
    }
    current.push(f);
    bytes += f.size;
  }
  if (current.length) chunks.push(current);
  return chunks;
}

/**
 * Upload in bounded groups, sequentially, reporting progress.
 *
 * SEQUENTIAL on purpose: the batch folder is shared mutable state and the server
 * de-dups against what is already there, so two concurrent uploads to one requisition
 * would race on the same dedupe set. It is also kinder to a single-core host.
 *
 * Resolves with the LAST response, which carries the authoritative batch state. If a
 * chunk fails, the error names how many groups had already landed — the ones already
 * uploaded are safely on the server (the endpoint is incremental), so a retry adds only
 * what is missing rather than starting over.
 */
export async function uploadFilesInChunks<T>(
  /** Query parameters belong in `path` (the batch route reads ?auto_screen=1 there),
   *  so every chunk carries the same intent without a separate argument. */
  path: string,
  files: FileList | File[],
  onProgress?: (done: number, total: number) => void,
): Promise<T> {
  const chunks = chunkForUpload(files);
  let last: T | undefined;
  for (let i = 0; i < chunks.length; i++) {
    onProgress?.(i, chunks.length);
    try {
      last = await uploadFiles<T>(path, chunks[i]);
    } catch (e) {
      const done = i;
      const msg = e instanceof Error ? e.message : String(e);
      throw new Error(
        done > 0
          ? `${msg} — ${done} of ${chunks.length} upload groups had already been added; ` +
            `re-drop the same files and only the missing ones will be added.`
          : msg,
      );
    }
  }
  onProgress?.(chunks.length, chunks.length);
  return last as T;
}
