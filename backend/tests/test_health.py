import asyncio
from collections.abc import Iterator
from secrets import token_urlsafe
from unittest.mock import AsyncMock

import pytest
from asyncpg.exceptions import InvalidPasswordError
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.dependencies import get_services
from app.core.config import Settings
from app.infrastructure.services import probe
from app.main import create_app

ORIGIN = "http://example.test"


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(
        {
            "postgres_user": "test",
            "postgres_db": "test",
            "postgres_password": token_urlsafe(24),
            "postgres_host": "127.0.0.1",
            "postgres_port": 5432,
            "redis_host": "127.0.0.1",
            "redis_port": 6379,
            "cors_origins": [ORIGIN],
        }
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


def test_health_does_not_check_dependencies(client: TestClient) -> None:
    services = AsyncMock()
    client.app.dependency_overrides[get_services] = lambda: services
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    services.readiness.assert_not_awaited()


@pytest.mark.parametrize(
    ("postgres", "redis", "code", "state"),
    [
        (True, True, 200, "ready"),
        (False, True, 503, "not_ready"),
        (True, False, 503, "not_ready"),
        (False, False, 503, "not_ready"),
    ],
)
def test_readiness(client: TestClient, postgres: bool, redis: bool, code: int, state: str) -> None:
    services = AsyncMock()
    services.readiness.return_value = (postgres, redis)
    client.app.dependency_overrides[get_services] = lambda: services
    response = client.get("/ready")
    assert response.status_code == code
    assert response.json() == {"status": state, "checks": {"postgres": postgres, "redis": redis}}


def test_cors_allows_only_configured_origins(client: TestClient) -> None:
    allowed = client.get("/health", headers={"Origin": ORIGIN})
    denied = client.get("/health", headers={"Origin": "http://unlisted.test"})
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-origin" not in denied.headers


def test_bearer_preflight_requires_configured_origin(client: TestClient) -> None:
    headers = {
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }
    allowed = client.options("/api/v1/auth/me", headers=headers)
    assert allowed.status_code == 200
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
    blocked = client.options(
        "/api/v1/auth/me", headers={**headers, "Origin": "http://unlisted.test"}
    )
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/ingestion/files?filename=report.txt"),
        ("POST", "/ingestion/remote"),
        ("GET", "/ingestion/jobs"),
        ("GET", "/ingestion/jobs/00000000-0000-0000-0000-000000000001"),
        ("POST", "/ingestion/jobs/00000000-0000-0000-0000-000000000001/retry"),
        ("GET", "/documents"),
        ("GET", "/documents/00000000-0000-0000-0000-000000000001"),
        (
            "GET",
            "/documents/00000000-0000-0000-0000-000000000001"
            "/versions/00000000-0000-0000-0000-000000000002",
        ),
    ],
)
def test_knowledge_routes_require_session(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, "/api/v1" + path, json={}).status_code == 401


def test_settings_reject_blank_database_password(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), "postgres_password": ""})


@pytest.mark.parametrize("error_type", [RedisConnectionError, InvalidPasswordError])
def test_probe_handles_dependency_failure(error_type: type[Exception]) -> None:
    check = AsyncMock(side_effect=error_type)
    assert asyncio.run(probe("dependency", check)) is False
