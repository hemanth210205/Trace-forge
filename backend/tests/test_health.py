import json

from tests.conftest import ApiClient


def test_health_reports_database_ok(client: ApiClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "version": "0.1.0"}
    assert response.headers["x-request-id"]


def test_openapi_documents_every_m2_route_and_security_scheme(client: ApiClient) -> None:
    spec = client.get("/api/openapi.json").json()
    assert spec["info"]["title"] == "Traceforge API"
    assert set(spec["paths"]) == {
        "/api/v1/health",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/providers",
        "/api/v1/auth/oauth/{provider}/start",
        "/api/v1/auth/oauth/{provider}/callback",
        "/api/v1/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/api-keys",
        "/api/v1/projects/{project_id}/api-keys/{key_id}",
        "/api/v1/api-keys/self",
        "/api/v1/ingest/batch",
        "/api/v1/projects/{project_id}/traces",
        "/api/v1/projects/{project_id}/traces/{trace_id}",
        "/api/v1/projects/{project_id}/datasets",
        "/api/v1/projects/{project_id}/datasets/{dataset_id}",
        "/api/v1/projects/{project_id}/datasets/{dataset_id}/cases",
        "/api/v1/projects/{project_id}/datasets/{dataset_id}/cases/{case_id}",
        "/api/v1/projects/{project_id}/datasets/{dataset_id}/import",
        "/api/v1/projects/{project_id}/datasets/{dataset_id}/export",
        "/api/v1/projects/{project_id}/datasets/{dataset_id}/versions",
        "/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version}",
        "/api/v1/projects/{project_id}/eval-runs",
        "/api/v1/projects/{project_id}/eval-runs/{run_id}",
        "/api/v1/projects/{project_id}/eval-runs/{run_id}/results",
        "/api/v1/projects/{project_id}/eval-runs/{run_id}/finish",
        "/api/v1/projects/{project_id}/eval-runs/{run_id}/abort",
        "/api/v1/projects/{project_id}/eval-runs/{run_id}/rerun",
        "/api/v1/sdk/eval-runs",
        "/api/v1/sdk/eval-runs/{run_id}",
        "/api/v1/sdk/eval-runs/{run_id}/results",
        "/api/v1/sdk/eval-runs/{run_id}/finish",
        "/api/v1/projects/{project_id}/eval-runs/{run_id}/diagnoses",
        "/api/v1/projects/{project_id}/diagnoses",
        "/api/v1/projects/{project_id}/diagnoses/{diagnosis_id}",
        "/api/v1/projects/{project_id}/diagnoses/{diagnosis_id}/retry",
        "/api/v1/projects/{project_id}/diagnoses/{diagnosis_id}/hypotheses/{hypothesis_id}",
        "/api/v1/projects/{project_id}/experiments",
        "/api/v1/projects/{project_id}/experiments/{experiment_id}",
        "/api/v1/projects/{project_id}/experiments/{experiment_id}/cases",
        "/api/v1/projects/{project_id}/experiments/{experiment_id}/complete",
        "/api/v1/projects/{project_id}/experiments/{experiment_id}/abort",
        "/api/v1/projects/{project_id}/experiments/{experiment_id}/decisions",
        "/api/v1/projects/{project_id}/experiments/{experiment_id}/validate-hypothesis",
        "/api/v1/sdk/experiments/{experiment_id}",
        "/api/v1/sdk/experiments/{experiment_id}/start",
        "/api/v1/sdk/experiments/{experiment_id}/complete",
    }
    assert set(spec["components"]["securitySchemes"]) == {"SessionCookie", "CsrfToken", "ProjectApiKey"}
    # Stored credential fields must never appear in any response schema.
    text = json.dumps(spec["components"]["schemas"])
    for field in ("password_hash", "token_hash", "key_hash"):
        assert field not in text


def test_docs_page_is_served(client: ApiClient) -> None:
    assert client.get("/api/docs").status_code == 200


def test_unknown_route_uses_error_envelope(client: ApiClient) -> None:
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
