from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.api.dependencies import get_services
from app.infrastructure.services import Services

router = APIRouter(tags=["system"])


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class ServiceChecks(BaseModel):
    postgres: bool
    redis: bool


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: ServiceChecks


@router.get("/health")
async def health() -> Health:
    return Health()


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
async def ready(
    response: Response,
    services: Annotated[Services, Depends(get_services)],
) -> Readiness:
    postgres, redis = await services.readiness()
    ready = postgres and redis
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(
        status="ready" if ready else "not_ready",
        checks=ServiceChecks(postgres=postgres, redis=redis),
    )
