/**
 * Sign-in with the identity provider: OpenID Connect authorization code flow with PKCE.
 *
 * The browser never sees a client secret (this is a public client). The access token is kept in
 * session storage, so closing the tab signs the person out, and it is attached to every API call by
 * `getToken`. The API checks the token itself; nothing here is trusted by the server.
 */
import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { loadConfig } from "../config";

export interface AuthState {
  status: "loading" | "signedIn" | "error";
  /** Display name, or undefined until signed in. */
  name?: string;
  sub?: string;
  error?: string;
  /** A current access token, renewing it first if it is about to expire. */
  getToken: () => Promise<string>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside an AuthProvider");
  return auth;
}

/** Lets tests (and nothing else) supply a ready-made auth state. */
export const AuthStateProvider = AuthContext.Provider;

function createManager(): UserManager {
  const config = loadConfig();
  return new UserManager({
    authority: config.oidcAuthority,
    client_id: config.oidcClientId,
    redirect_uri: `${window.location.origin}/`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    response_type: "code",
    scope: "openid profile",
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: true,
  });
}

/** The callback is processed once even if React mounts the provider twice (StrictMode). */
let callbackInFlight: Promise<User | undefined> | null = null;

export function AuthProvider({ children }: { children: ReactNode }) {
  const manager = useMemo(createManager, []);
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | undefined>();
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    async function start() {
      try {
        const params = new URLSearchParams(window.location.search);
        if (params.has("error") && params.has("state")) {
          // The identity provider refused the request. Say so and stop: redirecting again would
          // only bounce between the two forever.
          throw new Error(params.get("error_description") ?? params.get("error") ?? "The sign-in was refused.");
        }
        if (params.has("code") && params.has("state")) {
          callbackInFlight ??= manager.signinCallback();
          const signedIn = await callbackInFlight;
          callbackInFlight = null;
          // Drop the one-time code from the address bar and return to where they were headed.
          const target = (signedIn?.state as { path?: string } | undefined)?.path ?? "/";
          window.history.replaceState({}, "", target);
          if (signedIn) return setUser(signedIn);
        }
        const existing = await manager.getUser();
        if (existing && !existing.expired) return setUser(existing);
        await manager.signinRedirect({ state: { path: window.location.pathname + window.location.search } });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Sign-in failed.");
      }
    }
    void start();
  }, [manager]);

  useEffect(() => {
    const renewed = (u: User) => setUser(u);
    manager.events.addUserLoaded(renewed);
    return () => manager.events.removeUserLoaded(renewed);
  }, [manager]);

  const getToken = useCallback(async () => {
    let current = await manager.getUser();
    if (!current || current.expired) {
      try {
        current = await manager.signinSilent();
      } catch {
        current = null;
      }
    }
    if (!current) {
      await manager.signinRedirect({ state: { path: window.location.pathname + window.location.search } });
      throw new Error("Signing in again.");
    }
    return current.access_token;
  }, [manager]);

  const value = useMemo<AuthState>(
    () => ({
      status: error ? "error" : user ? "signedIn" : "loading",
      name: (user?.profile.name as string | undefined) ?? (user?.profile.preferred_username as string | undefined),
      sub: user?.profile.sub,
      error,
      getToken,
      signOut: () => void manager.signoutRedirect(),
    }),
    [user, error, getToken, manager],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
