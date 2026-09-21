import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

import { ApiTestProvider, type Api } from "../api/client";

type Handler = (params?: Record<string, unknown>, body?: unknown) => unknown;

/**
 * A stand-in for the admin API. Routes are "METHOD /path"; anything not listed fails the test
 * loudly, so a screen can't quietly call an endpoint the test didn't expect.
 */
export function fakeApi(routes: Record<string, Handler | unknown>) {
  const calls: { method: string; path: string; params?: Record<string, unknown>; body?: unknown }[] = [];
  const answer = async (method: string, path: string, params?: Record<string, unknown>, body?: unknown) => {
    calls.push({ method, path, params, body });
    const route = routes[`${method} ${path}`];
    if (route === undefined) throw new Error(`Unexpected request: ${method} ${path}`);
    return typeof route === "function" ? (route as Handler)(params, body) : route;
  };
  const api: Api = {
    get: (path, params) => answer("GET", path, params) as Promise<never>,
    post: (path, body) => answer("POST", path, undefined, body) as Promise<never>,
    put: (path, body) => answer("PUT", path, undefined, body) as Promise<never>,
    del: async (path) => void (await answer("DELETE", path)),
  };
  return { api, calls };
}

export function renderScreen(ui: ReactElement, api: Api, url = "/") {
  return render(
    <ApiTestProvider value={api}>
      <MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter>
    </ApiTestProvider>,
  );
}
