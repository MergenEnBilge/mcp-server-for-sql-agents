import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { ApiProvider } from "./api/client";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { Shell } from "./components/Shell";
import { ErrorNote } from "./components/ui";
import { AgentsPage } from "./features/agents/AgentsPage";
import { AuditPage } from "./features/audit/AuditPage";
import { ConnectionsPage } from "./features/connections/ConnectionsPage";
import { HealthPage } from "./features/health/HealthPage";
import { PermissionsPage } from "./features/permissions/PermissionsPage";
import { ReportsPage } from "./features/reports/ReportsPage";
import { SchemaPage } from "./features/schema/SchemaPage";

/** The routes, for an already signed-in person. Tests render this with their own providers. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/audit" replace />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="permissions" element={<PermissionsPage />} />
        <Route path="connections" element={<ConnectionsPage />} />
        <Route path="schema" element={<SchemaPage />} />
        <Route path="health" element={<HealthPage />} />
        <Route path="reports" element={<ReportsPage />} />
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
