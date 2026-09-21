export interface AppConfig {
  /** Issuer URL of the identity provider (Keycloak realm). */
  oidcAuthority: string;
  /** Public client id registered for this app. */
  oidcClientId: string;
  /** Where the admin API lives. Relative, because the UI and API share an origin. */
  apiBase: string;
}

declare global {
  interface Window {
    __APP_CONFIG__?: Partial<AppConfig>;
  }
}

const defaults: AppConfig = {
  oidcAuthority: import.meta.env.VITE_OIDC_AUTHORITY ?? "http://localhost:8080/auth/realms/sql-data-layer",
  oidcClientId: import.meta.env.VITE_OIDC_CLIENT_ID ?? "gui",
  apiBase: import.meta.env.VITE_API_BASE ?? "/api",
};

export function loadConfig(): AppConfig {
  return { ...defaults, ...(window.__APP_CONFIG__ ?? {}) };
}
