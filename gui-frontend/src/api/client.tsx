/** A small typed client for the admin API. Every call carries the person's access token. */
import { createContext, useContext, useMemo, type ReactNode } from "react";

import { useAuth } from "../auth/AuthProvider";
import { loadConfig } from "../config";

/** An API failure, with a message written for the person looking at the screen. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type Params = Record<string, string | number | boolean | undefined | null>;

export interface Api {
  get<T>(path: string, params?: Params, signal?: AbortSignal): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  put<T>(path: string, body?: unknown): Promise<T>;
  del(path: string): Promise<void>;
}

/** FastAPI reports validation problems as a list; turn either shape into a sentence. */
function messageFrom(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? "invalid"}`)
      .join("; ");
  }
  if (status === 401) return "Your session has ended. Sign in again.";
  if (status === 403) return "This needs an administrator.";
  if (status >= 500) return "The server hit a problem handling that. It has been logged.";
  return `The request failed (${status}).`;
}

export function createApi(getToken: () => Promise<string>, base: string): Api {
  async function request<T>(method: string, path: string, params?: Params, body?: unknown, signal?: AbortSignal): Promise<T> {
    const url = new URL(base + path, window.location.origin);
    for (const [key, value] of Object.entries(params ?? {})) {
      if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
    }
    const send = async () =>
      fetch(url, {
        method,
        signal,
        headers: {
          Authorization: `Bearer ${await getToken()}`,
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });

    let response: Response;
    try {
      response = await send();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
    }
    if (response.status === 401) response = await send(); // once more, with a renewed token
    if (response.status === 204) return undefined as T;
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(response.status, messageFrom(response.status, payload));
    return payload as T;
  }

  return {
    get: (path, params, signal) => request("GET", path, params, undefined, signal),
    post: (path, body) => request("POST", path, undefined, body ?? {}),
    put: (path, body) => request("PUT", path, undefined, body ?? {}),
    del: (path) => request("DELETE", path),
  };
}

const ApiContext = createContext<Api | null>(null);

export function ApiProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  const api = useMemo(() => createApi(getToken, loadConfig().apiBase), [getToken]);
  return <ApiContext.Provider value={api}>{children}</ApiContext.Provider>;
}

/** Lets tests supply a fake API. */
export const ApiTestProvider = ApiContext.Provider;

export function useApi(): Api {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi must be used inside an ApiProvider");
  return api;
}
