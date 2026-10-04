"""Route-by-route authorization matrix.

This is the executable authorization contract for the API: every route must appear
here with its expected protection level. Adding a route without classifying it fails
this suite, which forces a deliberate authorization decision for every new endpoint.

Protection levels:
- ``public``   no authentication (system probes and token endpoints only)
- ``user``     authenticated user, no organization scoping (self-service only)
- ``tenant``   authenticated + X-Organization-ID membership check, no role restriction
- ``roles``    tenant + require_roles(...) restriction (the role set is asserted too)
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from fastapi.testclient import TestClient

# --- The contract -----------------------------------------------------------

PUBLIC = {
    ("GET", "/live"),
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/metrics"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}

USER_ONLY = {
    ("GET", "/api/v1/auth/me"),
}

TENANT = {
    ("GET", "/api/v1/plants"),
    ("GET", "/api/v1/plants/{plant_id}"),
    ("GET", "/api/v1/sensors"),
    ("GET", "/api/v1/sensors/{sensor_id}/readings"),
    ("GET", "/api/v1/simulations/{simulation_id}"),
    ("GET", "/api/v1/recommendations"),
    ("GET", "/api/v1/alerts"),
    ("GET", "/api/v1/models"),
    ("GET", "/api/v1/audit-log"),
    ("GET", "/api/v1/dashboard/summary"),
    ("GET", "/api/v1/events/dashboard"),
    ("GET", "/api/v1/datasets"),
    ("GET", "/api/v1/datasets/hierarchy"),
    ("GET", "/api/v1/datasets/import-jobs/{job_id}"),
    ("GET", "/api/v1/datasets/{version_id}/variables"),
    ("GET", "/api/v1/datasets/{version_id}/exploration"),
    ("GET", "/api/v1/data-sources"),
    ("GET", "/api/v1/data-sources/{source_id}"),
    ("GET", "/api/v1/data-sources/{source_id}/readings"),
    ("GET", "/api/v1/calibrations"),
    ("GET", "/api/v1/models/{model_id}/evaluations"),
    ("GET", "/api/v1/models/{model_id}/drift-events"),
    ("GET", "/api/v1/physics/parameter-catalog"),
    ("GET", "/api/v1/physics/parameter-sets"),
    ("GET", "/api/v1/digital-twins/{equipment_id}/latest"),
    ("GET", "/api/v1/anomalies"),
    ("GET", "/api/v1/recommendations/{recommendation_id}/reviews"),
}

ROLE_GATES = {
    ("POST", "/api/v1/ingestion/readings"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/ingestion/csv"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/simulations"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/optimization/runs"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/models/train"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/models/hybrid/train"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/models/{model_id}/evaluate"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/models/{model_id}/drift"): {"OWNER", "ADMIN", "ENGINEER"},
    # Model lifecycle transitions are deliberately narrower: governance sign-off
    # is OWNER/ADMIN only, ENGINEER cannot move a model toward production.
    ("POST", "/api/v1/models/{model_id}/validate"): {"OWNER", "ADMIN"},
    ("POST", "/api/v1/models/{model_id}/stage"): {"OWNER", "ADMIN"},
    ("POST", "/api/v1/models/{model_id}/promote"): {"OWNER", "ADMIN"},
    ("POST", "/api/v1/datasets/import"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/datasets/import-chunked"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/datasets/import-jobs/{job_id}/process-chunk/{chunk_index}"): {
        "OWNER",
        "ADMIN",
        "ENGINEER",
    },
    ("POST", "/api/v1/physics/parameter-sets"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/calibrations"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/data-sources"): {"OWNER", "ADMIN", "ENGINEER"},
    ("POST", "/api/v1/recommendations/{recommendation_id}/review"): {
        "OWNER",
        "ADMIN",
        "ENGINEER",
    },
}

# GET /api/v1/recommendations is intentionally registered by two routers
# (legacy dashboard and workflow API); both must stay tenant-scoped.
DUPLICATE_TENANT_ROUTES = {("GET", "/api/v1/recommendations")}


# --- Route introspection ----------------------------------------------------


def _walk_routes(routes: Any) -> Any:
    """Yield APIRoute objects across nested included routers (FastAPI >=0.141)."""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        else:
            sub = getattr(route, "original_router", None) or getattr(route, "router", None)
            if sub is not None:
                yield from _walk_routes(sub.routes)


def _walk_deps(dependant: Any) -> Any:
    """Yield (qualname, callable) for a dependency and all of its sub-dependencies."""
    call = getattr(dependant, "call", None)
    yield getattr(call, "__qualname__", ""), call
    for sub in dependant.dependencies:
        yield from _walk_deps(sub)


def _roles_of(call: Any) -> set[str]:
    """Extract the allowed role set captured by a require_roles() closure."""
    if call is None or "require_roles" not in getattr(call, "__qualname__", ""):
        return set()
    for cell in call.__closure__ or ():
        value = cell.cell_contents
        if isinstance(value, tuple) and value and all(isinstance(v, str) for v in value):
            return {getattr(v, "value", v) for v in value}
    return set()


def _analyze(route: Any) -> tuple[bool, bool, bool, set[str]]:
    """Return (has_user_auth, has_tenant, has_role_gate, roles)."""
    has_user = False
    has_tenant = False
    has_roles = False
    roles: set[str] = set()
    for dependency in route.dependant.dependencies:
        for name, call in _walk_deps(dependency):
            if name == "current_user":
                has_user = True
            elif name == "tenant_context":
                has_tenant = True
            elif "require_roles" in name:
                has_roles = True
                roles |= _roles_of(call)
    return has_user, has_tenant, has_roles, roles


@pytest.fixture(scope="module")
def route_matrix() -> dict[tuple[str, str], tuple[bool, bool, bool, set[str]]]:
    from apps.api.processtwin_api.main import app

    matrix: dict[tuple[str, str], tuple[bool, bool, bool, set[str]]] = {}
    for route in _walk_routes(app.routes):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            key = (method, route.path)
            if key in matrix:
                # Duplicate registration: both must carry identical protection.
                assert matrix[key] == _analyze(route), f"duplicate route differs: {key}"
                continue
            matrix[key] = _analyze(route)
    return matrix


# --- Assertions -------------------------------------------------------------


def test_route_inventory_is_fully_classified(route_matrix: Any) -> None:
    """Every route is in exactly one contract bucket; no unknown routes."""
    # Duplicate registrations are deduplicated by the fixture after their
    # protection levels are asserted identical, so keys are unique here.
    expected = Counter(PUBLIC | USER_ONLY | TENANT | set(ROLE_GATES) | DUPLICATE_TENANT_ROUTES)
    actual = Counter(route_matrix.keys())
    assert actual == expected, (
        f"unclassified or removed routes -> new: {sorted(actual - expected)}, "
        f"missing: {sorted(expected - actual)}"
    )


def test_public_routes_have_no_auth_dependencies(route_matrix: Any) -> None:
    for key in PUBLIC:
        has_user, has_tenant, has_roles, roles = route_matrix[key]
        assert not (has_user or has_tenant or has_roles or roles), f"{key} must be public"


def test_user_only_route_is_scoped_to_self(route_matrix: Any) -> None:
    for key in USER_ONLY:
        has_user, has_tenant, has_roles, roles = route_matrix[key]
        assert has_user, f"{key} must require an authenticated user"
        assert not has_tenant, f"{key} must not accept an organization context"
        assert not has_roles, f"{key} must not carry a role gate"


def test_tenant_routes_require_membership_context(route_matrix: Any) -> None:
    for key in TENANT | DUPLICATE_TENANT_ROUTES:
        has_user, has_tenant, has_roles, roles = route_matrix[key]
        assert has_user and has_tenant, f"{key} must require user + tenant context"
        assert not has_roles and not roles, f"{key} must not carry a role gate"


def test_mutating_routes_are_role_gated(route_matrix: Any) -> None:
    for (method, path), expected_roles in ROLE_GATES.items():
        has_user, has_tenant, has_roles, roles = route_matrix[(method, path)]
        assert method == "POST", f"{path}: role-gated routes are expected to be writes"
        assert has_user and has_tenant, f"{path} must require user + tenant context"
        assert has_roles, f"{path} must be role-gated"
        assert roles == expected_roles, f"{path}: expected {expected_roles}, got {roles}"


def test_every_mutation_is_role_gated_or_public_auth(route_matrix: Any) -> None:
    """No write endpoint may be reachable with tenant access alone."""
    for (method, path), (_, _has_tenant, has_roles, _) in route_matrix.items():
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        if (method, path) in PUBLIC:
            continue
        assert has_roles, f"{method} {path} is a mutation without a role gate"


def test_enforcement_smoke() -> None:
    """Live enforcement: protected routes reject anonymous access, public ones do not."""
    from apps.api.processtwin_api.main import app

    with TestClient(app) as client:
        for path in ("/live", "/health", "/ready"):
            assert client.get(path).status_code != 401, path
        protected_samples = (
            ("GET", "/api/v1/plants"),
            ("GET", "/api/v1/models"),
            ("GET", "/api/v1/auth/me"),
            ("POST", "/api/v1/models/train"),
            ("POST", "/api/v1/ingestion/readings"),
            ("POST", "/api/v1/models/{model_id}/promote".replace("{model_id}", "00000000-0000-0000-0000-000000000000")),
        )
        for method, path in protected_samples:
            response = client.request(method, path)
            assert response.status_code == 401, f"{method} {path} -> {response.status_code}"
