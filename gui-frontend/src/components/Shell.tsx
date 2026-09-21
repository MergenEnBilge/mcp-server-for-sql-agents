import { NavLink, Outlet } from "react-router-dom";

import { useApi } from "../api/client";
import type { Me } from "../api/types";
import { useAsync } from "../api/useAsync";
import { useAuth } from "../auth/AuthProvider";
import { ErrorNote, PageHead } from "./ui";

/** The screens, in the order they are used. Each has a route in App.tsx. */
export const NAV = [
  { to: "/audit", label: "Audit log" },
  { to: "/permissions", label: "Permissions" },
] as const;

/**
 * Navigation rail plus the working area. Who is signed in, and in what role, stays visible on
 * every screen, because this is a multi-user tool where actions are attributed to a person.
 */
export function Shell() {
  const auth = useAuth();
  const api = useApi();
  const me = useAsync((signal) => api.get<Me>("/me", undefined, signal), [api]);

  return (
    <div className="app">
      <aside className="rail">
        <div className="rail-brand">
          SQL data layer
          <small>Admin console</small>
        </div>
        <nav className="rail-nav" aria-label="Screens">
          {/* Only offered once we know the person can use them. */}
          {me.data?.is_admin &&
            NAV.map((item) => (
              <NavLink key={item.to} to={item.to}>
                {item.label}
              </NavLink>
            ))}
        </nav>
        <div className="rail-identity">
          <div className="who">{auth.name ?? me.data?.name ?? "Signed in"}</div>
          <div className="role">{me.data ? (me.data.is_admin ? "Administrator" : "Not an administrator") : " "}</div>
          <button type="button" onClick={auth.signOut}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">
        {me.error ? (
          <ErrorNote onRetry={me.reload}>{me.error}</ErrorNote>
        ) : me.data && !me.data.is_admin ? (
          <AccessDenied name={me.data.name} />
        ) : me.data ? (
          <Outlet />
        ) : (
          <p className="muted" role="status">
            Loading…
          </p>
        )}
      </main>
    </div>
  );
}

function AccessDenied({ name }: { name: string | null }) {
  return (
    <>
      <PageHead title="No access" />
      <p>
        {name ? `${name}, you` : "You"} are signed in, but this console is for administrators. Ask an administrator to give you the{" "}
        <code>admin</code> role if you need it.
      </p>
    </>
  );
}
