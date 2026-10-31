import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { EmptyState, Spinner } from "./components/ui";
import { ApiKeysPage } from "./pages/ApiKeysPage";
import { AuthPage } from "./pages/AuthPage";
import { DatasetPage } from "./pages/DatasetPage";
import { DatasetsPage } from "./pages/DatasetsPage";
import { DiagnosesPage, DiagnosisPage } from "./pages/DiagnosisPages";
import { ExperimentPage, ExperimentsPage, NewExperimentPage } from "./pages/ExperimentPages";
import { EvaluationsPage, RunPage } from "./pages/EvaluationPages";
import { ProjectsPage } from "./pages/ProjectsPage";
import { TraceDetailPage } from "./pages/TraceDetailPage";
import { TracesPage } from "./pages/TracesPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner label="Checking session" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<ProjectsPage />} />
        <Route path="projects/:projectId" element={<Navigate to="traces" replace />} />
        <Route path="projects/:projectId/traces" element={<TracesPage />} />
        <Route path="projects/:projectId/traces/:traceId" element={<TraceDetailPage />} />
        <Route path="projects/:projectId/datasets" element={<DatasetsPage />} />
        <Route path="projects/:projectId/datasets/:datasetId" element={<DatasetPage />} />
        <Route path="projects/:projectId/evaluations" element={<EvaluationsPage />} />
        <Route path="projects/:projectId/evaluations/:runId" element={<RunPage />} />
        <Route path="projects/:projectId/diagnoses" element={<DiagnosesPage />} />
        <Route path="projects/:projectId/diagnoses/:diagnosisId" element={<DiagnosisPage />} />
        <Route path="projects/:projectId/experiments" element={<ExperimentsPage />} />
        <Route path="projects/:projectId/experiments/new" element={<NewExperimentPage />} />
        <Route path="projects/:projectId/experiments/:experimentId" element={<ExperimentPage />} />
        <Route path="projects/:projectId/keys" element={<ApiKeysPage />} />
        <Route path="*" element={<EmptyState title="Page not found" />} />
      </Route>
    </Routes>
  );
}
