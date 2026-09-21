import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { ApiProvider } from "./api/client";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { Shell } from "./components/Shell";
import { ErrorNote } from "./components/ui";
import { AuditPage } from "./features/audit/AuditPage";
import { PermissionsPage } from "./features/permissions/PermissionsPage";

/** The routes, for an already signed-in person. Tests render this with their own providers. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/audit" replace />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="permissions" element={<PermissionsPage />} />
        <Route path="*" element={<Navigate to="/audit" replace />} />
      </Route>
    </Routes>
  );
}

function Gate() {
  const auth = useAuth();
  if (auth.status === "error") {
    return (
      <main className="main">
        <ErrorNote>
          Sign-in failed: {auth.error} <a href="/">Try again</a>
        </ErrorNote>
      </main>
    );
  }
  if (auth.status === "loading") {
    return (
      <main className="main">
        <p className="muted" role="status">
          Signing you in…
        </p>
      </main>
    );
  }
  return (
    <ApiProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </ApiProvider>
  );
}

export function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
